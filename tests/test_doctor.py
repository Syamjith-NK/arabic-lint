"""Tests for --doctor.

The environment these run in has a shaping matplotlib and a Raqm Pillow, which is
only one of the four combinations that matter. The interesting cases are the ones
this machine cannot produce, so they are constructed rather than imported.
"""
from arabic_lint.doctor import Check, _parse, MPL_SHAPING_FROM


def test_version_parsing_handles_real_version_strings():
    assert _parse("3.11.0") == (3, 11, 0)
    assert _parse("3.9.2") == (3, 9, 2)
    assert _parse("3.11.0rc1") == (3, 11, 0)      # pre-releases shape too
    assert _parse("11.0.0.post1") == (11, 0, 0)
    assert _parse("") == ()
    assert _parse("weird") == ()


def test_the_shaping_boundary_is_311_not_310():
    assert _parse("3.11.0") >= MPL_SHAPING_FROM
    assert _parse("3.12.0") >= MPL_SHAPING_FROM
    assert not _parse("3.10.5") >= MPL_SHAPING_FROM
    assert not _parse("3.9.0") >= MPL_SHAPING_FROM


def test_verdict_wording_per_state():
    breaks = Check("matplotlib", True, "3.11.0", True, "")
    needs = Check("matplotlib", True, "3.10.5", False, "")
    absent = Check("matplotlib", False, None, None, "")
    unknown = Check("Pillow", True, "12.0.0", None, "")
    assert "BREAKS" in breaks.verdict
    assert "required" in needs.verdict
    assert absent.verdict == "not installed"
    assert unknown.verdict == "could not determine"


def test_a_missing_library_is_an_answer_not_a_crash():
    """The package has no dependencies. Neither renderer being present is normal."""
    from arabic_lint import doctor
    real_mpl, real_pil = doctor.check_matplotlib, doctor.check_pillow
    try:
        doctor.check_matplotlib = lambda: Check("matplotlib", False, None, None, "not installed")
        doctor.check_pillow = lambda: Check("Pillow", False, None, None, "not installed")
        lines, breaks = doctor.report()
        assert breaks is False
        assert any("nothing to say" in l for l in lines)
    finally:
        doctor.check_matplotlib, doctor.check_pillow = real_mpl, real_pil


def test_disagreeing_renderers_are_called_out():
    """A shaping matplotlib beside a non-Raqm Pillow means one helper cannot serve both.

    This is the combination most likely to produce a confident wrong fix, because
    whichever renderer the developer tests first sets the rule they apply to the other.
    """
    from arabic_lint import doctor
    real_mpl, real_pil = doctor.check_matplotlib, doctor.check_pillow
    try:
        doctor.check_matplotlib = lambda: Check("matplotlib", True, "3.11.0", True, "shapes")
        doctor.check_pillow = lambda: Check("Pillow", True, "12.2.0", False, "no raqm")
        lines, breaks = doctor.report()
        assert breaks is True
        assert any("they disagree" in l for l in lines)
        assert any("Do not share a single helper" in l for l in lines)
    finally:
        doctor.check_matplotlib, doctor.check_pillow = real_mpl, real_pil
