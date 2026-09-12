# arabic-lint

Finds Arabic text that was corrupted **before it was stored** — in your JSON, your
localisation files, your database exports, your source code.

```bash
pip install arabic-lint
arabic-lint ./src
```

```
src/strings.json:3:20: 21 Arabic presentation forms stored  [UNSAFE TO AUTO-FIX]
    found     : ﺓﺪﺤﺘﻤﻟﺍ ﺔﻴﺑﺮﻌﻟﺍ ﺕﺍﺭﺎﻣﻹﺍ
    would be  : اإلمارات العربية المتحدة
    contains a lam-alef ligature; NFKC decomposition reorders the pair, so this
    recovery is wrong even though it looks like Arabic

3 corrupted span(s) in 1 file(s); 1 cannot be auto-fixed safely.
```

Exit code 1 when anything is found, so it drops into CI unchanged.
MIT. **Zero dependencies.** Python 3.9+.

## Does this actually happen in the wild?

Yes, and here is the measurement rather than the assertion.

**[syamjithnk/arabic-corpus-audit](https://huggingface.co/datasets/syamjithnk/arabic-corpus-audit)**
— 341 public Arabic datasets on the Hugging Face Hub, 276 readable, 26,318 rows,
119,517 text fields, scanned with this tool.

- **1 dataset is 100% corrupted.** `Yousefmd/arabic_ocr_dataset`: 400 of 400 fields, 23 to
  29 presentation forms per label, INITIAL/MEDIAL/FINAL/ISOLATED forms together and the
  words in reversed order. It is an OCR set, so the corrupted field is the **ground-truth
  label** — a model trained on it learns to emit presentation forms in visual order.
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
| `reshaped` | 5+ | reshape+bidi ran before this was stored | **audit the pipeline**, not the file |

Reporting them identically means a team with one stray glyph in ten thousand rows gets the
same alarm as a team whose corpus was destroyed, and then they switch the alarm off.

```bash
arabic-lint . --min-severity reshaped     # fail CI only on pipeline damage
```

## Run it on commit, or in CI

Corruption at rest is cheap to catch and expensive to find later, because by the time
anyone notices, the pipeline that produced it has written the same text into several
other places. Both integrations below default to the `reshaped` gate for the reason in
the table above.

### pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Syamjith-NK/arabic-lint
    rev: ""        # run `pre-commit autoupdate` to fill in the latest tag
    hooks:
      - id: arabic-lint
```

```bash
pre-commit install
pre-commit run arabic-lint --all-files
```

The hook installs into pre-commit's own isolated environment, and because the package
has no dependencies there is nothing to resolve. Use `id: arabic-lint-strict` instead
if you want a single stray presentation form to fail the commit too.

### GitHub Actions

```yaml
# .github/workflows/arabic-lint.yml
name: arabic-lint
on: [push, pull_request]
jobs:
  arabic-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Syamjith-NK/arabic-lint@main    # or pin to a tag
```

Inputs, all optional: `path` (default `.`), `min-severity` (default `reshaped`), `args`
for anything else the CLI takes, and `version` to install a published release from PyPI
instead of the code at the ref you pinned.

```yaml
      - uses: Syamjith-NK/arabic-lint@main
        with:
          path: locales
          min-severity: stray
          args: --exclude vendor --exclude fixtures
```

The job fails on findings, because that is what exit code 1 is for. Two things worth
knowing before you turn either of these on:

- **The severity gate applies to stored text.** A source finding, the reshape+bidi
  recipe feeding a renderer that already shapes, is not graded by severity and is
  always reported. That check is quiet by design and does not fire on the thousands of
  files where the recipe is correct, so it is not the thing that will flood you.
- **`--fix` is not wired into either integration**, and should not be. Which repair is
  right depends on whether you control your dependency floor, which is not in the
  source file: see
  [which fix is correct depends on your dependency floor](#which-fix-is-correct-depends-on-your-dependency-floor).

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

## Two checks

**Stored corruption** — Arabic presentation forms that were already written to disk.

**Source risk** (Python files) — the reshape+bidi recipe feeding a renderer that
already shapes, which corrupts at *render* time before anything is stored:

```
cogs/bear_track.py:715:16: pre-shaped text passed to matplotlib  [RENDERS REVERSED]
    return _bidi_get_display(_arabic_reshaper.reshape(text))
    matplotlib >= 3.11 shapes text with libraqm and applies bidi itself, so this
    reorders an already-reordered string and the text renders reversed
```

The recipe appears in **3,168 indexed Python files on GitHub** (measured 2026-09-08),
and flagging all of them would be worthless, because whether it is a bug depends
entirely on what draws the text:

| renderer | shapes and reorders? | verdict |
|---|---|---|
| matplotlib >= 3.11 | yes | pre-shaping **reverses** the text |
| Pillow built with Raqm | yes | pre-shaping **reverses** the text |
| Pillow without Raqm | no | pre-shaping is **required** |
| ReportLab, fpdf | no | pre-shaping is **required** |
| `print()` to a terminal | terminal-dependent | not this tool's call |

So the source check stays silent unless a shaping renderer is imported *and*
something in the file actually draws with it. It also follows import aliases, follows
the recipe when `reshape()` and `get_display()` are on separate lines, names the
renderer that actually draws rather than the first one imported, and ignores a
pre-shaping helper that a script never calls.

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

Rewriting the second line to `processed = reshaped` would remove the reordering and
leave the shaping applied. That is still wrong, and wrong in a way that looks fixed.
Both lines have to go, and which other code reads `reshaped` is not knowable from that
expression. So it says so and leaves the file alone.

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

> **Why this exists:** the recipe below appears in **~1,160 indexed files on
> GitHub** (measured 2026-09-04; the figure drifts as GitHub reindexes), and on
> matplotlib 3.11 it now renders Arabic *backwards* with no error at all.
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
It never rewrites your files.

## Verification

- The test suite runs with **no runtime dependency** on `arabic_reshaper` or
  `python-bidi` (fixtures are recorded from a real run of both).
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

## Known limits

- A document whose only Arabic is a standalone Allah ligature is missed. That is
  the deliberate trade above; any corrupted phrase around it still trips Forms-B.
- The recovery direction assumes bidi was applied. Text that was reshaped but
  *not* reordered recovers reversed. The tool shows you the candidate so you can
  see which case you have; it does not guess.
- The stored check only sees corruption that is *already written down*. The source
  check is what looks ahead at code that will create some, and `--doctor` is what
  answers it for the environment actually doing the rendering.
- **It cannot tell whether you control your dependency floor**, so it cannot tell you
  whether to delete the pre-shaping call or gate it on the renderer version. That
  distinction lives in your packaging and your users' upgrade path, not in the source
  file. See
  [which fix is correct depends on your dependency floor](#which-fix-is-correct-depends-on-your-dependency-floor).

## Contributing

Renderer knowledge, false-positive reports and test cases taken from real repositories
are all wanted. [CONTRIBUTING.md](CONTRIBUTING.md) has the two rules that matter: no
runtime dependencies, ever, and validate against real code rather than invented
snippets. The open issues are scoped so a stranger can start on one, and the ones
labelled `good first issue` genuinely are.

A false positive is the most valuable report this project can get. The whole design is
built around staying quiet.

## Related

Part of a series measuring where Arabic silently breaks in software.
See also [`arabic-tts-frontend`](https://pypi.org/project/arabic-tts-frontend/) —
numerals, dates and currency converted to spoken Arabic before synthesis.
