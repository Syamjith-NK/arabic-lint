"""Read a Jupyter notebook well enough to lint it.

A notebook is JSON, so `nbformat` is not needed to read one and the zero
dependency promise survives.

The reason this module exists rather than letting the plain text path handle
`.ipynb`: scanning the raw JSON does find stored corruption, because the
presentation forms sit in the JSON as literal characters, but it reports a
position inside a single-line blob. `nb.ipynb:1:48213` is not somewhere a
reader can go and act. Decoding the cells first means a finding can say *cell 3,
line 2*, which is what the notebook UI actually shows.

It also unlocks the half of the tool that was missing entirely. The source check
is gated on a `.py` suffix, so `get_display(reshape(...))` feeding `plt.title`
in a notebook cell was never examined — and the recipe is copied between
notebooks far more than between modules, because it is the thing someone pastes
from an answer to make one chart work.

A notebook stores its corruption **twice**: once in the code that produced it,
and once in the rendered output cell committed to the repository. Outputs are
therefore scanned too, and marked as outputs, because a reader cannot fix an
output by editing it — it is regenerated from the code above.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Cell:
    index: int           # position among ALL cells, 0-based, as the UI counts
    cell_type: str       # "code" | "markdown" | "raw" | ...
    source: str          # the cell's own text, newline-joined
    outputs: str = ""    # text outputs, joined; "" when there are none


@dataclass
class Notebook:
    cells: list[Cell] = field(default_factory=list)
    nbformat: int = 0

    @property
    def code_cells(self) -> list[Cell]:
        return [c for c in self.cells if c.cell_type == "code"]


class NotebookError(ValueError):
    """Not a notebook, or too damaged to read."""


def _join(src) -> str:
    """`source` and text outputs are either a list of lines or one string.

    The list form keeps its trailing newlines on every line but the last, so
    joining with "" is correct and joining with "\\n" doubles them — which would
    shift every reported line number by one per preceding line.
    """
    if isinstance(src, str):
        return src
    if isinstance(src, list):
        return "".join(x for x in src if isinstance(x, str))
    return ""


def _outputs_text(cell: dict) -> str:
    """Text an output actually shows. Images and HTML blobs are deliberately
    skipped: a base64 PNG is megabytes of noise that can never contain the
    Arabic we are looking for, and scanning it would make every notebook with a
    chart look like a slow file."""
    out: list[str] = []
    for o in cell.get("outputs") or []:
        if not isinstance(o, dict):
            continue
        kind = o.get("output_type")
        if kind == "stream":
            out.append(_join(o.get("text")))
        elif kind in ("execute_result", "display_data"):
            data = o.get("data") or {}
            if isinstance(data, dict):
                out.append(_join(data.get("text/plain")))
        elif kind == "error":
            # A traceback can carry the corrupted string that caused the error.
            out.append("\n".join(x for x in (o.get("traceback") or [])
                                 if isinstance(x, str)))
    return "\n".join(x for x in out if x)


def parse(text: str) -> Notebook:
    """Parse notebook JSON. Raises NotebookError on anything that is not one.

    Deliberately strict about `cells` being present: a `.ipynb` that is valid
    JSON but has no cells is not a notebook we can honestly report as scanned,
    and silence there reads as "scanned and clean" — the one thing a linter must
    not say about a file it never read.
    """
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise NotebookError(f"not valid JSON ({exc.msg if hasattr(exc, 'msg') else exc})")
    if not isinstance(doc, dict):
        raise NotebookError("JSON is not an object")
    cells = doc.get("cells")
    if not isinstance(cells, list):
        raise NotebookError("no 'cells' array - not a notebook")

    nb = Notebook(nbformat=doc.get("nbformat") or 0)
    for i, c in enumerate(cells):
        if not isinstance(c, dict):
            continue
        nb.cells.append(Cell(
            index=i,
            cell_type=str(c.get("cell_type") or "unknown"),
            source=_join(c.get("source")),
            outputs=_outputs_text(c),
        ))
    return nb
