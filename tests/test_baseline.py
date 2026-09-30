"""Baseline tests: what it suppresses, what it must never suppress.

The interesting assertions are the negative ones. A baseline is only worth having
if it cannot hide a NEW problem, so the tests that matter are the ones proving a
severity change, an extra copy, and a different file all still fail the run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arabic_lint import baseline as bl
from arabic_lint.cli import main

# Two separate corrupted words, so a file can gain a finding without touching the
# one already recorded. Both are presentation forms written in visual order.
CORRUPT = "ﺓﺪﺤﺘﻤﻟﺍ ﺔﻴﺑﺮﻌﻟﺍ ﺕﺍﺭﺎﻣﻹﺍ"
OTHER = "ﺱﺭﺎﻔﻟﺍ ﺪﻤﺤﻣ"
LRM = "‎"


def write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def tree(tmp_path):
    write(tmp_path / "data.json", {"name": CORRUPT})
    (tmp_path / "clean.txt").write_text("nothing here\n", encoding="utf-8")
    return tmp_path


def run(*argv) -> int:
    return main([str(a) for a in argv])


# ---- the four tests the issue asked for -----------------------------------

def test_writing_a_baseline_then_rerunning_passes(tree, capsys):
    bfile = tree / "bl.json"
    assert run(tree, "--write-baseline", bfile) == 0
    out = capsys.readouterr().out
    assert "wrote 1 baseline entry" in out

    assert run(tree, "--baseline", bfile) == 0
    out = capsys.readouterr().out
    assert "no NEW findings" in out
    assert "1 finding(s) suppressed by the baseline" in out


def test_a_new_finding_in_the_same_file_still_fails(tree, capsys):
    bfile = tree / "bl.json"
    run(tree, "--write-baseline", bfile)
    capsys.readouterr()

    write(tree / "data.json", {"name": CORRUPT, "other": OTHER})
    code = run(tree, "--baseline", bfile, "--json")
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["suppressed"] == 1
    assert len(payload["findings"]) == 1, "only the new span should be reported"
    assert payload["findings"][0]["text"] == OTHER


def test_moving_the_finding_to_another_line_keeps_it_suppressed(tree, capsys):
    """The test that proves the key is content-based and not positional."""
    bfile = tree / "bl.json"
    run(tree, "--write-baseline", bfile)
    capsys.readouterr()

    # Same corrupted span, pushed down the file by unrelated lines above it.
    (tree / "data.json").write_text(
        "\n".join(["// a comment", "// another", json.dumps({"name": CORRUPT},
                                                            ensure_ascii=False)]),
        encoding="utf-8")

    code = run(tree, "--baseline", bfile, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["findings"] == []
    assert payload["suppressed"] == 1


def test_a_fixed_finding_is_counted_stale_not_fatal(tree, capsys):
    bfile = tree / "bl.json"
    run(tree, "--write-baseline", bfile)
    capsys.readouterr()

    write(tree / "data.json", {"name": "الإمارات العربية المتحدة"})   # logical order
    code = run(tree, "--baseline", bfile)
    out = capsys.readouterr().out
    assert code == 0
    assert "1 baselined finding(s) no longer present" in out


# ---- what it must refuse to hide -----------------------------------------

def test_a_second_copy_of_a_baselined_control_is_reported(tmp_path, capsys):
    """Counts, not a blanket excuse: one baselined LRM does not licence two."""
    f = tmp_path / "a.txt"
    f.write_text(f"مرحبا{LRM} بالعالم\n", encoding="utf-8")
    bfile = tmp_path / "bl.json"
    assert run(tmp_path, "--write-baseline", bfile) == 0
    capsys.readouterr()
    assert run(tmp_path, "--baseline", bfile) == 0
    capsys.readouterr()

    f.write_text(f"مرحبا{LRM} بالعالم{LRM}\n", encoding="utf-8")
    code = run(tmp_path, "--baseline", bfile, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["suppressed"] == 1
    assert len(payload["control_findings"]) == 1


def test_the_same_text_in_a_different_file_is_not_suppressed(tree, capsys):
    bfile = tree / "bl.json"
    run(tree, "--write-baseline", bfile)
    capsys.readouterr()

    write(tree / "copy.json", {"name": CORRUPT})
    code = run(tree, "--baseline", bfile, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert [f["file"] for f in payload["findings"]] == [str(tree / "copy.json")]


def test_severity_is_part_of_the_key(tmp_path):
    """A stray glyph that becomes a reshaped run is a new problem, not a known one."""
    stray = {"text": "بيتﺔ", "severity": "stray"}
    grown = {"text": "بيتﺔ", "severity": "reshaped"}
    assert bl.key_for(bl.STORED, "a.json", stray) != bl.key_for(bl.STORED, "a.json", grown)


def test_a_control_key_separates_residue_from_a_scoped_pair():
    a = {"codepoint": "U+200E", "kind": "mark", "risk": "residue"}
    b = {"codepoint": "U+200E", "kind": "embedding", "risk": "residue"}
    assert bl.key_for(bl.CONTROL, "a.txt", a) != bl.key_for(bl.CONTROL, "a.txt", b)


# ---- the self-referential trap -------------------------------------------

def test_the_baseline_file_is_not_itself_a_finding(tree, capsys):
    """⛔ The defect this pins: writing raw presentation forms into a .json baseline
    makes the next scan find every one of them again, in a file no entry covers, so
    the feature would fail on first use. Samples are escaped AND the file is skipped.
    """
    bfile = tree / "bl.json"
    run(tree, "--write-baseline", bfile)
    capsys.readouterr()

    raw = bfile.read_text(encoding="utf-8")
    assert CORRUPT not in raw, "the baseline must not contain raw presentation forms"
    assert "U+FE" in raw or "U+FB" in raw, "the sample should be escaped codepoints"

    # And a scan that does not even know about the baseline stays clean on it.
    (tree / "data.json").unlink()
    code = run(tree)
    out = capsys.readouterr().out
    assert code == 0, out


def test_a_control_sample_reads_as_a_codepoint_not_as_escaped_ascii(tmp_path):
    """A control's content is already `U+200E:mark`; escaping it per character gave
    `U+0055 U+002B U+0032 ...`, the descriptor spelled out letter by letter."""
    (tmp_path / "a.txt").write_text(f"مرحبا{LRM} بالعالم\n", encoding="utf-8")
    bfile = tmp_path / "bl.json"
    run(tmp_path, "--write-baseline", bfile)
    doc = json.loads(bfile.read_text(encoding="utf-8"))
    control = [e for e in doc["entries"] if e["kind"] == bl.CONTROL]
    assert control, doc
    assert control[0]["sample"].startswith("U+200E")
    assert "U+0055" not in control[0]["sample"]


def test_the_sample_is_reviewable_and_counted(tree):
    bfile = tree / "bl.json"
    run(tree, "--write-baseline", bfile)
    doc = json.loads(bfile.read_text(encoding="utf-8"))
    assert doc["version"] == bl.VERSION
    assert doc["entries"][0]["file"] == "data.json", "paths are relative to the baseline"
    assert doc["entries"][0]["count"] == 1
    assert doc["entries"][0]["sample"].startswith("U+")


# ---- usage and bad input -------------------------------------------------

def test_both_flags_together_is_a_usage_error(tree):
    with pytest.raises(SystemExit) as exc:
        run(tree, "--baseline", tree / "bl.json", "--write-baseline", tree / "bl.json")
    assert exc.value.code == 2


def test_a_missing_baseline_says_how_to_make_one(tree, capsys):
    code = run(tree, "--baseline", tree / "nope.json")
    err = capsys.readouterr().err
    assert code == 2
    assert "--write-baseline" in err


def test_a_baseline_from_another_version_is_refused(tree, capsys):
    bfile = tree / "bl.json"
    bfile.write_text(json.dumps({"version": 99, "entries": []}), encoding="utf-8")
    code = run(tree, "--baseline", bfile)
    assert code == 2
    assert "regenerate it" in capsys.readouterr().err


def test_a_file_that_is_not_a_baseline_is_refused(tree, capsys):
    bfile = tree / "bl.json"
    bfile.write_text('{"hello": 1}', encoding="utf-8")
    code = run(tree, "--baseline", bfile)
    assert code == 2
    assert "cannot read baseline" in capsys.readouterr().err


def test_paths_are_relative_to_the_baseline_not_the_cwd(tmp_path):
    """Two invocations from different directories must agree on the key."""
    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "data.json"
    bfile = tmp_path / "bl.json"
    assert bl.relative(target, bfile) == "pkg/data.json"
    # A baseline sitting beside the file keys it by bare name.
    assert bl.relative(target, tmp_path / "pkg" / "bl.json") == "data.json"


def test_min_severity_still_applies_when_writing(tmp_path, capsys):
    """--write-baseline records what the run REPORTS, so a floor narrows the file."""
    write(tmp_path / "data.json", {"a": CORRUPT})
    bfile = tmp_path / "bl.json"
    run(tmp_path, "--write-baseline", bfile, "--min-severity", "reshaped")
    capsys.readouterr()
    doc = json.loads(bfile.read_text(encoding="utf-8"))
    for e in doc["entries"]:
        assert e["grade"] == "reshaped"
