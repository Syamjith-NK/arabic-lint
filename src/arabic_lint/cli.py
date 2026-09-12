"""arabic-lint — find Arabic text that was corrupted before it was stored.

    arabic-lint path/to/repo
    arabic-lint data.json --json
    arabic-lint . --exclude node_modules --exclude .git

Two checks run over a tree:

  * stored corruption  - Arabic presentation forms that were written to disk
  * source risk        - the reshape+bidi recipe feeding a renderer that already
                         shapes, which corrupts at render time (Python files only)

Exit codes:
    0  clean
    1  corrupted Arabic found
    2  usage / IO error

The source check is deliberately quiet. It reports only where a shaping renderer is
imported AND something in the file actually draws with it, so ReportLab, terminal
output and dead helpers stay silent. Flagging every occurrence of the recipe would
mean thousands of false positives, and a checker nobody trusts is worse than none.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .detect import scan_text, SEVERITY_ORDER
from .source import scan_source, apply_fixes
from .doctor import report as doctor_report

TEXT_SUFFIXES = {
    ".txt", ".json", ".jsonl", ".csv", ".tsv", ".md", ".yml", ".yaml",
    ".xml", ".html", ".htm", ".svg", ".po", ".properties", ".strings",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".swift",
    ".php", ".rb", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".sql",
}

DEFAULT_EXCLUDES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


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
                         "pasted glyph (default: stray, i.e. report everything)")
    ap.add_argument("--no-source", action="store_true",
                    help="skip the Python source check, scan stored text only")
    ap.add_argument("--fix", action="store_true",
                    help="rewrite the source findings that can be fixed mechanically. "
                         "Never touches stored text, which cannot be repaired safely.")
    args = ap.parse_args(argv)

    if args.doctor:
        lines, _ = doctor_report()
        print("\n".join(lines))
        return 0

    if not args.paths:
        ap.error("give at least one path, or use --doctor")

    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    results: list[dict] = []
    source_results: list[dict] = []
    fixed_files: list[tuple[str, int]] = []
    scanned = 0

    for root in args.paths:
        if not root.exists():
            print(f"arabic-lint: no such path: {root}", file=sys.stderr)
            return 2
        for path in iter_files(root, excludes):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            scanned += 1
            report = scan_text(text)
            floor = SEVERITY_ORDER.index(args.min_severity)
            for f in report.findings:
                if SEVERITY_ORDER.index(f.severity) < floor:
                    continue
                results.append({
                    "file": str(path),
                    "line": f.line,
                    "col": f.col,
                    "presentation_forms": f.n_presentation,
                    "text": f.text,
                    "recovered": f.recovered,
                    "safe_to_autofix": f.recoverable,
                    "note": f.note,
                    "severity": f.severity,
                    "advice": f.advice,
                })

            if not args.no_source and path.suffix.lower() == ".py":
                sreport = scan_source(text)
                if args.fix and any(f.fix for f in sreport.findings):
                    new_text, n = apply_fixes(text, sreport.findings)
                    try:
                        compile(new_text, str(path), "exec")
                    except SyntaxError as exc:
                        print(f"arabic-lint: refusing to write {path}: the rewrite "
                              f"would not parse ({exc.msg})", file=sys.stderr)
                    else:
                        path.write_text(new_text, encoding="utf-8")
                        fixed_files.append((str(path), n))
                for sf in sreport.findings:
                    source_results.append({
                        "file": str(path),
                        "line": sf.line,
                        "col": sf.col,
                        "sink": sf.sink,
                        "snippet": sf.snippet,
                        "reason": sf.reason,
                        "confidence": sf.confidence,
                        "fixable": bool(sf.fix),
                        "unfixable_why": sf.unfixable_why,
                    })

    if args.as_json:
        json.dump({"scanned": scanned, "findings": results,
                   "source_findings": source_results,
                   "fixed": [{"file": f, "calls": n} for f, n in fixed_files]}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 1 if (results or source_results) else 0

    if not args.quiet:
        for r in results:
            flag = "" if r["safe_to_autofix"] else "  [UNSAFE TO AUTO-FIX]"
            print(f"{r['file']}:{r['line']}:{r['col']}: "
                  f"{r['presentation_forms']} Arabic presentation forms stored "
                  f"[{r['severity']}]{flag}")
            print(f"    found     : {r['text']}")
            print(f"    would be  : {r['recovered']}")
            print(f"    {r['advice']}")
            print(f"    {r['note']}")
            print()

    if not args.quiet:
        for r in source_results:
            print(f"{r['file']}:{r['line']}:{r['col']}: pre-shaped text passed to "
                  f"{r['sink']}  [RENDERS REVERSED]")
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

    unsafe = sum(1 for r in results if not r["safe_to_autofix"])
    parts = []
    if results:
        import collections
        by = collections.Counter(r["severity"] for r in results)
        breakdown = ", ".join(f"{by[s]} {s}" for s in SEVERITY_ORDER if by[s])
        parts.append(f"{len(results)} corrupted span(s) ({breakdown}); "
                     f"{unsafe} cannot be auto-fixed safely")
    remaining = [r for r in source_results if not (args.fix and r["fixable"])]
    if remaining:
        parts.append(f"{len(remaining)} source site(s) that will corrupt at render time"
                     + (" and could not be fixed mechanically" if args.fix else ""))
    if parts:
        print("; ".join(parts) + f" — in {scanned} file(s) scanned.")
        return 1
    if fixed_files:
        print(f"all source findings fixed — {scanned} file(s) scanned.")
        return 0
    print(f"clean — {scanned} file(s) scanned, no corrupted Arabic found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
