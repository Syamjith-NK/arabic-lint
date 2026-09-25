"""CLI-level tests: what the summary claims, and what it stays quiet about.

The bug these pin down: a file that would not decode as UTF-8 was dropped
without a word, so a directory of corrupted Arabic stored as UTF-16 reported
clean. Silence is only correct for a file that is not text at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from arabic_lint.cli import main

CORRUPT = "ﺓﺪﺤﺘﻤﻟﺍ ﺔﻴﺑﺮﻌﻟﺍ ﺕﺍﺭﺎﻣﻹﺍ"
PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"


def write_tree(root: Path) -> Path:
    body = json.dumps({"a": CORRUPT}, ensure_ascii=False)
    (root / "utf8.json").write_text(body, encoding="utf-8")
    (root / "utf16.json").write_bytes(body.encode("utf-16"))
    (root / "binary.txt").write_bytes(PNG_HEAD)
    (root / "clean.txt").write_text("nothing to see here\n", encoding="utf-8")
    return root


def test_the_summary_counts_a_file_that_did_not_decode(tmp_path, capsys):
    write_tree(tmp_path)
    main([str(tmp_path), "--quiet"])
    out = capsys.readouterr().out
    assert "1 skipped (not valid UTF-8)" in out


def test_the_skipped_file_is_named(tmp_path, capsys):
    write_tree(tmp_path)
    main([str(tmp_path)])
    out = capsys.readouterr().out
    assert f"{tmp_path / 'utf16.json'}: not scanned" in out


def test_quiet_counts_without_naming(tmp_path, capsys):
    write_tree(tmp_path)
    main([str(tmp_path), "--quiet"])
    out = capsys.readouterr().out
    assert "utf16.json" not in out
    assert "1 skipped (not valid UTF-8)" in out


def test_json_lists_the_skipped_files(tmp_path, capsys):
    write_tree(tmp_path)
    code = main([str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped"] == [str(tmp_path / "utf16.json")]
    assert code == 1  # the corrupted UTF-8 file is still a finding


def test_a_binary_file_is_not_reported(tmp_path, capsys):
    write_tree(tmp_path)
    (tmp_path / "utf8.json").unlink()
    (tmp_path / "utf16.json").unlink()
    code = main([str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped"] == []
    assert payload["findings"] == []
    assert code == 0


def test_a_skip_alone_does_not_fail_the_run(tmp_path, capsys):
    write_tree(tmp_path)
    (tmp_path / "utf8.json").unlink()
    code = main([str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 skipped (not valid UTF-8)" in out
    assert "no corrupted Arabic found" in out


def test_a_file_with_no_suffix_that_does_not_decode_is_still_counted(tmp_path, capsys):
    # iter_files() takes an explicitly named file whatever its suffix, so the
    # "looks like text" question is asked about content, not about the name.
    odd = tmp_path / "export"
    odd.write_bytes(json.dumps({"a": CORRUPT}).encode("utf-16"))
    main([str(odd), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped"] == [str(odd)]
