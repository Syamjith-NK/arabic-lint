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

# github.com/amirivojdan/shekar, shekar/visualization/word_cloud.py. Matplotlib
# supplies only the colormap; WordCloud is the renderer that consumes the
# pre-shaped frequency keys through Pillow.
WORDCLOUD_DRAWS = """
import matplotlib
import arabic_reshaper
from bidi import get_display
from wordcloud import WordCloud

def cloud(freqs, colormap="viridis"):
    cmap = matplotlib.colormaps[colormap]
    reshaped = {get_display(arabic_reshaper.reshape(k)): float(v) for k, v in freqs.items()}
    return WordCloud(colormap=cmap).generate_from_frequencies(reshaped)
"""

WORDCLOUD_UNUSED = """
import arabic_reshaper
from bidi import get_display
from wordcloud import WordCloud

def shape(text):
    return get_display(arabic_reshaper.reshape(text))

print(shape("مرحبا"))
"""

# --- the bidi-only form (issue #1) ---------------------------------------------
#
# No reshaper anywhere. `python-bidi` is downloaded around 9.4 million times a
# month and `arabic-reshaper` far less, so this is the larger population, and the
# check could not see any of it. Rendered on Pillow 12.2.0 with `raqm: True` at
# 48px Arial Unicode, `محمد الفارس` comes out as `سرافلا دمحم` here exactly as it
# does through the full recipe. The difference is that this one is still correctly
# joined, so it reads as fluent Arabic in reverse rather than as obvious rubbish.

# github.com/waseef-ullah/neural-style-transfer-urdu, checking_fonts.py.
# It even reshapes into a variable it then never uses, and draws the bidi-only one.
BIDI_ONLY_PIL = """
import numpy as np
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from bidi.algorithm import get_display

text_to_be_reshaped = 'بش'
bidi_text = get_display(text_to_be_reshaped)

image = Image.new("RGB", (64, 64), (255, 255, 255))
draw = ImageDraw.Draw(image)
draw.text((0, 0), bidi_text, (0, 0, 0), font=ImageFont.truetype('font.ttf', 10))
"""

# github.com/The1per/SleepApp, patient_report.py. The docstring states the belief
# this research disproves, and the ImportError fallback defines a local
# `get_display` beside the real one, which must not stop the import counting.
BIDI_ONLY_MPL = """
import matplotlib.pyplot as plt

try:
    from bidi.algorithm import get_display
except ImportError:
    def get_display(text, base_dir=None):
        return text

def _bidi(text):
    '''matplotlib+Agg renders text strictly left-to-right without BiDi awareness.'''
    return get_display(text)

plt.title(_bidi("שלום"))
"""

# github.com/aqntks/Easy-Yolo-OCR, easyocr.py. Pillow is imported, the OCR result
# is reordered for display, and nothing in the file draws any text.
BIDI_ONLY_NOTHING_DRAWS = """
import numpy as np
from PIL import Image
from bidi.algorithm import get_display

def readtext(self, image, result):
    for item in result:
        item[1] = get_display(item[1])
    return result
"""

# ReportLab does no reordering of its own, so bidi alone is the correct thing to
# do here and reporting it is how the tool would lose its users.
BIDI_ONLY_REPORTLAB = """
from reportlab.platypus import Paragraph
from bidi.algorithm import get_display

def rtl(text):
    return get_display(text)

Paragraph(rtl("مرحبا"))
"""

# github.com/fferegrino/pinteractions, pinteractions/display.py. `get_display` is
# an extremely common method name: code search returns hundreds of projects that
# define one on a screen, a dashboard or a blackjack hand. This one imports PIL
# and draws text, and has nothing whatever to do with bidi.
LOCAL_GET_DISPLAY = """
from PIL import Image
from PIL import ImageDraw

def get_display():
    return DisplayEPD()

def main():
    display = get_display()
    image = Image.new("1", (250, 122), 255)
    draw = ImageDraw.Draw(image)
    draw.text((0, 0), "hello")
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


def test_flags_wordcloud_generation_as_a_pillow_sink():
    r = scan_source(WORDCLOUD_DRAWS)
    assert len(r.findings) == 1
    assert r.findings[0].sink == "wordcloud"
    assert "Pillow" in r.findings[0].reason


def test_flags_each_wordcloud_generation_entry_point():
    for method in ("generate", "generate_from_frequencies", "generate_from_text"):
        source = WORDCLOUD_DRAWS.replace("generate_from_frequencies", method)
        assert scan_source(source).findings[0].sink == "wordcloud"


def test_wordcloud_import_without_generation_is_silent():
    r = scan_source(WORDCLOUD_UNUSED)
    assert r.findings == []
    assert any("nothing in this file draws" in s for s in r.skipped)


# --- the bidi-only form (issue #1) ---------------------------------------------


def test_flags_bidi_only_into_pillow():
    """get_display() with no reshape at all still reverses on a shaping renderer."""
    r = scan_source(BIDI_ONLY_PIL)
    assert len(r.findings) == 1
    f = r.findings[0]
    assert f.sink == "PIL"
    assert f.kind == "bidi-only"


def test_the_bidi_only_message_does_not_reuse_the_reshape_wording():
    """Bidi alone reverses ORDER and produces no presentation forms.

    Measured on Pillow 12.2.0, raqm True, 48px Arial Unicode: rendering
    get_display(s) against the logical string gives mean abs pixel diff 9.01 and
    reads as `سرافلا دمحم`, the same reversal the full recipe produces. What it
    does not do is emit presentation forms, so saying it "pre-shapes" is false.
    """
    f = scan_source(BIDI_ONLY_PIL).findings[0]
    assert "presentation forms" in f.reason
    assert "reorders an already-reordered string" not in f.reason
    assert f.format("x.py").startswith("x.py:9:13: pre-reordered text passed to PIL")


def test_flags_bidi_only_into_matplotlib_through_an_importerror_fallback():
    """A local def in the `except ImportError` arm is not what the call resolves to."""
    r = scan_source(BIDI_ONLY_MPL)
    assert len(r.findings) == 1
    assert r.findings[0].sink == "matplotlib"


def test_silent_for_bidi_only_when_nothing_draws():
    """Pillow imported, OCR output reordered, nothing drawn. Not our business."""
    r = scan_source(BIDI_ONLY_NOTHING_DRAWS)
    assert r.findings == []
    assert any("nothing in this file draws" in s for s in r.skipped)


def test_silent_for_bidi_only_into_reportlab():
    """The negative case that matters: bidi alone is CORRECT on a non-shaping sink."""
    r = scan_source(BIDI_ONLY_REPORTLAB)
    assert r.findings == []
    assert any("reportlab" in s for s in r.skipped)
    # and the message must not claim a reshaper was involved, because none was
    assert not any("pre-shaping" in s for s in r.skipped)


def test_silent_for_a_projects_own_get_display_method():
    """`def get_display` is a common name in code that has never heard of bidi.

    The bidi-only trigger fires only on a name imported from `bidi`, which is the
    single thing keeping this whole class of file quiet.
    """
    r = scan_source(LOCAL_GET_DISPLAY)
    assert r.findings == [] and r.skipped == []


def test_silent_for_a_bidi_only_helper_that_is_never_called():
    dead = """
import matplotlib.pyplot as plt
from bidi.algorithm import get_display

def rtl(text):
    return get_display(text)

plt.title("untouched")
"""
    r = scan_source(dead)
    assert r.findings == []
    assert any("never called" in s for s in r.skipped)


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


def test_the_bidi_only_form_is_reported_but_not_rewritten():
    """Detection is this change; rewriting is not.

    Removing a lone get_display() where the sink shapes is very likely correct,
    but --fix has never been validated against this form, and a rewriter that
    edits somebody else's source on a "probably" is not one to ship.
    """
    r = scan_source(BIDI_ONLY_PIL)
    f = r.findings[0]
    assert f.fix is None
    assert "not been validated" in f.unfixable_why
    new, n = apply_fixes(BIDI_ONLY_PIL, r.findings)
    assert n == 0 and new == BIDI_ONLY_PIL


def test_nothing_is_rewritten_where_there_is_no_finding():
    for src in (REPORTLAB_OK, DEAD_HELPER, PRINT_ONLY,
                BIDI_ONLY_REPORTLAB, BIDI_ONLY_NOTHING_DRAWS, LOCAL_GET_DISPLAY):
        r = scan_source(src)
        new, n = apply_fixes(src, r.findings)
        assert n == 0 and new == src
