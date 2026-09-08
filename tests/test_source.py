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
