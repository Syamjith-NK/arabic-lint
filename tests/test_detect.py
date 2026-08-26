"""Tests. Run: python3 -m pytest -q   (or python3 tests/test_detect.py)

The reference corruption is produced with arabic_reshaper + python-bidi when they
are installed; otherwise the same strings are hard-coded from a recorded run, so
the suite has no runtime dependency on either package.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arabic_lint.detect import (  # noqa: E402
    has_lam_alef, is_presentation_form, recover, scan_text,
)

# Recorded from arabic_reshaper 3.0.0 + python-bidi, verified 26 Aug 2026.
EMIRATES_CLEAN = "الإمارات"
EMIRATES_BAD = "ﺕﺍﺭﺎﻣﻹﺍ"      # has lam-alef -> unsafe
MARHABA_CLEAN = "مرحبا"
MARHABA_BAD = "ﺎﺒﺣﺮﻣ"                     # no lam-alef -> safe


def test_clean_arabic_is_not_flagged():
    for s in [EMIRATES_CLEAN, "مَرْحَبًا بِكُمْ", "Total: 1,250 درهم", "hello world",
              "שלום עולם", "المبيعات ٢٠٢٦"]:
        assert scan_text(s).ok, f"false positive on {s!r}"


def test_corrupted_arabic_is_flagged():
    r = scan_text(EMIRATES_BAD)
    assert not r.ok
    assert len(r.findings) == 1
    assert r.findings[0].n_presentation == 7


def test_lam_alef_span_is_reported_unsafe():
    r = scan_text(EMIRATES_BAD)
    assert has_lam_alef(EMIRATES_BAD)
    assert r.findings[0].recoverable is False
    assert r.unsafe


def test_span_without_lam_alef_round_trips_exactly():
    recovered, safe, _ = recover(MARHABA_BAD)
    assert safe is True
    assert recovered == MARHABA_CLEAN


def test_lam_alef_recovery_is_wrong_and_says_so():
    """The whole reason the tool exists: this 'fix' looks like Arabic and is not."""
    recovered, safe, note = recover(EMIRATES_BAD)
    assert safe is False
    assert recovered != EMIRATES_CLEAN
    assert "lam-alef" in note


def test_line_and_column_are_reported():
    text = "line one\nok here\n" + EMIRATES_BAD + "\n"
    r = scan_text(text)
    assert r.findings[0].line == 3
    assert r.findings[0].col == 1


def test_spaces_inside_a_corrupted_phrase_do_not_split_it():
    r = scan_text(MARHABA_BAD + " " + MARHABA_BAD)
    assert len(r.findings) == 1


def test_presentation_form_classifier():
    assert is_presentation_form("ﺕ")
    assert not is_presentation_form("ا")
    assert not is_presentation_form("a")



def test_bom_is_not_flagged():
    """U+FEFF is the byte order mark, not Arabic. Found as a live false positive."""
    assert not is_presentation_form("\ufeff")
    assert scan_text("\ufeff{\"a\": 1}").ok


def test_legitimate_arabic_ligatures_are_not_flagged():
    """These are used deliberately in ordinary Arabic writing."""
    for ch in ["\ufdfa", "\ufdfb", "\ufdf2", "\ufdfd"]:      # PBUH, jalla jalaaluhu, Allah, bismillah
        assert not is_presentation_form(ch), f"false positive on U+{ord(ch):04X}"
    assert scan_text("قال النبي \ufdfa كلاما طيبا").ok


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
