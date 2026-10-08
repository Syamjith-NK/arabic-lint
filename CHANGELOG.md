# Changelog

This file starts at 0.7.0. Releases before it are in the git history and in the
[tags](https://github.com/Syamjith-NK/arabic-lint/tags); rather than reconstruct them
from memory and get a date wrong, the record begins where it is accurate.

## Unreleased

On `main`, after 0.8.0. Not on PyPI until the next tag.

### Added

- **wordcloud is a drawing sink.** `generate`, `generate_from_text`, and
  `generate_from_frequencies` count as draws when the file imports `wordcloud`.
  `generate` counts only when the receiver is a `WordCloud`, so `model.generate()`
  stays silent even if wordcloud is imported. Constructing `WordCloud` without
  generating stays silent. A matplotlib import used only for a colormap is not the
  sink: the finding names wordcloud, and the reason names Pillow.

- **A helper that returns the recipe is reported.** A library function that returns
  the pre-shaped string is reported at low confidence (`[LOW]`) when that file
  imports matplotlib or Pillow, even if nothing in the file draws. `--fix` does not
  rewrite it. A script that prints the result, and an uncalled helper, stay silent.
  `if __name__ == "__main__"` does not hide the return. When several files are
  scanned together, a caller that imports the helper and passes the result to a
  drawing call is reported even if the helper imports no renderer. Matplotlib
  imported in a file that never calls the helper does not flag the helper. A helper
  that only imports wordcloud and returns the recipe stays silent until a scanned
  caller generates.

Notebook scanning is in 0.8.0, below. It is already released.

## 0.8.0 - 2026-10-04

### Added

- **Jupyter notebooks are scanned** (closes #7). `.ipynb` was not in `TEXT_SUFFIXES`, so a
  directory containing a notebook reported `0 file(s) scanned` — and notebooks are where
  plotting code actually lives. Naming the file explicitly did reach it, but only the
  stored check applied: the source check is gated on a `.py` suffix, so the
  `get_display(reshape(...))` call feeding `plt.title()` in the next cell was never
  examined.

  A notebook is JSON, so the reader is `json` from the standard library in a new
  `notebook.py` — `nbformat` is not needed and the zero-dependency promise survives.

  Design points that are load-bearing rather than cosmetic:

  - Findings report **cell and line within that cell** (`nb.ipynb:cell2:5:11`), not a
    position in the file. Scanning the raw JSON does find the stored forms, because they
    sit in the JSON as literal characters, but it reports a column inside a one-line blob
    — somewhere no reader can go and act.
  - **Outputs are scanned and labelled `(output)`.** A notebook stores its corruption
    twice: once in the code that produced it and once in the rendered output committed
    beside it. The label matters because an output cannot be fixed by editing it; it is
    regenerated from the code above.
  - **Image and HTML outputs are skipped.** A base64 PNG is megabytes that cannot contain
    Arabic, and scanning it would make every notebook with a chart slow for nothing.
  - **A cell that does not parse costs a quiet skip**, never a crash or a false finding.
    `%matplotlib inline` and `!pip install` are normal and are not valid Python;
    `scan_source` already records a skip rather than raising.
  - **Markdown cells are scanned for stored corruption** but never for source risk.
  - **`--fix` refuses to rewrite a notebook**, and says which cell to edit. Rewriting one
    cell means re-dumping the whole notebook JSON, which reformats every untouched cell
    and buries a one-line fix in a whole-file diff.
  - **A `.ipynb` that cannot be read is reported as skipped, not counted as clean.** Same
    rule as a file that will not decode: silence reads as "scanned and clean", which is
    the one thing a linter must not say about a file it never read.


- **`--baseline` and `--write-baseline`** (closes #8), so a codebase that already has
  findings can adopt the check and gate on what it adds next instead of having to fix
  everything first. Covers all three checks — stored corruption, bidi controls and
  source risk. `--json` carries `suppressed` and `stale`.

  Design points that are load-bearing rather than cosmetic:

  - Entries are keyed on **content, not position**: file + check + hash of the offending
    span + grade. Moving code between lines neither resurrects an old entry nor hides a
    new one.
  - **Grade is inside the key**, so a `stray` glyph that later becomes part of a
    `reshaped` run is reported as the new problem it is.
  - **Identical findings are counted**, and suppression spends that count. One baselined
    `U+200E` excuses exactly one.
  - **Suppressions are always printed**, and baselined findings that are no longer
    present are counted rather than being fatal — a team that fixed forty entries should
    hear about it, and should not have its build broken for the improvement.
  - Samples are stored as **escaped codepoints**. Writing raw presentation forms into a
    `.json` baseline would mean the next scan found every one of them again, in a file no
    entry covers: exit 1 for ever, on first use.

  `--baseline` and `--write-baseline` together is a usage error, since it would suppress
  findings in the same run that records them.

## 0.7.0

### Added

- **A third check: invisible bidi control characters**, in Arabic text and in source.
  Reports the codepoint, its Unicode name, line, column and absolute offset, its kind
  and bidi class, and whether its scope is balanced.

  Two different problems share the signal, and they are graded apart:

  - `residue` — a lone directional mark (`U+061C` ALM, `U+200E` LRM, `U+200F` RLM).
    Invisible, and **not consistently normalized**: `nmt_nfkc`, the default normalizer
    for SentencePiece training, strips LRM and RLM and leaves ALM. Measured on
    `google/mt5-base`, a phrase goes from 5 pieces to 7 with an ALM in it and is
    unchanged with an RLM, so the same visible text tokenizes two ways depending on
    which pipeline saw it.
    ([google/sentencepiece#1331](https://github.com/google/sentencepiece/issues/1331))
  - `scoped` — an embedding, override or isolate that is opened and closed. Ordinary
    directional markup; reported because pipelines disagree about whether it survives.
  - `unpaired` — a scope with no terminator, or a terminator with no scope. An
    unclosed `RLO` makes the rest of the paragraph *display* in an order it is not
    stored in: the Trojan Source class.

  The nine explicit formatting characters are **derived from their Unicode bidi
  class**, not tabulated, so a future addition classifies itself — the same rule the
  presentation-form check uses. The three marks are named, because the identifying
  property is `Bidi_Control` and stdlib `unicodedata` does not expose it; the obvious
  substitute (`Cf` plus a strong bidi class) matches 23 characters on Unicode 16.0,
  including the Syriac abbreviation mark, two Kaithi number signs and sixteen Egyptian
  hieroglyph joiners. A test asserts that over-match, so the reason cannot quietly
  become false.

- `--no-controls`, to turn the check off.

### Behaviour worth knowing before you upgrade

- **The check is quiet by design, and the silence is the feature.** A mark or a closed
  scope is reported only when its paragraph contains Arabic. An *unpaired* control is
  reported whatever the script, because an unterminated override reorders whatever
  follows it and the Trojan Source case lands in files with no Arabic at all.
- **Balance is computed per paragraph**, which is what the bidirectional algorithm
  does. An opener on one line and a terminator on the next are two unpaired controls,
  not a pair.
- `--min-severity` gates the new findings **by position on its own ladder**, so
  `--min-severity reshaped` narrows controls to the unpaired ones. The band names are
  deliberately not shared: a stored `reshaped` means a shaping pass ran, which an
  unterminated override does not.
- `--fix` never touches a control. Whether one belongs in a document is a question
  about the document, not about the character.
- A repository that already carries unpaired controls will now fail a gate it passed
  before. That is the point of the check, but it is a new failure mode on upgrade.

### Unchanged

- **Zero runtime dependencies.** `controls.py` imports `unicodedata` and
  `dataclasses`, both stdlib. CI asserts that every declared requirement belongs to an
  extra.
- The stored and source checks behave exactly as in 0.6.2. Their output format, JSON
  keys and exit codes are untouched; `control_findings` is a new key alongside them.
