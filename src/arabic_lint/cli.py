"""arabic-lint - find Arabic text that was corrupted before it was stored.

    arabic-lint path/to/repo
    arabic-lint data.json --json
    arabic-lint . --exclude node_modules --exclude .git

Three checks run over a tree:

  * stored corruption  - Arabic presentation forms that were written to disk
  * bidi controls      - the invisible directional characters that survived into the
                         text: residue a normalizer missed, or a scope nobody closed
  * source risk        - the reshape+bidi recipe feeding a renderer that already
                         shapes, which corrupts at render time (Python files only)

Exit codes:
    0  clean
    1  corrupted Arabic found
    2  usage / IO error

The source check is deliberately quiet. It reports where a shaping renderer is
imported and something in the file draws with it, and, at low confidence, a library
function that returns the pre-shaped string when that same file imports matplotlib
or Pillow. ReportLab, terminal output and dead helpers in scripts stay silent. A
shaping import in some other file is not evidence. Flagging every occurrence of
the recipe would mean thousands of false positives, and a checker nobody trusts
is worse than none.
"""

from __future__ import annotations

import argparse
import codecs
import json
import sys
from pathlib import Path

from .detect import scan_text, SEVERITY_ORDER
from .controls import scan_controls, RISK_ORDER
from .source import (
    scan_source, apply_fixes, HEADLINE, returned_helpers, helpers_imported,
)
from .doctor import report as doctor_report
from . import baseline as bl
from . import notebook as nbmod

TEXT_SUFFIXES = {
    ".txt", ".json", ".jsonl", ".csv", ".tsv", ".md", ".yml", ".yaml",
    ".xml", ".html", ".htm", ".svg", ".po", ".properties", ".strings",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".swift",
    ".php", ".rb", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".sql",
    ".ipynb",
}

DEFAULT_EXCLUDES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

TEXT_BOMS = (
    codecs.BOM_UTF8,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
)


def looks_like_text(path: Path) -> bool:
    """Was a file that would not decode as UTF-8 probably text anyway?

    A PNG is correctly skipped, and saying so for every image in a repository
    would be noise, so the line is drawn at "looks like text but did not
    decode" rather than at "did not decode". A NUL byte in the head means
    binary, unless the head opens with a text BOM: UTF-16 and UTF-32 text is
    full of NUL bytes, and legacy exports are exactly the pipeline this tool
    exists for.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    if head.startswith(TEXT_BOMS):
        return True
    return b"\x00" not in head


def iter_files(root: Path, excludes: set[str]):
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in excludes for part in p.parts):
            continue
        if p.suffix.lower() in TEXT_SUFFIXES:
            yield p


class Unit:
    """One stretch of text to scan, and where it came from.

    A plain file is a single unit covering the whole file. A notebook is many:
    one per cell source, plus one per cell's text outputs. Scanning per unit is
    what lets a finding say *cell 3, line 2* instead of a column offset inside
    one line of JSON.
    """

    __slots__ = ("text", "cell", "origin", "is_code")

    def __init__(self, text: str, cell: int | None = None,
                 origin: str = "", is_code: bool = False):
        self.text = text
        self.cell = cell
        self.origin = origin        # "" | "source" | "output"
        self.is_code = is_code

    def loc(self) -> dict:
        """Extra row keys. Absent for a plain file, so existing output and the
        baseline's row shape are untouched when no notebook is involved."""
        if self.cell is None:
            return {}
        return {"cell": self.cell, "origin": self.origin}


def units_for(path: Path, text: str) -> list[Unit]:
    """Split a file into scannable units. Raises NotebookError for an .ipynb
    that cannot be read, so the caller can report it as skipped rather than
    counting it as scanned and clean."""
    if path.suffix.lower() != ".ipynb":
        return [Unit(text)]
    nb = nbmod.parse(text)
    units: list[Unit] = []
    for c in nb.cells:
        if c.source:
            units.append(Unit(c.source, cell=c.index, origin="source",
                              is_code=(c.cell_type == "code")))
        if c.outputs:
            # Marked as an output because a reader cannot fix it by editing it;
            # it is regenerated from the code above. Never source-scanned: an
            # output is not code, and a traceback full of Python would parse as
            # some of it.
            units.append(Unit(c.outputs, cell=c.index, origin="output"))
    return units


def where(row: dict) -> str:
    """file:line:col, or file:cellN:line:col inside a notebook."""
    if row.get("cell") is None:
        return f"{row['file']}:{row['line']}:{row['col']}"
    tail = " (output)" if row.get("origin") == "output" else ""
    return f"{row['file']}:cell{row['cell']}:{row['line']}:{row['col']}{tail}"


def _scan_sources(pending: list[tuple[Path, "Unit"]], args, covered,
                  source_results: list[dict], fixed_files: list[tuple[str, int]]) -> None:
    """Scan Python and notebook code once every returned helper is known.

    Exports are collected from `.py` files first, then each unit is scanned with
    the imports it actually resolved. A matplotlib import in a file that does not
    call the helper adds nothing.
    """
    exports: dict[Path, dict[str, str]] = {}
    for path, unit in pending:
        if path.suffix.lower() != ".py":
            continue
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key not in exports:
            exports[key] = returned_helpers(unit.text)

    for path, unit in pending:
        try:
            importer = path.resolve()
        except OSError:
            importer = path
        linked = helpers_imported(unit.text, importer, exports)
        sreport = scan_source(unit.text, linked)
        is_nb = path.suffix.lower() == ".ipynb"
        if args.fix and any(f.fix for f in sreport.findings):
            if is_nb:
                # Rewriting a cell means re-dumping the whole notebook
                # JSON, which reformats every untouched cell and buries
                # a one-line fix in a whole-file diff. Refuse and say
                # so, rather than hand someone an unreviewable change.
                print(f"arabic-lint: not fixing {path}: --fix does not "
                      f"rewrite notebooks (it would reformat every cell). "
                      f"Edit cell {unit.cell} by hand.", file=sys.stderr)
            else:
                new_text, n = apply_fixes(unit.text, sreport.findings)
                try:
                    compile(new_text, str(path), "exec")
                except SyntaxError as exc:
                    print(f"arabic-lint: refusing to write {path}: the rewrite "
                          f"would not parse ({exc.msg})", file=sys.stderr)
                else:
                    path.write_text(new_text, encoding="utf-8")
                    fixed_files.append((str(path), n))
        for sf in sreport.findings:
            srow = {
                "file": str(path),
                **unit.loc(),
                "line": sf.line,
                "col": sf.col,
                "sink": sf.sink,
                "snippet": sf.snippet,
                "reason": sf.reason,
                "confidence": sf.confidence,
                "kind": sf.kind,
                "fixable": bool(sf.fix),
                "unfixable_why": sf.unfixable_why,
            }
            if covered(bl.SOURCE, path, srow):
                continue
            source_results.append(srow)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="arabic-lint",
        description="Find Arabic text corrupted by reshape+bidi before storage.",
    )
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--doctor", action="store_true",
                    help="report what THIS environment does with pre-shaped Arabic, and exit")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output")
    ap.add_argument("--exclude", action="append", default=[],
                    help="directory name to skip (repeatable)")
    ap.add_argument("--quiet", "-q", action="store_true",
                    help="only print the summary line")
    ap.add_argument("--min-severity", choices=SEVERITY_ORDER, default="stray",
                    help="only report stored findings at this severity or above. "
                         "'reshaped' gates CI on pipeline damage while tolerating the odd "
                         "pasted glyph (default: stray, i.e. report everything). The same "
                         "floor applies to bidi controls by position, so 'reshaped' also "
                         "narrows those to the unpaired ones")
    ap.add_argument("--no-source", action="store_true",
                    help="skip the Python source check, scan stored text only")
    ap.add_argument("--no-controls", action="store_true",
                    help="skip the bidi control check")
    ap.add_argument("--fix", action="store_true",
                    help="rewrite the source findings that can be fixed mechanically. "
                         "Never touches stored text, which cannot be repaired safely, "
                         "and never strips a bidi control: whether one belongs there "
                         "is a question about the document, not about the character.")
    ap.add_argument("--baseline", metavar="FILE", default=None,
                    help="report only findings that are NOT recorded in FILE, so an "
                         "existing codebase can gate on what it adds next. Suppressed "
                         "and no-longer-present counts are always printed.")
    ap.add_argument("--write-baseline", metavar="FILE", default=None,
                    help="record everything this run finds into FILE and exit 0")
    args = ap.parse_args(argv)

    if args.baseline and args.write_baseline:
        ap.error("--baseline reports what is new, --write-baseline records what exists; "
                 "using both in one run would suppress findings while writing them down. "
                 "Regenerate with --write-baseline alone.")

    if args.doctor:
        lines, _ = doctor_report()
        print("\n".join(lines))
        return 0

    if not args.paths:
        ap.error("give at least one path, or use --doctor")

    excludes = DEFAULT_EXCLUDES | set(args.exclude)

    # The baseline file lives inside the tree it describes, so it would otherwise be
    # scanned like any other .json. Even with escaped samples there is nothing in it
    # worth reading twice; skipping it keeps the feature honest if the format ever
    # carries raw text again.
    ledger_path = args.baseline or args.write_baseline
    try:
        ledger_resolved = Path(ledger_path).expanduser().resolve() if ledger_path else None
    except OSError:                                      # pragma: no cover - defensive
        ledger_resolved = None

    if args.baseline:
        try:
            base = bl.Baseline.load(args.baseline)
        except FileNotFoundError:
            print(f"arabic-lint: no such baseline: {args.baseline}\n"
                  f"             create one with --write-baseline {args.baseline}",
                  file=sys.stderr)
            return 2
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"arabic-lint: cannot read baseline {args.baseline}: {exc}",
                  file=sys.stderr)
            return 2
    else:
        base = bl.Baseline.empty()

    # (kind, file-relative-to-baseline, finding) for --write-baseline
    to_record: list[tuple[str, str, dict]] = []

    def covered(kind: str, path: Path, finding: dict) -> bool:
        """Record it, or ask the baseline whether it is already known."""
        if args.write_baseline:
            to_record.append((kind, bl.relative(path, args.write_baseline), finding))
            return False
        if not args.baseline:
            return False
        return base.allows(kind, bl.relative(path, args.baseline), finding)

    results: list[dict] = []
    control_results: list[dict] = []
    source_results: list[dict] = []
    fixed_files: list[tuple[str, int]] = []
    skipped: list[str] = []
    scanned = 0
    # Source is scanned after the walk, once every file's returned helpers are
    # known. Linking during the walk would miss a helper that sorts after its
    # caller. The link is the import, not "matplotlib was imported somewhere".
    pending_source: list[tuple[Path, Unit]] = []

    for root in args.paths:
        if not root.exists():
            print(f"arabic-lint: no such path: {root}", file=sys.stderr)
            return 2
        for path in iter_files(root, excludes):
            if ledger_resolved is not None:
                try:
                    if path.resolve() == ledger_resolved:
                        continue
                except OSError:                          # pragma: no cover - defensive
                    pass
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Silence here reads as "scanned and clean", which is the one
                # thing a linter must not say about a file it never read.
                if looks_like_text(path):
                    skipped.append(str(path))
                continue
            except OSError:
                continue
            try:
                units = units_for(path, text)
            except nbmod.NotebookError as exc:
                # Same rule as a file that would not decode: saying nothing
                # reads as "scanned and clean", which is the one thing a linter
                # must not say about a file it never read.
                skipped.append(f"{path} ({exc})")
                continue

            scanned += 1
            floor = SEVERITY_ORDER.index(args.min_severity)
            for unit in units:
                for f in scan_text(unit.text).findings:
                    if SEVERITY_ORDER.index(f.severity) < floor:
                        continue
                    row = {
                        "file": str(path),
                        **unit.loc(),
                        "line": f.line,
                        "col": f.col,
                        "presentation_forms": f.n_presentation,
                        "text": f.text,
                        "recovered": f.recovered,
                        "safe_to_autofix": f.recoverable,
                        "note": f.note,
                        "severity": f.severity,
                        "advice": f.advice,
                    }
                    if covered(bl.STORED, path, row):
                        continue
                    results.append(row)

            if not args.no_controls:
                # One floor, two ladders. `--min-severity` names a stored severity, and
                # the control risks sit at the same three positions, so the index is
                # what carries across rather than the word.
                for unit in units:
                    for c in scan_controls(unit.text).findings:
                        if RISK_ORDER.index(c.risk) < floor:
                            continue
                        crow = {
                            "file": str(path),
                            **unit.loc(),
                            "line": c.line,
                            "col": c.col,
                            "offset": c.offset,
                            "codepoint": f"U+{c.codepoint:04X}",
                            "name": c.name,
                            "kind": c.kind,
                            "bidi_class": c.bidi_class,
                            "balanced": c.balanced,
                            "partner_offset": c.partner_offset,
                            "risk": c.risk,
                            "note": c.note,
                            "advice": c.advice,
                        }
                        if covered(bl.CONTROL, path, crow):
                            continue
                        control_results.append(crow)

            is_py = path.suffix.lower() == ".py"
            is_nb = path.suffix.lower() == ".ipynb"
            if not args.no_source and (is_py or is_nb):
                # A notebook's code lives in cells, so each cell is parsed on its
                # own. A cell holding `%matplotlib inline` or `!pip install` is
                # not valid Python and is normal; scan_source already records a
                # skip rather than raising, so a magic costs a quiet skip and
                # never a crash or a false finding.
                src_units = ([Unit(text)] if is_py
                             else [u for u in units if u.is_code])
                for unit in src_units:
                    pending_source.append((path, unit))

    _scan_sources(pending_source, args, covered, source_results, fixed_files)

    if args.write_baseline:
        # Recording what already exists is not a failure, so this exits 0 even though
        # every one of these findings would otherwise have exited 1.
        try:
            document = bl.build(to_record, tool_version=_tool_version())
            n = bl.save(args.write_baseline, document)
        except OSError as exc:
            print(f"arabic-lint: cannot write baseline {args.write_baseline}: {exc}",
                  file=sys.stderr)
            return 2
        print(f"wrote {n} baseline entry(ies) covering {len(to_record)} finding(s) "
              f"to {args.write_baseline} - {scanned} file(s) scanned{_skip_note(skipped)}.")
        print("Run with --baseline to report only what is new from here.")
        return 0

    if args.as_json:
        payload = {"scanned": scanned, "skipped": skipped, "findings": results,
                   "control_findings": control_results,
                   "source_findings": source_results,
                   "fixed": [{"file": f, "calls": n} for f, n in fixed_files]}
        if args.baseline:
            payload["baseline"] = args.baseline
            payload["suppressed"] = base.suppressed
            payload["stale"] = base.stale
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 1 if (results or control_results or source_results) else 0

    if not args.quiet:
        for r in results:
            flag = "" if r["safe_to_autofix"] else "  [UNSAFE TO AUTO-FIX]"
            print(f"{where(r)}: "
                  f"{r['presentation_forms']} Arabic presentation forms stored "
                  f"[{r['severity']}]{flag}")
            print(f"    found     : {r['text']}")
            print(f"    would be  : {r['recovered']}")
            print(f"    {r['advice']}")
            print(f"    {r['note']}")
            print()

    if not args.quiet:
        for r in control_results:
            print(f"{where(r)}: {r['codepoint']} {r['name']} "
                  f"[{r['risk']}]")
            print(f"    kind      : {r['kind']} (bidi class {r['bidi_class']}), "
                  f"offset {r['offset']}")
            print(f"    balance   : {r['note']}")
            print(f"    {r['advice']}")
            print()

    if not args.quiet:
        for r in source_results:
            # Low confidence means the pre-shaped value leaves this file and we
            # have not seen the draw. Saying it renders reversed would overclaim.
            tag = "  [LOW]" if r["confidence"] == "low" else "  [RENDERS REVERSED]"
            print(f"{where(r)}: {HEADLINE[r['kind']]} {r['sink']}{tag}")
            print(f"    {r['snippet']}")
            print(f"    {r['reason']}")
            if r["unfixable_why"]:
                print(f"    NOT auto-fixable: {r['unfixable_why']}")
            elif not args.fix:
                print("    fixable: --fix deletes the call, which is correct only if you pin"
                      " the renderer")
                print("             version. If you cannot, gate on it instead"
                      " (README: dependency floor).")
            print()

    if fixed_files:
        for path, n in fixed_files:
            print(f"fixed {n} call(s) in {path}")
        print()

    if skipped and not args.quiet:
        for path in skipped:
            print(f"{path}: not scanned - looks like text but is not valid UTF-8")
        print()

    skipped_note = _skip_note(skipped)

    # Printed whether or not anything is left to report. A baseline that silently
    # hides work becomes permanent, which is how these files rot; and a team that
    # has FIXED baselined text should be told so rather than never hearing about it.
    baseline_note = ""
    if args.baseline:
        bits = [f"{base.suppressed} finding(s) suppressed by the baseline"]
        if base.stale:
            bits.append(f"{base.stale} baselined finding(s) no longer present "
                        f"(fixed - prune them with --write-baseline)")
        baseline_note = "; ".join(bits)

    unsafe = sum(1 for r in results if not r["safe_to_autofix"])
    parts = []
    if results:
        import collections
        by = collections.Counter(r["severity"] for r in results)
        breakdown = ", ".join(f"{by[s]} {s}" for s in SEVERITY_ORDER if by[s])
        parts.append(f"{len(results)} corrupted span(s) ({breakdown}); "
                     f"{unsafe} cannot be auto-fixed safely")
    if control_results:
        import collections
        byrisk = collections.Counter(r["risk"] for r in control_results)
        rbreak = ", ".join(f"{byrisk[s]} {s}" for s in RISK_ORDER if byrisk[s])
        parts.append(f"{len(control_results)} bidi control(s) ({rbreak})")
    remaining = [r for r in source_results if not (args.fix and r["fixable"])]
    if remaining:
        parts.append(f"{len(remaining)} source site(s) that will corrupt at render time"
                     + (" and could not be fixed mechanically" if args.fix else ""))
    if parts:
        print("; ".join(parts) + f" - in {scanned} file(s) scanned{skipped_note}.")
        if baseline_note:
            print(baseline_note + ".")
        return 1
    if fixed_files:
        print(f"all source findings fixed - {scanned} file(s) scanned{skipped_note}.")
        if baseline_note:
            print(baseline_note + ".")
        return 0
    if baseline_note:
        print(f"no NEW findings - {scanned} file(s) scanned{skipped_note}.")
        print(baseline_note + ".")
        return 0
    print(f"clean - {scanned} file(s) scanned{skipped_note}, no corrupted Arabic found.")
    return 0


def _skip_note(skipped: list[str]) -> str:
    return f", {len(skipped)} skipped (not valid UTF-8)" if skipped else ""


def _tool_version() -> str:
    """Recorded in the baseline so a stale format is traceable to a release."""
    try:
        from importlib.metadata import version

        return version("arabic-lint")
    except Exception:                                    # pragma: no cover - source tree
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
