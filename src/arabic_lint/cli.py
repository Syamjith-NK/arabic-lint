"""arabic-lint — find Arabic text that was corrupted before it was stored.

    arabic-lint path/to/repo
    arabic-lint data.json --json
    arabic-lint . --exclude node_modules --exclude .git

Exit codes:
    0  clean
    1  corrupted Arabic found
    2  usage / IO error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .detect import scan_text

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
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output")
    ap.add_argument("--exclude", action="append", default=[],
                    help="directory name to skip (repeatable)")
    ap.add_argument("--quiet", "-q", action="store_true",
                    help="only print the summary line")
    args = ap.parse_args(argv)

    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    results: list[dict] = []
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
            for f in report.findings:
                results.append({
                    "file": str(path),
                    "line": f.line,
                    "col": f.col,
                    "presentation_forms": f.n_presentation,
                    "text": f.text,
                    "recovered": f.recovered,
                    "safe_to_autofix": f.recoverable,
                    "note": f.note,
                })

    if args.as_json:
        json.dump({"scanned": scanned, "findings": results}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 1 if results else 0

    if not args.quiet:
        for r in results:
            flag = "" if r["safe_to_autofix"] else "  [UNSAFE TO AUTO-FIX]"
            print(f"{r['file']}:{r['line']}:{r['col']}: "
                  f"{r['presentation_forms']} Arabic presentation forms stored{flag}")
            print(f"    found     : {r['text']}")
            print(f"    would be  : {r['recovered']}")
            print(f"    {r['note']}")
            print()

    unsafe = sum(1 for r in results if not r["safe_to_autofix"])
    if results:
        print(f"{len(results)} corrupted span(s) in {scanned} file(s); "
              f"{unsafe} cannot be auto-fixed safely.")
        return 1
    print(f"clean — {scanned} file(s) scanned, no corrupted Arabic found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
