"""Tests for the source check.

Every case here was taken from a real repository found by code search, not invented.
The negative cases matter more than the positive ones: a checker that flags all 3,168
files containing this pattern is noise, and noise gets switched off.
"""
from arabic_lint.source import scan_source

MPL_BUG = """
import matplotlib.pyplot as plt
import arabic_reshaper
from bidi.algorithm import get_display

def arab(text):
    return get_display(arabic_reshaper.reshape(text))

plt.title(arab("مرحبا"))
"""

ALIASED = """
import matplotlib.pyplot as plt
import arabic_reshaper as _ar
from bidi.algorithm import get_display as _gd

def fix(text):
    return _gd(_ar.reshape(text))

plt.title(fix("مرحبا"))
"""

SPLIT_STATEMENTS = """
from PIL import Image, ImageDraw
import arabic_reshaper
from bidi.algorithm import get_display

def draw(d, segment):
    reshaped = arabic_reshaper.reshape(segment)
    processed = get_display(reshaped)
    d.text((0, 0), processed)
"""

REPORTLAB_OK = """
from reportlab.platypus import Paragraph
import arabic_reshaper
from bidi.algorithm import get_display

def rtl(text):
    return get_display(arabic_reshaper.reshape(text))

Paragraph(rtl("مرحبا"))
"""

DEAD_HELPER = """
import matplotlib.pyplot as plt
import arabic_reshaper
from bidi.algorithm import get_display

def display_ar(text):
    return get_display(arabic_reshaper.reshape(text))

plt.title("untouched")
"""

PRINT_ONLY = """
from PIL import Image
import arabic_reshaper
from bidi.algorithm import get_display

img = Image.open("page.jpg")
print(get_display(arabic_reshaper.reshape("مرحبا")))
"""

BOTH_IMPORTED_MPL_DRAWS = """
from PIL import Image
import matplotlib.pyplot as plt
import arabic_reshaper
from bidi.algorithm import get_display

def arab(t):
    return get_display(arabic_reshaper.reshape(t))

plt.title(arab("مرحبا"))
"""


def test_flags_the_matplotlib_case():
    r = scan_source(MPL_BUG)
    assert len(r.findings) == 1
    assert r.findings[0].sink == "matplotlib"


def test_follows_import_aliases():
    """Real code renames these on import; the canonical spelling is not enough."""
    assert len(scan_source(ALIASED).findings) == 1


def test_follows_the_recipe_across_two_statements():
    """reshape() on one line, get_display() on the next, is the common shape."""
    r = scan_source(SPLIT_STATEMENTS)
    assert len(r.findings) == 1
    assert r.findings[0].sink == "PIL"


def test_silent_for_reportlab():
    """ReportLab does no shaping, so the recipe is correct and must not be flagged."""
    r = scan_source(REPORTLAB_OK)
    assert r.findings == []
    assert any("reportlab" in s for s in r.skipped)


def test_silent_for_a_helper_that_is_never_called():
    r = scan_source(DEAD_HELPER)
    assert r.findings == []
    assert any("never called" in s for s in r.skipped)


def test_silent_when_nothing_draws():
    """Pillow imported to load an image, Arabic sent to print(). Not our business."""
    assert scan_source(PRINT_ONLY).findings == []


def test_names_the_renderer_that_actually_draws():
    """With both libraries imported, alphabetical order would blame Pillow."""
    r = scan_source(BOTH_IMPORTED_MPL_DRAWS)
    assert len(r.findings) == 1
    assert r.findings[0].sink == "matplotlib"


def test_unparseable_file_is_skipped_not_crashed():
    r = scan_source("def broken(:\n")
    assert r.findings == []
    assert r.skipped


def test_clean_file_says_nothing():
    r = scan_source("import matplotlib.pyplot as plt\nplt.title('hello')\n")
    assert r.findings == [] and r.skipped == []


# --- automatic rewriting -------------------------------------------------------
#
# The asymmetry here is the point of the whole package. Stored corruption can never
# be repaired safely, because lam-alef decomposition produces a real but different
# word. Source is the opposite: where the whole recipe sits inside one expression,
# deleting it is exactly correct and provably so.

from arabic_lint.source import apply_fixes
import ast


def test_a_single_expression_is_rewritten_to_the_original_string():
    r = scan_source(MPL_BUG)
    assert r.findings[0].fix == '"مرحبا"' or r.findings[0].fix == "text"
    new, n = apply_fixes(MPL_BUG, r.findings)
    assert n == 1
    ast.parse(new)                       # the rewrite must still be valid Python
    assert "get_display" not in new.split("def arab")[1].split("\n")[1]
    assert not scan_source(new).findings  # and must actually clear the finding


def test_the_aliased_form_is_rewritten_too():
    r = scan_source(ALIASED)
    new, n = apply_fixes(ALIASED, r.findings)
    assert n == 1
    ast.parse(new)
    assert not scan_source(new).findings


def test_the_split_form_is_REFUSED_and_says_why():
    """Rewriting only the get_display() call would leave reshape() applied.

    That is still broken, and broken in a way that looks fixed, which is worse than
    leaving it alone.
    """
    r = scan_source(SPLIT_STATEMENTS)
    f = r.findings[0]
    assert f.fix is None
    assert "earlier line" in f.unfixable_why
    new, n = apply_fixes(SPLIT_STATEMENTS, r.findings)
    assert n == 0
    assert new == SPLIT_STATEMENTS       # not one byte touched


def test_nothing_is_rewritten_where_there_is_no_finding():
    for src in (REPORTLAB_OK, DEAD_HELPER, PRINT_ONLY):
        r = scan_source(src)
        new, n = apply_fixes(src, r.findings)
        assert n == 0 and new == src
