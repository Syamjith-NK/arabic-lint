"""Read the text and Python source stored in a Jupyter notebook."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class NotebookText:
    cell: int
    text: str
    is_code: bool = False


def _joined(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return "".join(value)
    return None


def read_notebook(text: str) -> list[NotebookText]:
    """Return cell source and textual outputs with one-based cell positions."""
    document = json.loads(text)
    if not isinstance(document, dict) or not isinstance(document.get("cells"), list):
        raise ValueError("notebook does not contain a cells list")

    chunks: list[NotebookText] = []
    for cell_number, cell in enumerate(document["cells"], start=1):
        if not isinstance(cell, dict):
            continue
        cell_type = cell.get("cell_type")
        source = _joined(cell.get("source"))
        if source is not None and cell_type in {"code", "markdown"}:
            chunks.append(NotebookText(cell_number, source, cell_type == "code"))

        if cell_type != "code" or not isinstance(cell.get("outputs"), list):
            continue
        for output in cell["outputs"]:
            if not isinstance(output, dict):
                continue
            output_text = _joined(output.get("text"))
            if output_text is not None:
                chunks.append(NotebookText(cell_number, output_text))
            data = output.get("data")
            if not isinstance(data, dict):
                continue
            for mime, value in data.items():
                if not isinstance(mime, str) or not mime.startswith("text/"):
                    continue
                rendered = _joined(value)
                if rendered is not None:
                    chunks.append(NotebookText(cell_number, rendered))
    return chunks
