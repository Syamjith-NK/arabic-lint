"""Notebook tests: the half of the tool that never ran, and the positions it reports.

The bug these pin down: `.ipynb` was not in `TEXT_SUFFIXES`, so a directory
containing a notebook reported `0 file(s) scanned`. Naming the file explicitly
reached it, but only the stored check applied — the source check is gated on a
`.py` suffix, so the recipe feeding `plt.title()` in the next cell was never
examined. Notebooks are where plotting code actually lives.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arabic_lint.cli import main
from arabic_lint import notebook as nbmod

# Presentation forms in visual order: what get_display(reshape(...)) produces.
CORRUPT = "ﺒﺣﺭﺎ"
CLEAN_AR = "نص عربي سليم هنا."

RECIPE = [
    "import arabic_reshaper\n",
    "from bidi.algorithm import get_display\n",
    "import matplotlib.pyplot as plt\n",
    "\n",
    "plt.title(get_display(arabic_reshaper.reshape('مرحبا')))\n",
]


def cell(kind, source, outputs=None):
    c = {"cell_type": kind, "metadata": {}, "source": source}
    if kind == "code":
        c["execution_count"] = None
        c["outputs"] = outputs or []
    return c


def write_nb(path: Path, cells) -> Path:
    path.write_text(json.dumps(
        {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells},
        indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def fixture(root: Path) -> Path:
    """The four cells the issue asks for: clean Arabic prose, a magic, the
    recipe feeding matplotlib, and a stored corrupted string with an output."""
    return write_nb(root / "nb.ipynb", [
        cell("markdown", ["# تقرير\n", CLEAN_AR + "\n"]),
        cell("code", ["%matplotlib inline\n", "!pip install arabic-reshaper\n"]),
        cell("code", RECIPE),
        cell("code", ["label = '" + CORRUPT + "'\n"],
             [{"output_type": "stream", "name": "stdout", "text": [CORRUPT + "\n"]}]),
    ])


# --- the headline bug -------------------------------------------------------

def test_a_directory_scan_now_reaches_a_notebook(tmp_path, capsys):
    fixture(tmp_path)
    main([str(tmp_path), "--quiet"])
    out = capsys.readouterr().out
    assert "0 file(s) scanned" not in out
    assert "1 file(s) scanned" in out


def test_the_source_check_runs_on_cells(tmp_path, capsys):
    """This is the half that did not run at all before: the recipe is in a
    cell, not a .py file, so nothing examined it."""
    fixture(tmp_path)
    main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "source site(s) that will corrupt at render time" in out
    assert "matplotlib" in out


# --- positions a reader can act on -----------------------------------------

def test_a_finding_names_the_cell_not_a_json_offset(tmp_path, capsys):
    """Scanning the raw JSON reports a column inside a one-line blob, which
    points nowhere. The cell index is what the notebook UI shows."""
    fixture(tmp_path)
    main([str(tmp_path)])
    out = capsys.readouterr().out
    assert ":cell2:5:" in out, out          # the recipe, cell 2, line 5 of that cell
    assert ":cell3:1:" in out, out          # the stored string, cell 3, line 1


def test_source_line_is_cell_relative_not_file_relative(tmp_path):
    """The recipe's plt.title is line 5 of its cell. As a file line it would be
    far larger, and as a JSON line larger still."""
    nb = write_nb(Path(str(tmp_path / "x.ipynb")), [
        cell("markdown", ["# padding\n"] * 20),
        cell("code", RECIPE),
    ])
    rows = json.loads(_json_run(nb))["source_findings"]
    assert len(rows) == 1
    assert rows[0]["cell"] == 1
    assert rows[0]["line"] == 5


def test_an_output_is_reported_and_marked_as_an_output(tmp_path, capsys):
    """A notebook stores the corruption twice - in the code and in the rendered
    output committed beside it. The output cannot be hand-edited, so it is
    labelled rather than reported as if it were editable source."""
    fixture(tmp_path)
    main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "(output)" in out


# --- what it must stay quiet about ------------------------------------------

def test_clean_arabic_markdown_is_not_flagged(tmp_path):
    nb = write_nb(Path(str(tmp_path / "m.ipynb")), [cell("markdown", [CLEAN_AR])])
    assert json.loads(_json_run(nb))["findings"] == []


def test_a_magic_cell_does_not_crash_or_report(tmp_path):
    """`%matplotlib inline` and `!pip install` are not valid Python and are
    completely normal. scan_source records a skip rather than raising."""
    nb = write_nb(Path(str(tmp_path / "g.ipynb")),
                  [cell("code", ["%matplotlib inline\n", "!pip install x\n"])])
    d = json.loads(_json_run(nb))
    assert d["findings"] == [] and d["source_findings"] == []


def test_image_outputs_are_not_scanned(tmp_path):
    """A base64 PNG is megabytes that cannot contain Arabic. Scanning it would
    make every notebook with a chart slow for nothing."""
    nb = write_nb(Path(str(tmp_path / "i.ipynb")), [
        cell("code", ["plt.show()\n"],
             [{"output_type": "display_data",
               "data": {"image/png": "iVBORw0KGgo=" * 500}}]),
    ])
    assert json.loads(_json_run(nb))["findings"] == []


# --- a notebook it cannot read must not read as clean -----------------------

def test_malformed_notebook_is_skipped_not_called_clean(tmp_path, capsys):
    (tmp_path / "broken.ipynb").write_text("{not json", encoding="utf-8")
    code = main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "0 file(s) scanned" in out
    assert "broken.ipynb" in out
    assert code == 0


def test_json_that_is_not_a_notebook_is_skipped(tmp_path, capsys):
    (tmp_path / "x.ipynb").write_text('{"hello": "world"}', encoding="utf-8")
    main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "not a notebook" in out


# --- --fix must not rewrite a notebook --------------------------------------

def test_fix_refuses_to_rewrite_a_notebook_and_leaves_it_byte_identical(tmp_path, capsys):
    """Rewriting one cell means re-dumping the whole notebook, reformatting
    every untouched cell. The file must come out unchanged."""
    nb = write_nb(Path(str(tmp_path / "f.ipynb")), [cell("code", RECIPE)])
    before = nb.read_bytes()
    main([str(tmp_path), "--fix"])
    err = capsys.readouterr().err
    assert nb.read_bytes() == before
    assert "does not rewrite notebooks" in err


# --- the plain-file path is untouched ---------------------------------------

def test_a_python_file_still_has_no_cell_key(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("".join(RECIPE), encoding="utf-8")
    d = json.loads(_json_run(p))
    assert d["source_findings"] and all("cell" not in r for r in d["source_findings"])


# --- the reader itself ------------------------------------------------------

def test_source_lines_join_without_doubling_newlines():
    """`source` lines keep their own trailing newlines, so joining with "\\n"
    would insert a blank line between every pair and shift every reported line
    number by one per preceding line."""
    nb = nbmod.parse(json.dumps({"cells": [
        {"cell_type": "code", "source": ["a = 1\n", "b = 2\n"]}]}))
    assert nb.cells[0].source == "a = 1\nb = 2\n"


def test_source_may_be_a_bare_string():
    nb = nbmod.parse(json.dumps({"cells": [
        {"cell_type": "code", "source": "a = 1\n"}]}))
    assert nb.cells[0].source == "a = 1\n"


def test_parse_rejects_non_notebooks():
    for bad in ("[]", '{"no": "cells"}', "not json at all"):
        with pytest.raises(nbmod.NotebookError):
            nbmod.parse(bad)


def test_cell_index_counts_all_cells_as_the_ui_does():
    """Indices must match what the notebook UI shows, so markdown cells count."""
    nb = nbmod.parse(json.dumps({"cells": [
        {"cell_type": "markdown", "source": "#\n"},
        {"cell_type": "code", "source": "x\n"}]}))
    assert [c.index for c in nb.code_cells] == [1]


# --- helper -----------------------------------------------------------------

def _json_run(path: Path) -> str:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main([str(path), "--json"])
    return buf.getvalue()
