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

# Presentation Forms-A (U+FB50-U+FDFF) used to be excluded wholesale, on the
# belief that the reshaper emits exactly one codepoint from it -- U+FDF2, the
# Allah ligature -- so treating the block as corruption would flag correct
# religious and formal text.
#
# Measured over every Arabic-block letter in all four joining positions, the
# reshaper emits 126 distinct Forms-A codepoints. 125 are POSITIONAL forms
# (ISOLATED/FINAL/INITIAL/MEDIAL); exactly one, U+FDF2, is a semantic ligature.
# The old rule was right about the ligatures and wrong about the block.
#
# What it cost, measured rather than assumed -- and it is smaller than it first
# looks, because Persian shares most of its alphabet with Arabic and those
# letters do have Forms-B mappings:
#   * UNDER-COUNTING on ordinary Persian and Urdu. "چگونه" reshaped reports 3
#     presentation forms under the old rule and 5 under this one; "دنیا" 3 -> 4.
#     The span was still flagged, just measured short.
#   * MISSED ENTIRELY only for strings made purely of Persian-specific letters,
#     which have no Forms-B mapping at all: گچ, چپ, گپ, پژ, پی and the bare
#     Farsi yeh ی all scored zero before and are caught now. Short, but real.
#
# So: positional forms are signal, exactly like Forms-B. The word ligatures are
# not -- ﷲ, ﷺ, ﷻ, ﷽ are typed deliberately in ordinary writing and
# flagging them would mark correct text as corrupt.
PRESENTATION_A = (0xFB50, 0xFDFF)

# U+FDF0..U+FDFD are the Arabic word ligatures (ﷺ, ﷻ, ﷽, ﷲ and the
# rest). Semantic characters, not glyph choices: excluded from the signal.
#
# They are NOT the only ones, which a range-based exclusion cannot express because
# Forms-A is interleaved:
#
#     FB50..FBB1  positional forms      signal
#     FBB2..FBD2  Arabic symbols        SEMANTIC
#     FBD3..FD3D  positional forms      signal
#     FD3E..FD4F  ornate parentheses ﴾ ﴿ and honorific ligatures ﵀ ﵊   SEMANTIC
#     FD50..FDCE  positional forms      signal
#     FDCF        ARABIC LIGATURE SALAAMUHU ALAYNAA                    SEMANTIC
#     FDF0..FDFD  word ligatures                                       SEMANTIC
#
# Measured 2026-09-09 against public Arabic corpora on Hugging Face: treating all of
# Forms-A as signal reported 35.6% of `synth_shamela_ocr_arabic_books` and 5.5% of
# `arabic-turath-ocr` as corrupted. Every one of those was ﴾ ﴿ around a Quranic
# quotation, or an honorific. Islamic heritage text is a large share of all Arabic
# training data, so this was not a rare edge: on that whole category the tool was
# wrong, loudly, and in the direction that produces a false accusation.
#
# The rule is derivable rather than tabulated. A contextual shaping artefact is
# exactly a character Unicode names "... ISOLATED/INITIAL/MEDIAL/FINAL FORM";
# anything else in the block is a character somebody typed on purpose. Deriving it
# from the name means new Unicode additions classify themselves.
PRESENTATION_A_LIGATURES = (0xFDF0, 0xFDFD)

def _shaping_forms_in_A() -> frozenset:
    import unicodedata
    out = set()
    for cp in range(PRESENTATION_A[0], PRESENTATION_A[1] + 1):
        try:
            name = unicodedata.name(chr(cp))
        except ValueError:            # unassigned, or a noncharacter
            continue
        if name.endswith((" ISOLATED FORM", " INITIAL FORM",
                          " MEDIAL FORM", " FINAL FORM")):
            out.add(cp)
    return frozenset(out)

# Arabic proper (letters, tashkeel, Arabic-Indic digits).
ARABIC = (0x0600, 0x06FF)

# The lam-alef ligatures. These are why recovery is not simply reversible:
# each is ONE codepoint that decomposes to TWO, and it decomposes in logical
# order while the text around it is in visual order -- so the pair comes out
# swapped relative to its neighbours.
LAM_ALEF = frozenset(range(0xFEF5, 0xFEFD))  # U+FEF5..U+FEFC


_SHAPING_A: frozenset = frozenset()   # filled in below, once


def _in(cp: int, rng: tuple[int, int]) -> bool:
    return rng[0] <= cp <= rng[1]


_SHAPING_A = _shaping_forms_in_A()


def is_presentation_form(ch: str) -> bool:
    """True only for codepoints that indicate baked-in Arabic glyph choices."""
    cp = ord(ch)
    if cp == BOM:
        return False
    if _in(cp, PRESENTATION_B):
        return True
    # Forms-A: only the positional forms. They are as much a baked-in glyph
    # choice as anything in Forms-B, and they are the only signal a reshaped
    # Persian or Urdu string leaves behind. The semantic characters scattered
    # through the same block are not signal and must never be flagged.
    if not _in(cp, PRESENTATION_A):
        return False
    if _in(cp, PRESENTATION_A_LIGATURES):
        return False              # word ligatures, incl. the Koranic stop signs
    return cp in _SHAPING_A


def is_arabic(ch: str) -> bool:
    return _in(ord(ch), ARABIC)


def has_lam_alef(text: str) -> bool:
    """True if the text contains a lam-alef ligature codepoint."""
    return any(ord(c) in LAM_ALEF for c in text)


# Severity, and why it is not decoration.
#
# Measured across 276 public Arabic datasets (see the corpus audit): 361 of 363 findings
# were a SINGLE presentation form sitting in otherwise correct text, and exactly one
# dataset carried long runs. Those two are different problems with different causes and
# different fixes, and reporting them identically is actively unhelpful:
#
#   stray     one glyph, pasted out of a PDF or left by OCR. Fix that character.
#   partial   a few. Usually the same, sometimes a fragment that went through the recipe.
#   reshaped  a long run: reshape+bidi ran over this text before it was stored. The file
#             is not the problem, the pipeline that wrote it is, and every other file it
#             touched needs checking too.
#
# A team with one stray glyph in ten thousand rows should not get the same alarm as a team
# whose corpus was destroyed, or they will switch the alarm off.
STRAY_MAX = 1
PARTIAL_MAX = 4


def severity_of(n_presentation: int) -> str:
    if n_presentation <= STRAY_MAX:
        return "stray"
    if n_presentation <= PARTIAL_MAX:
        return "partial"
    return "reshaped"


SEVERITY_ADVICE = {
    "stray": "a single presentation form in otherwise correct text: usually pasted from a "
             "PDF or left by OCR. Fix the character, not the pipeline.",
    "partial": "a short run of presentation forms. Check whether this text passed through "
               "reshape+bidi, or whether a fragment was pasted in.",
    "reshaped": "a long run of presentation forms: reshape+bidi ran over this text before it "
                "was stored. The pipeline that wrote this file is the problem, and every "
                "other file it touched needs checking.",
}

SEVERITY_ORDER = ("stray", "partial", "reshaped")


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

    @property
    def severity(self) -> str:
        return severity_of(self.n_presentation)

    @property
    def advice(self) -> str:
        return SEVERITY_ADVICE[self.severity]

    def __str__(self) -> str:
        status = "recoverable" if self.recoverable else "UNSAFE to auto-fix"
        return (f"{self.line}:{self.col}: {self.n_presentation} presentation forms "
                f"[{self.severity}] ({status})")


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
