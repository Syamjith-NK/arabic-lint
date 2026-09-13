"""Notebook coverage based on Syamjith-NK/arabic-lint#7's reproduction."""

import json

from arabic_lint.cli import main


def test_directory_scan_checks_notebook_cells_and_outputs(tmp_path, capsys):
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": [
                    "import matplotlib.pyplot as plt\n",
                    "import arabic_reshaper\n",
                    "from bidi.algorithm import get_display\n",
                    "text = get_display(arabic_reshaper.reshape('مرحبا'))\n",
                    "plt.title(text)\n",
                ],
                "outputs": [{"output_type": "stream", "text": ["ﺎﺒﺣﺮﻣ\n"]}],
            },
            {"cell_type": "markdown", "source": ["هذا نص عربي سليم\n"]},
            {"cell_type": "code", "source": ["%matplotlib inline\n"], "outputs": []},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = tmp_path / "example.ipynb"
    path.write_text(json.dumps(notebook, ensure_ascii=False), encoding="utf-8")

    assert main([str(tmp_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)

    assert report["scanned"] == 1
    assert [(finding["cell"], finding["line"]) for finding in report["findings"]] == [
        (1, 1)
    ]
    assert [finding["cell"] for finding in report["source_findings"]] == [1]
    assert report["source_findings"][0]["line"] == 4
    assert report["source_findings"][0]["sink"] == "matplotlib"

    assert main([str(path), "--json", "--no-source"]) == 1
    stored_only = json.loads(capsys.readouterr().out)
    assert len(stored_only["findings"]) == 1
    assert stored_only["source_findings"] == []
