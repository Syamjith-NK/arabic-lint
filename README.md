# arabic-lint

[![CI](https://github.com/Syamjith-NK/arabic-lint/actions/workflows/ci.yml/badge.svg)](https://github.com/Syamjith-NK/arabic-lint/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/arabic-lint)](https://pypi.org/project/arabic-lint/)
[![Python versions](https://img.shields.io/pypi/pyversions/arabic-lint)](https://pypi.org/project/arabic-lint/)
[![License: MIT](https://img.shields.io/github/license/Syamjith-NK/arabic-lint)](LICENSE)

The recipe `get_display(arabic_reshaper.reshape(text))` writes Arabic that still
looks Arabic and is not the string you typed. The file holds presentation-form
codepoints, so comparisons, tokenizers, and search miss it. `arabic-lint` finds
that text in JSON, localisation files, source, and notebooks.

| you typed | stored after the recipe | `arabic-lint` |
|---|---|---|
| `مرحبا` | `ﺎﺒﺣﺮﻣ` | flagged |
| `الإمارات العربية المتحدة` | `ﺓﺪﺤﺘﻤﻟﺍ ﺔﻴﺑﺮﻌﻟﺍ ﺕﺍﺭﺎﻣﻹﺍ` | flagged, and unsafe to undo |

Those pairs are [`demo/strings.json`](demo/strings.json). The clean strings in that
file (`موافق`, the title as typed) are not reported. The second row contains a
lam-alef ligature, so the recovery the tool prints is a different word. It shows
that candidate and does not rewrite the file.

```bash
pip install arabic-lint && arabic-lint .
```

Exit code 1 when anything is found, so the same command is a CI check.
MIT. **Zero dependencies.** Python 3.9+.

```
demo/strings.json:3:20: 21 Arabic presentation forms stored [reshaped]  [UNSAFE TO AUTO-FIX]
    found     : ﺓﺪﺤﺘﻤﻟﺍ ﺔﻴﺑﺮﻌﻟﺍ ﺕﺍﺭﺎﻣﻹﺍ
    would be  : اإلمارات العربية المتحدة
    a long run of presentation forms: a shaping pass ran over this text before it was stored. The pipeline that wrote this file is the problem, and every other file it touched needs checking.
    contains a lam-alef ligature; NFKC decomposition reorders the pair, so this recovery is wrong even though it looks like Arabic
```

## Add it

### pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Syamjith-NK/arabic-lint
    rev: v0.8.0  # a real tag; `pre-commit autoupdate` bumps it
    hooks:
      - id: arabic-lint
```

```bash
pre-commit install && pre-commit run arabic-lint --all-files
```

The hook installs into pre-commit's own environment. There is nothing to resolve,
because the package has no dependencies. The default hook fails on a reshaped run
(five or more presentation forms), not on one pasted glyph. Use
`id: arabic-lint-strict` when a single form should fail the commit too. Why that
default exists: [severity](#severity-one-pasted-glyph-is-not-a-destroyed-corpus).

### GitHub Actions

```yaml
# .github/workflows/arabic-lint.yml
name: arabic-lint
on: [push, pull_request]
jobs:
  arabic-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: Syamjith-NK/arabic-lint@v0.8.0
```

Pin a tag. Inputs, all optional: `path` (default `.`), `min-severity` (default
`reshaped`), `args`, and `version` to install a PyPI release instead of the code
at the ref.

```yaml
      - uses: Syamjith-NK/arabic-lint@v0.8.0
        with:
          path: locales
          min-severity: stray
          args: --exclude vendor --exclude fixtures
```

Or skip the action and call the package directly:

```yaml
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - run: pip install arabic-lint && arabic-lint . --min-severity reshaped
```

The job fails on findings, because that is what exit code 1 is for. Two limits,
before you turn it on:

- **The severity gate applies to stored text.** A source finding, the reshape+bidi
  recipe feeding a renderer that already shapes, is not graded by severity and is
  always reported. That check is quiet by design and does not fire on the files
  where the recipe is the right thing to do.
- **`--fix` is not in these snippets**, and should not be. Which repair is right
  depends on whether you control your dependency floor:
  [which fix is correct](#which-fix-is-correct-depends-on-your-dependency-floor).

A repository that already has findings can record them and fail only on what is
new. The file format is [below](#adopting-it-on-a-codebase-that-already-has-findings).

```bash
arabic-lint . --write-baseline .arabic-lint-baseline.json
arabic-lint . --baseline .arabic-lint-baseline.json
```

The rest of this file is why the check is shaped this way: what was measured,
what is deliberately not a finding, and when deleting the call is the wrong fix.

## Does this actually happen in the wild?

Yes, and here is the measurement rather than the assertion.

**[syamjithnk/arabic-corpus-audit](https://huggingface.co/datasets/syamjithnk/arabic-corpus-audit)**
— 341 public Arabic datasets on the Hugging Face Hub, 276 readable, 26,318 rows,
119,517 text fields, scanned with this tool.

- **1 dataset carries presentation forms in every sampled label.**
  `Yousefmd/arabic_ocr_dataset`: 1,000 of 1,000 sampled labels, 3 to 50 presentation forms per label
  (median 24), INITIAL/MEDIAL/FINAL/ISOLATED forms together. It is an OCR set, so the
  affected field is the **ground-truth label** — a model trained on it learns to emit glyph
  forms that will not compare equal to ordinary Arabic.
  **Only the shaping step ran there, not bidi:** the words are in logical order and plain
  `NFKC` recovers them. An earlier version of this README said visual order; that was wrong
  and is corrected in the audit's own
  [correction log](https://huggingface.co/datasets/syamjithnk/arabic-corpus-audit#corrections).
  (Its scale, stated plainly: 17 downloads. This proves the mechanism reaches training
  data; it is not evidence that widely-used corpora are affected.)
- **20 more** had single stray presentation forms, no reshaped runs. Still wrong, since a
  word becomes `[UNK]` on a tokenizer that does not normalise Unicode. Measured on four
  real vocabularies: **AraBERT v02 and mBERT replace the word with `[UNK]` 6 times out of
  6**, so every character of meaning is discarded before the model sees it. One corrupted
  letter out of four is enough. XLM-R normalises and is unaffected. A smaller defect than
  a reshaped corpus, but not a cosmetic one.
- Everything else was clean.

The audit ships its own scanner, so the numbers are re-derivable rather than trusted. It
also documents a false positive this tool used to produce against Islamic heritage text,
which is worth reading before you point any such tool at someone else's corpus.

**Confirmed in the wild.** Four bug reports were filed from the source check below. Two were
confirmed and closed as fixed on 12 September 2026, in
[whiteout-project/bot#110](https://github.com/whiteout-project/bot/issues/110) and its sibling
[kingshot-project/Kingshot-Discord-Bot#28](https://github.com/kingshot-project/Kingshot-Discord-Bot/issues/28),
by the maintainer of both. The maintainer also found the same pattern in a second code path
neither report mentioned. Two reports,
[ComfyUI-PersianText#3](https://github.com/shahkoorosh/ComfyUI-PersianText/issues/3) and
[Diwan#3](https://github.com/NoorBayan/Diwan/issues/3), are still open.

## Severity: one pasted glyph is not a destroyed corpus

The [audit](https://huggingface.co/datasets/syamjithnk/arabic-corpus-audit) settled this
empirically. Across 276 public Arabic datasets, **361 of 363 findings were a single stray
presentation form** in otherwise correct text, and exactly one dataset carried long runs.
Those are different problems:

| severity | forms | what it means | what to do |
|---|---|---|---|
| `stray` | 1 | pasted from a PDF, or OCR residue | fix the character |
| `partial` | 2–4 | a fragment, or a short pass through the recipe | check where the text came from |
| `reshaped` | 5+ | a shaping pass ran before this was stored | **audit the pipeline**, not the file |

Reporting them identically means a team with one stray glyph in ten thousand rows gets the
same alarm as a team whose corpus was destroyed, and then they switch the alarm off.

```bash
arabic-lint . --min-severity reshaped     # fail CI only on pipeline damage
```

## Adopting it on a codebase that already has findings

The exit code is binary, so on an existing repository the check fails on day one and
the usual outcome is that nobody turns it on. Record what is already there, then gate
on what gets added next:

```bash
arabic-lint . --write-baseline .arabic-lint-baseline.json   # what exists today
arabic-lint . --baseline .arabic-lint-baseline.json         # fail only on what is new
```

```
no NEW findings - 412 file(s) scanned.
2 finding(s) suppressed by the baseline.
```

Commit the baseline. Four things about it are deliberate:

- **Entries are keyed on content, not on line numbers.** Adding an import at the top of
  a file does not resurrect an entry, and does not hide a new one either.
- **The grade is part of the key.** A stray glyph that later grows into a `reshaped` run
  is a new problem in the same file, and is reported.
- **Identical findings are counted, not collapsed.** One baselined `U+200E` excuses one,
  not any number of them.
- **Suppressions are always printed**, and a baselined finding that has since been fixed
  is counted as no longer present. Silence about what a baseline hides is how these
  files quietly become permanent; prune them by regenerating.

Paths inside the file are relative to the baseline itself, so it means the same thing
whether CI runs from the repository root or a subdirectory. Samples are stored as
escaped codepoints (`U+FEE3 U+FE8E`) rather than raw text — the file would otherwise be
full of presentation forms and the next scan would report every one of them.

`--min-severity` still applies while writing, so `--write-baseline --min-severity
reshaped` records only pipeline damage and leaves stray glyphs reportable.

## Which case are you in?

The findings below all depend on what draws your text, and that is not knowable by
reading code: it depends on your installed matplotlib version and on whether your
Pillow was built with Raqm, which is a property of the wheel rather than the version.

```bash
arabic-lint --doctor
```

```
  matplotlib 3.11.0
      pre-shaping BREAKS text here
      3.11 and later shape text with libraqm and apply bidi themselves

  Pillow 12.2.0
      pre-shaping BREAKS text here
      this build has Raqm, so ImageDraw.text() shapes and reorders for you. Note
      this is a property of the BUILD: the same version on another machine can
      answer differently

Verdict: on matplotlib and Pillow, remove the reshape/bidi step and pass the
logical string straight through. Leaving it in reverses the text, silently.
That is THIS environment. Code installed on machines you do not control
should gate on the renderer version instead of removing the step.
```

If one renderer shapes and the other does not, it says so, because then a single
shared helper cannot be correct for both.

`--doctor` answers for the machine it runs on. If your code is installed on other
people's machines, that is a different question, and it changes the fix: see
[which fix is correct depends on your dependency floor](#which-fix-is-correct-depends-on-your-dependency-floor).

## What counts as corruption, and what does not

Arabic Presentation Forms-A is **interleaved**: positional glyph forms and ordinary
semantic characters share the same block. The ornate parentheses ﴾ ﴿ that enclose a
Quranic quotation live at U+FD3E/U+FD3F, in the middle of it, and honorific ligatures
like ﵀ sit just above them. Those are characters people type on purpose.

Treating the whole block as a corruption signal reports Islamic heritage text as
broken. Measured against public corpora on Hugging Face on 9 September 2026, the
naive rule flagged **35.6%** of one heritage OCR corpus and **5.5%** of another. Every
hit was a Quranic quotation mark or an honorific. With the block classified properly,
both read **0%**.

So the classification is derived rather than tabulated: a contextual shaping artefact
is exactly a codepoint Unicode names `... ISOLATED/INITIAL/MEDIAL/FINAL FORM`.
Anything else in the block is deliberate. New Unicode additions classify themselves.

## Three checks

**Stored corruption** — Arabic presentation forms that were already written to disk.

**Bidi controls** — the twelve invisible directional characters, when they survive
into Arabic text or into source. See
[invisible bidi controls](#invisible-bidi-controls) below.

**Source risk** (Python files and notebook code cells) — the reshape+bidi recipe
feeding a renderer that already shapes, which corrupts at *render* time before
anything is stored:

```
cogs/bear_track.py:715:16: pre-shaped text passed to matplotlib  [RENDERS REVERSED]
    return _bidi_get_display(_arabic_reshaper.reshape(text))
    matplotlib >= 3.11 shapes text with libraqm and applies bidi itself, so this
    reorders an already-reordered string and the text renders reversed
```

The recipe appears in **3,168 indexed Python files on GitHub** (measured 2026-09-08;
re-measured 3,128 on 2026-09-13, because the count drifts as GitHub reindexes, so
re-run the search rather than trusting the number). Flagging all of them would be
worthless, because whether it is a bug depends entirely on what draws the text:

| renderer | shapes and reorders? | verdict |
|---|---|---|
| matplotlib >= 3.11 | yes | pre-shaping **reverses** the text |
| Pillow built with Raqm | yes | pre-shaping **reverses** the text |
| Pillow without Raqm | no | pre-shaping is **required** |
| wordcloud | draws through Pillow | inherits that Pillow row. The finding names wordcloud, not a matplotlib import that only supplied a colormap |
| ReportLab, fpdf | no | pre-shaping is **required** |
| `print()` to a terminal | terminal-dependent | not this tool's call |

The source check follows import aliases, follows the recipe when `reshape()` and
`get_display()` are on separate lines, names the renderer that actually draws
rather than the first one imported, and ignores a pre-shaping helper that a script
never calls. It stays quiet unless one of these is true:

- a shaping renderer is imported **and** something in the file draws with the
  pre-shaped string
- the file imports matplotlib or Pillow **and** returns that string to a caller
  (low confidence: the draw may be in another file)
- another file in the same run imports such a helper and passes it to a drawing call

**wordcloud** counts as drawing on `generate`, `generate_from_text`, and
`generate_from_frequencies`, and `generate` only when the receiver is a
`WordCloud`. `model.generate()` stays silent, and so does constructing `WordCloud`
without generating. The reason names Pillow, not matplotlib.

**A returned helper** is tagged `[LOW]`. `--fix` will not rewrite it, because a
caller that draws with ReportLab still needs the recipe. A script that only prints
the result stays silent. `if __name__ == "__main__"` does not hide the return.
Matplotlib imported in a file that never calls the helper flags nothing. A helper
that only imports wordcloud stays silent until a scanned caller actually generates.
The cases are in `tests/test_source.py`.

Validated against six real repositories found by code search: it flags the three that
are genuinely broken, and stays silent on a ReportLab project, a dead helper in an
unrelated benchmark, and a script that only prints to a terminal. Pass `--no-source`
to turn it off.

A finding here says the recipe will corrupt text on a shaping renderer. It does not say
which repair is right for your project, and the report text overstates the case when it
suggests simply removing the call. Removing it is correct only if you control which
version of the renderer your code runs against:
[which fix is correct depends on your dependency floor](#which-fix-is-correct-depends-on-your-dependency-floor).

## `--fix`, and why it exists here but not for stored text

The same package refuses to repair one kind of damage and offers to repair the other,
which sounds inconsistent until you look at what each one is.

**Stored corruption is not safely repairable.** Undoing it round-trips exactly until the
text contains a lam-alef ligature, and then <span dir="rtl">السلام</span> comes back as
<span dir="rtl">السالم</span>: a real word, a different word, one that survives a
proofread. So the stored check reports and never rewrites.

**Source is the opposite.** Where the whole recipe sits inside one expression, deleting
it is exactly correct:

```diff
- return get_display(arabic_reshaper.reshape(text))
+ return text
```

`arabic-lint . --fix` makes that edit in place, then refuses to write if the result
would not parse.

It does **not** touch the split form:

```python
reshaped  = arabic_reshaper.reshape(segment)
processed = get_display(reshaped)        # NOT auto-fixable
```

Rewriting the second line to `processed = reshaped` would remove the explicit
reordering but leave the shaping applied. A shaping renderer still reorders that
text, so it can look correct while differing on ligatures. On Pillow without Raqm,
it renders in the wrong order because the deleted call was doing the reordering.
Both lines must be handled together, and which other code reads `reshaped` is not
knowable from that expression. Pass the original string to a shaping renderer, or
gate both steps on the renderer's shaping support. The tool leaves the file alone.

### Which fix is correct depends on your dependency floor

Deleting the call is not always the right repair, and **this tool cannot tell which case
you are in**, because the answer is not in the code. It depends on whether your project
controls the version of the renderer it runs against.

**If you set the floor**, delete the call. A repo that pins `matplotlib>=3.11`, an
application shipped with a lockfile, or anything whose supported versions are declared in
CI knows that the renderer shapes. That is the rewrite `--fix` performs.

**If you cannot force an upgrade**, gate on the version instead. A distributed application
whose users update on their own schedule, or a library installed next to whatever the user
already has, will keep running on older renderers after your fix ships. For those installs a
bare removal leaves no shaping at all, which trades reversed text for disjointed text: a
different bug, not a fix.

```python
import matplotlib
from packaging.version import Version

if Version(matplotlib.__version__) >= Version("3.11"):
    label = text                                    # matplotlib shapes and reorders
else:
    label = get_display(arabic_reshaper.reshape(text))
```

This is not a hypothetical. It is the fix the maintainer of
[whiteout-project/bot](https://github.com/whiteout-project/bot/issues/110) and
[kingshot-project/Kingshot-Discord-Bot](https://github.com/kingshot-project/Kingshot-Discord-Bot/issues/28)
applied in September 2026, in preference to the removal these reports proposed, and the
reason is one no static analyser can reach: their updater installs missing packages but
never bumps existing ones, so a bare removal would have left every existing 3.10 install
with no shaping at all.

So treat `--fix` as correct for code whose renderer version you pin, and read a source
finding as *this will corrupt text on a shaping renderer* rather than as *delete this line*.
Note also that `--doctor` reports the machine it runs on, which is the right answer for a
repo you deploy and the wrong one for software other people install.

> **Why this exists:** the recipe appears across [the indexed Python files counted
> in Two checks](#two-checks) above, and on matplotlib 3.11 it now renders Arabic
> *backwards* with no error at all.
> [The Arabic fix everyone recommends is now the bug](https://syamjith-nk.github.io/arabic-reshape-bidi-is-now-the-bug/) — the measurements, and
> what happened when it was filed against Pillow and matplotlib.

## Check it yourself

Do not take the claim on trust — `verify_mpl311.py` re-measures it on your machine:

```bash
pip install matplotlib arabic-reshaper python-bidi
python3 verify_mpl311.py
```

It renders each test string twice, once as typed and once through the workaround,
and reports the mean absolute pixel difference against a same-string control. On
matplotlib 3.11.0 with Pillow 12.2.0 (Raqm enabled) all five strings render
differently, against a control of exactly `0.000`.

The pixel numbers depend on your font and size, so treat them as a signal rather
than a constant. The evidence that needs no renderer at all is the codepoint
count the script also prints:

```
emirates    8 →  7 codepoints · 7 in the Arabic Presentation Forms block  ← lam-alef decomposed
```

`الإمارات` loses a codepoint because lam-alef is one codepoint that decomposes
into two, and every output character lands in a block that typed Arabic never
contains.

## What it actually detects

The most widely copied recipe for "making Arabic work" in Python is:

```python
text = get_display(arabic_reshaper.reshape(text))
```

Those two calls do the job a text engine is supposed to do: substitute each letter
for its contextual *presentation form*, and reorder the string into visual order.
If your renderer already does complex text layout — Pillow with Raqm, matplotlib,
any browser — the work happens twice and the output is wrong.

The real damage is when that string gets **written back**: to a config, an export,
a translation file. Now the corruption is at rest and every downstream reader
inherits it. It renders as clean-looking Arabic, so nobody who does not read the
script will ever notice.

The signature is unambiguous: Arabic **presentation form** codepoints in stored
text — all of Forms-B (U+FE70–U+FEFF), plus the positional forms in Forms-A
(U+FB50–U+FDFF). Those exist for legacy-encoding compatibility; correctly
authored modern Arabic never contains them.

The Arabic **word ligatures** ﷲ ﷺ ﷻ ﷽ (U+FDF0–U+FDFD) are deliberately
excluded — people type those on purpose, and flagging them would mark correct
religious and formal text as corrupt.

Forms-A matters more than it looks. Persian and Urdu share most of their
alphabet with Arabic, so most reshaped Persian already trips Forms-B — but it
was being *under-counted*, and words built only from Persian-specific letters
(گچ, چپ, گپ, پژ, پی, and a bare Farsi yeh) have no Forms-B mapping at all and
were missed outright.

## Why it reports instead of fixing

You would think you could just undo it: `NFKC` maps every presentation form back
to its base letter, and reversing undoes the visual reordering. That round-trips
exactly — **until the span contains a lam-alef ligature**.

A lam-alef ligature (`لا`, `لأ`, `لإ`, `لآ`) is *one* codepoint standing for *two*
letters. NFKC expands it in logical order while the text around it is still in
visual order, so the pair comes out reversed relative to its neighbours:

| original | naive "fix" | |
|---|---|---|
| `الإمارات` | `اإلمارات` | not a word |
| `السلام` | `السالم` | a **real but different** word |

That second row is the whole reason this tool exists rather than a `sed` command.
The output is still pronounceable Arabic, so it survives a proofread — and the
definite article followed by alef is one of the most common sequences in the
language, so this is not a corner case.

`arabic-lint` shows you the candidate recovery and tells you when it is unsafe.
It never rewrites stored text: `--fix` applies to source findings only, and refuses
even there when the two halves of the recipe are split across separate lines.

**One important qualifier.** All of the above assumes both calls ran. `reshape()` on its own,
with no `get_display()`, is common in the wild: it leaves presentation forms in **logical**
order, and then `NFKC` alone is a complete and safe repair, lam-alef included. So check word
order before you assume the harder case. If the string's words come back readable under plain
`NFKC`, bidi never ran and there is nothing to reverse. Getting this backwards means telling
someone their data is harder to repair than it is.

## Verification

- The test suite runs with **no runtime dependency** on `arabic_reshaper` or
  `python-bidi`: fixtures are recorded from a real run of both, and the two tests that
  do call them skip rather than fail when they are absent.
- Block boundaries were **measured, not assumed**: over a wide Arabic sample,
  `arabic_reshaper` 3.0.0 emits 53 distinct codepoints from Presentation Forms-B
  and never emits U+FEFF.
- **Validated against 3,826 real files** — the false positives that scan found are
  now regression tests:
  - **U+FEFF** sits inside Forms-B but is the byte order mark. Excluded.
  - **Presentation Forms-A is deliberately not a signal.** The reshaper emits
    exactly one codepoint from it (U+FDF2, the Allah ligature), and that character
    — like `ﷺ` U+FDFA, `ﷻ` U+FDFB and `﷽` U+FDFD — is used *intentionally* in
    ordinary Arabic writing. Treating the block as corruption flags correct
    religious and formal text.

## Invisible bidi controls

Twelve characters in Unicode carry direction and nothing else. They are zero-width,
they survive a proofread, and almost nothing handles all twelve the same way.

The blind spot is real and measured. `nmt_nfkc`, the **default normalizer for
SentencePiece training**, maps `U+200E` LEFT-TO-RIGHT MARK and `U+200F` RIGHT-TO-LEFT
MARK to a space and leaves the other ten alone — including `U+061C` ARABIC LETTER
MARK, which does for Arabic-script runs exactly what those two do everywhere else.
On `google/mt5-base`, a phrase goes from 5 pieces to 7 with an ALM in it and is
unchanged with an RLM; `google/mt5-base` also carries U+061C as its own vocabulary
token. Same visible text, different token sequence, decided by which pipeline saw it.

Filed upstream as
[google/sentencepiece#1331](https://github.com/google/sentencepiece/issues/1331), and
closed as fixed on 22 September 2026 by Taku Kudo, SentencePiece's author, who mapped
U+061C to a space in `nmt_nfkc` beside LRM and RLM and added a regression test covering
all three. The report was the contribution; the fix is his. It is in the repository and
not in a release: v0.2.2 is the newest and predates it, so the normalizer you install
today still passes ALM through, and a model that already exists keeps the precompiled
normalizer it was trained with. That is the `residue` band in the table below, and the
reason this check is here.

An unterminated scope is a different problem with a different weight:

```
config.py:14:31: U+202E RIGHT-TO-LEFT OVERRIDE [unpaired]
    kind      : override (bidi class RLO), offset 402
    balance   : never closed, so its scope runs to the end of the paragraph
    the directional scope is not paired. An unclosed embedding or override applies to
    everything after it to the end of the paragraph, so what is displayed is not the
    order the text is stored in. Close it, or remove it.
```

That is the Trojan Source class: the line *displays* in an order it is not stored in.
A balanced `RLE … PDF` is ordinary directional markup and is graded `scoped`, not
flagged as a defect.

| risk | what it is | gated by |
|---|---|---|
| `residue` | a lone ALM / LRM / RLM | `--min-severity stray` (default) |
| `scoped` | an explicit run, opened and closed | `--min-severity partial` |
| `unpaired` | a scope with no terminator, or a terminator with no scope | `--min-severity reshaped` |

One `--min-severity` floor gates both ladders by position, so `--min-severity
reshaped` narrows stored findings to pipeline damage *and* controls to unpaired ones.
The words are not shared on purpose: a stored `reshaped` means a shaping pass ran, and
printing that beside an unterminated override would be false.

**What stays quiet, and why.** A mark or a closed scope is reported only when its
paragraph actually contains Arabic — otherwise this would fire on every correctly
marked-up Hebrew document on earth. An *unpaired* control is reported whatever the
script, because an unterminated override reorders whatever follows it, and the Trojan
Source case lands in source files with no Arabic in them at all.

The nine explicit formatting characters are **derived from their Unicode bidi class**,
not tabulated, so additions classify themselves — the same rule as the presentation
forms. The three directional marks are named, because the property that identifies
them is `Bidi_Control`, which stdlib `unicodedata` does not expose. The obvious
substitute is wrong and the suite proves it: `Cf` + a strong bidi class matches 23
characters on Unicode 16.0, among them the Syriac abbreviation mark, two Kaithi number
signs and sixteen Egyptian hieroglyph joiners — characters people type on purpose.
Flagging those would be the ornate-parentheses mistake in a different block.

`--fix` never touches a control. Whether one belongs in a document is a question about
the document, not about the character. Pass `--no-controls` to turn the check off.

## Jupyter notebooks

`.ipynb` is scanned like any other file, and findings name the **cell**:

```
analysis.ipynb:cell2:5:11: pre-shaped text passed to matplotlib  [RENDERS REVERSED]
    plt.title(get_display(arabic_reshaper.reshape('مرحبا بالعالم')))
analysis.ipynb:cell3:1:10: 4 Arabic presentation forms stored [partial]
analysis.ipynb:cell3:1:1 (output): 4 Arabic presentation forms stored [partial]
```

Notebooks matter more than their share of the code: the recipe is copied between
notebooks far more than between modules, because it is the thing someone pastes from
an answer to make one chart work.

Three things worth knowing:

- **The same corruption is reported twice** — once in the cell source and once in the
  committed output — because a notebook stores both. The `(output)` label is there
  because you cannot fix an output by editing it; fix the code above and re-run.
- **Cells that are not Python are fine.** `%matplotlib inline` and `!pip install` cost a
  quiet skip, not a crash and not a false finding.
- **`--fix` will not rewrite a notebook.** Changing one cell means re-dumping the whole
  JSON, which reformats every untouched cell and buries a one-line fix in a whole-file
  diff. It tells you which cell to edit instead.

Reading needs no new dependency — a notebook is JSON, so `nbformat` is not required and
the tool still installs with nothing behind it.

## Known limits

- **Balance is computed per paragraph**, which is what the bidirectional algorithm
  does, so an opener on one line and its terminator on the next are reported as two
  unpaired controls. That is correct — the scope really did end at the paragraph — but
  it will surprise anyone who wrote them as a pair.
- The control check answers "is this scope closed", not "is this document's direction
  right". It does not resolve embedding levels, and it cannot tell you whether a
  correctly balanced override was a good idea.
- A document whose only Arabic is a standalone Allah ligature is missed. That is
  the deliberate trade above; any corrupted phrase around it still trips Forms-B.
- The recovery direction assumes bidi was applied. Text that was reshaped but
  *not* reordered recovers reversed. The tool shows you the candidate so you can
  see which case you have; it does not guess.
- The stored check only sees corruption that is *already written down*. The source
  check is what looks ahead at code that will create some, and `--doctor` is what
  answers it for the environment actually doing the rendering.
- **Notebook image and HTML outputs are not scanned.** A base64 PNG cannot contain the
  Arabic this looks for, and reading it would make every notebook with a chart slow for
  nothing. Text, `text/plain` and tracebacks are scanned.
- **Pure reordering is invisible to it, whatever produced it.** Reshaping leaves codepoints
  that authored Arabic never contains, so it is detectable; reordering leaves the *same*
  codepoints in a different order, so nothing here can flag it. A live example: pypdf
  classified the Arabic-Indic digits U+0660–U+0669 as right-to-left, so extraction returned
  `١٢٣٤` as `٤٣٢١` — valid Arabic digits in the wrong order, which this tool reads as clean.
  [Fixed upstream in pypdf](https://github.com/py-pdf/pypdf/pull/4077) — two lines of library
  change plus a test, merged 14 September 2026 — and **released in pypdf 6.19.0** on
  16 September 2026. So the version now answers half the question: below 6.19.0 the digits come
  out reversed. It does not answer the other half, because text extracted by an older pypdf is
  already wrong and scanning it will not reveal that. Check the digit order against the source
  document.
- **It cannot tell whether you control your dependency floor**, so it cannot tell you
  whether to delete the pre-shaping call or gate it on the renderer version. That
  distinction lives in your packaging and your users' upgrade path, not in the source
  file. See
  [which fix is correct depends on your dependency floor](#which-fix-is-correct-depends-on-your-dependency-floor).

## Contributing

Renderer knowledge, false-positive reports and test cases taken from real repositories
are all wanted. [CONTRIBUTING.md](CONTRIBUTING.md) has the two rules that matter: no
runtime dependencies, ever, and validate against real code rather than invented
snippets. Issue templates are in `.github/ISSUE_TEMPLATE/`. A false positive is the
most useful report this project can get.

A false positive is the most valuable report this project can get. The whole design is
built around staying quiet.

## Related

Part of a series measuring where Arabic silently breaks in software.
See also [`arabic-tts-frontend`](https://pypi.org/project/arabic-tts-frontend/) —
numerals, dates and currency converted to spoken Arabic before synthesis.

## The measurement behind this tool

The severity bands here are not a guess. They come from sweeping 341 public Arabic datasets
on the Hugging Face Hub: 361 of 363 findings across 276 readable datasets were a **single
stray presentation form**, and exactly one dataset carried long reshaped runs. That is why
one pasted glyph and a destroyed corpus are no longer reported identically.

The audit, its full per-dataset results and every script are archived and citable:

- DOI: [10.5281/zenodo.22733934](https://doi.org/10.5281/zenodo.22733934)
- Dataset: [`syamjithnk/arabic-corpus-audit`](https://huggingface.co/datasets/syamjithnk/arabic-corpus-audit)
