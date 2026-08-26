"""Detect Arabic text that was corrupted before it was stored.

The corruption this finds is the result of the most widely copied recipe for
"making Arabic work" in Python:

    text = get_display(arabic_reshaper.reshape(text))

That pair does two things a rendering engine is supposed to do: it substitutes
each letter for its contextual *presentation form*, and it reorders the string
into visual order. When the renderer already does complex text layout (Pillow
with Raqm, matplotlib, any browser), the work is done twice and the output is
wrong. Worse, the result is often written back to a file, a database or a JSON
export -- at which point the corruption is at rest, and every downstream reader
inherits it.

The signature is unambiguous: **Arabic Presentation Forms** codepoints in stored
text. Those blocks exist for compatibility with legacy encodings; correctly
authored modern Arabic never contains them. Zero false positives on clean
Arabic, on Arabic with tashkeel, on mixed Arabic/Latin, or on any other script.

Zero dependencies. Python 3.9+.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

# Arabic Presentation Forms-B is the corruption signal. Measured against
# arabic_reshaper 3.0.0 over a wide Arabic sample (26 Aug 2026): it emits 53
# distinct codepoints from this block and never emits U+FEFF.
PRESENTATION_B = (0xFE70, 0xFEFF)

# U+FEFF sits inside that range but is the BYTE ORDER MARK / zero-width
# no-break space. It is not Arabic and appears in perfectly healthy files.
# Flagging it was a real false positive found by scanning a live codebase.
BOM = 0xFEFF

# Presentation Forms-A (U+FB50-U+FDFF) is deliberately NOT a signal. The
# reshaper emits exactly one codepoint from it -- U+FDF2, the Allah ligature --
# and that character, like U+FDFA (ﷺ), U+FDFB (ﷻ) and U+FDFD (﷽), is used
# intentionally in ordinary Arabic writing. Treating the block as corruption
# flags correct religious and formal text. The cost of leaving it out is that a
# document containing only the word Allah as a ligature is missed; any real
# corrupted phrase around it trips Forms-B anyway.
PRESENTATION_A_INFORMATIONAL = (0xFB50, 0xFDFF)

# Arabic proper (letters, tashkeel, Arabic-Indic digits).
ARABIC = (0x0600, 0x06FF)

# The lam-alef ligatures. These are why recovery is not simply reversible:
# each is ONE codepoint that decomposes to TWO, and it decomposes in logical
# order while the text around it is in visual order -- so the pair comes out
# swapped relative to its neighbours.
LAM_ALEF = frozenset(range(0xFEF5, 0xFEFD))  # U+FEF5..U+FEFC


def _in(cp: int, rng: tuple[int, int]) -> bool:
    return rng[0] <= cp <= rng[1]


def is_presentation_form(ch: str) -> bool:
    """True only for codepoints that indicate baked-in Arabic glyph choices."""
    cp = ord(ch)
    return cp != BOM and _in(cp, PRESENTATION_B)


def is_arabic(ch: str) -> bool:
    return _in(ord(ch), ARABIC)


def has_lam_alef(text: str) -> bool:
    """True if the text contains a lam-alef ligature codepoint."""
    return any(ord(c) in LAM_ALEF for c in text)


@dataclass
class Finding:
    """One corrupted span."""

    line: int
    col: int
    text: str
    n_presentation: int
    recoverable: bool
    recovered: str | None = None
    note: str = ""

    def __str__(self) -> str:
        status = "recoverable" if self.recoverable else "UNSAFE to auto-fix"
        return f"{self.line}:{self.col}: {self.n_presentation} presentation forms ({status})"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def unsafe(self) -> list[Finding]:
        return [f for f in self.findings if not f.recoverable]


def recover(text: str) -> tuple[str, bool, str]:
    """Best-effort undo of reshape+bidi.

    Returns (recovered_text, is_safe, note).

    NFKC maps each presentation form back to its base letter, and reversing
    undoes the visual reordering. That round-trips exactly -- *unless* the span
    contains a lam-alef ligature.

    A lam-alef ligature is a single codepoint standing for two letters. NFKC
    expands it in logical order, but the surrounding text is in visual order, so
    the expanded pair ends up reversed relative to everything around it:

        الإمارات  ->  اإلمارات      ("the Emirates" -> not a word)
        السلام    ->  السالم        ("the peace"    -> "as-saalim", a real but different word)

    That second case is why this is reported rather than silently fixed: the
    output is still pronounceable Arabic, so it survives a proofread.
    """
    if not any(is_presentation_form(c) for c in text):
        return text, True, "nothing to recover"

    unsafe = has_lam_alef(text)
    guess = unicodedata.normalize("NFKC", text)[::-1]
    if unsafe:
        return guess, False, (
            "contains a lam-alef ligature; NFKC decomposition reorders the pair, "
            "so this recovery is wrong even though it looks like Arabic"
        )
    return guess, True, "NFKC + reverse round-trips exactly for this span"


def scan_text(text: str) -> Report:
    """Find corrupted spans in a string. Reports one finding per contiguous run."""
    report = Report()
    line = 1
    col = 1
    run_start: tuple[int, int] | None = None
    run: list[str] = []

    def flush() -> None:
        nonlocal run, run_start
        if run and run_start is not None:
            span = "".join(run)
            rec, safe, note = recover(span)
            report.findings.append(
                Finding(
                    line=run_start[0],
                    col=run_start[1],
                    text=span,
                    n_presentation=sum(1 for c in span if is_presentation_form(c)),
                    recoverable=safe,
                    recovered=rec,
                    note=note,
                )
            )
        run = []
        run_start = None

    for ch in text:
        if ch == "\n":
            flush()
            line += 1
            col = 1
            continue
        if is_presentation_form(ch):
            if run_start is None:
                run_start = (line, col)
            run.append(ch)
        else:
            # a space inside a corrupted phrase should not split the finding
            if run_start is not None and ch.isspace():
                run.append(ch)
            else:
                flush()
        col += 1
    flush()
    return report
