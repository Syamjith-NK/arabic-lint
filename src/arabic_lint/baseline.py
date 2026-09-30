"""Record what a codebase already has, so a team can gate on what it adds next.

The problem this closes is adoption, not detection. The exit code is binary: any
finding at or above `--min-severity` exits 1. A repository with two hundred
existing stray glyphs therefore has to fix everything before the check can be
turned on, or drop to `--min-severity reshaped` and lose stray detection
permanently, or not adopt the tool. A baseline is the difference between a check
that gets installed and one that gets bookmarked.

    arabic-lint . --write-baseline .arabic-lint-baseline.json
    arabic-lint . --baseline .arabic-lint-baseline.json

THE KEY IS CONTENT, NOT POSITION. A finding keyed by line number goes stale the
moment somebody adds an import at the top of the file, and then the baseline
either hides a new finding or resurrects an old one. An entry is keyed by the
file, the kind of check, a hash of the offending span, and its grade:

  * stored corruption - hash of the corrupted span,   grade = severity
  * bidi controls     - hash of codepoint + kind,     grade = risk
  * source risk       - hash of the snippet,          grade = kind of recipe

Grade is inside the key on purpose. A stray glyph that later becomes part of a
reshaped run is a NEW problem in the same file, and a baseline that swallowed it
would be worse than no baseline at all.

IDENTICAL FINDINGS ARE COUNTED, NOT COLLAPSED. Ten copies of the same lone
U+200E in one file hash to one key, so the entry carries a count and suppression
is allowed only that many times. The eleventh is reported. Without the count a
single baselined control would excuse any number of new ones.

PATHS ARE RELATIVE TO THE BASELINE FILE, not to the working directory. A
baseline committed at the repo root then means the same thing whether CI invokes
the tool from the root or from a subdirectory, which is not true of a
CWD-relative key.

⛔ THE SAMPLE IS STORED AS ESCAPED CODEPOINTS, AND THAT IS LOAD-BEARING. The
obvious thing is to write the offending text into the entry so a reviewer can
read it. Doing that writes Arabic presentation forms into a `.json` file, `.json`
is a scanned suffix, and so the very next run finds every one of them again in a
file the baseline does not cover - exit 1, for ever, on first use. Escaping also
keeps the baseline meaningful if it is renamed out from under the `--baseline`
flag. `U+FEE3 U+FE8E` is reviewable and inert.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1

#: Entry kinds, one per check the CLI runs.
STORED = "stored"
CONTROL = "control"
SOURCE = "source"

#: How much of an offending span is echoed into the entry for a human reader.
SAMPLE_CODEPOINTS = 16


def escape(text: str) -> str:
    """`محمد` -> `U+0645 U+062D U+0645 U+062F`, truncated, never raw.

    See the module note: raw text in the baseline makes the baseline a finding.
    """
    points = [f"U+{ord(c):04X}" for c in text[:SAMPLE_CODEPOINTS]]
    if len(text) > SAMPLE_CODEPOINTS:
        points.append("...")
    return " ".join(points)


def _digest(*parts: str) -> str:
    d = hashlib.sha256()
    for p in parts:
        d.update(p.encode("utf-8"))
        d.update(b"\x00")          # so ("ab","c") and ("a","bc") differ
    return d.hexdigest()[:16]


def relative(path: str | Path, baseline_path: str | Path) -> str:
    """Path of `path` as the baseline records it: relative to the baseline file."""
    base = Path(baseline_path).expanduser().resolve().parent
    try:
        target = Path(path).expanduser().resolve()
    except OSError:                                    # pragma: no cover - defensive
        target = Path(path)
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError:
        # Different drive on Windows: no relative path exists. An absolute key is
        # still deterministic, which is all the key has to be.
        return target.as_posix()


def content_of(kind: str, finding: dict) -> tuple[str, str]:
    """(hashable content, grade) for one finding dict as the CLI builds them."""
    if kind == STORED:
        return finding["text"], finding["severity"]
    if kind == CONTROL:
        # The codepoint alone is not enough: the same character is `residue` on its
        # own and part of a `scoped` pair elsewhere, and those are different facts.
        return f"{finding['codepoint']}:{finding['kind']}", finding["risk"]
    if kind == SOURCE:
        return finding["snippet"], finding["kind"]
    raise ValueError(f"unknown baseline entry kind: {kind!r}")


def sample_of(kind: str, content: str) -> str:
    """The human-readable echo of a finding, safe to store.

    ⚠️ Escaping is only right for the one kind whose content is the corrupted text
    itself. A control's content is already an ASCII descriptor (`U+200E:mark`) and a
    source finding's is a line of code; escaping those per character produced
    `U+0055 U+002B U+0032 ...` - the descriptor spelled out letter by letter, which
    is unreadable and tells a reviewer nothing. Caught by reading a written file
    rather than by a passing test.
    """
    if kind == STORED:
        return escape(content)
    return content[:120]


def key_for(kind: str, file_rel: str, finding: dict) -> str:
    content, grade = content_of(kind, finding)
    return f"{kind}|{file_rel}|{grade}|{_digest(content)}"


class Baseline:
    """A loaded baseline, consumed once per run.

    `allows()` is stateful by design: it spends an entry's count, so N baselined
    findings excuse exactly N findings and no more.
    """

    def __init__(self, entries: list[dict] | None = None, path: str | Path = "",
                 meta: dict | None = None) -> None:
        self.path = str(path)
        self.meta = meta or {}
        self.entries = entries or []
        self._left: dict[str, int] = {}
        self._by_key: dict[str, dict] = {}
        for e in self.entries:
            key = e["key"]
            self._left[key] = self._left.get(key, 0) + int(e.get("count", 1))
            self._by_key.setdefault(key, e)
        self.suppressed = 0

    # ---- reading -------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Baseline":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "entries" not in raw:
            raise ValueError("not an arabic-lint baseline: no 'entries' key")
        version = raw.get("version")
        if version != VERSION:
            raise ValueError(
                f"baseline version {version!r} was written by a different "
                f"arabic-lint (this one writes version {VERSION}); "
                f"regenerate it with --write-baseline")
        entries = raw["entries"]
        if not isinstance(entries, list):
            raise ValueError("not an arabic-lint baseline: 'entries' is not a list")
        for e in entries:
            if not isinstance(e, dict) or "key" not in e:
                raise ValueError("baseline entry has no 'key'")
        meta = {k: v for k, v in raw.items() if k != "entries"}
        return cls(entries, path, meta)

    @classmethod
    def empty(cls) -> "Baseline":
        return cls([], "", {})

    # ---- using ---------------------------------------------------------
    def allows(self, kind: str, file_rel: str, finding: dict) -> bool:
        """True if this finding was already recorded, spending one allowance."""
        key = key_for(kind, file_rel, finding)
        if self._left.get(key, 0) <= 0:
            return False
        self._left[key] -= 1
        self.suppressed += 1
        return True

    @property
    def stale(self) -> int:
        """Baselined findings that did not turn up: text somebody has since fixed.

        Counted, never fatal. A team that fixed forty entries should be told so,
        and should not have its build broken for the improvement.
        """
        return sum(n for n in self._left.values() if n > 0)

    def stale_entries(self) -> list[dict]:
        return [self._by_key[k] for k, n in self._left.items() if n > 0]


def build(records: list[tuple[str, str, dict]], tool_version: str = "") -> dict:
    """Collapse (kind, file_rel, finding) triples into the on-disk document."""
    counted: dict[str, dict] = {}
    for kind, file_rel, finding in records:
        key = key_for(kind, file_rel, finding)
        entry = counted.get(key)
        if entry is None:
            content, grade = content_of(kind, finding)
            counted[key] = {
                "key": key,
                "kind": kind,
                "file": file_rel,
                "grade": grade,
                "count": 1,
                "sample": sample_of(kind, content),
            }
        else:
            entry["count"] += 1
    return {
        "version": VERSION,
        "tool": "arabic-lint",
        "tool_version": tool_version,
        "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "note": ("Findings that already existed when this file was written. Runs with "
                 "--baseline report only what is NOT in here. Keys are content-based, "
                 "so moving code between lines does not resurrect an entry; paths are "
                 "relative to this file. Samples are escaped codepoints on purpose - "
                 "raw presentation forms here would be found by the next scan."),
        "entries": sorted(counted.values(), key=lambda e: (e["file"], e["kind"], e["key"])),
    }


def save(path: str | Path, document: dict) -> int:
    """Write the document, return how many entries it holds."""
    Path(path).write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(document["entries"])
