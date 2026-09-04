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

- **10/10 tests**, no runtime dependency on `arabic_reshaper` or `python-bidi`
  (fixtures are recorded from a real run of both).
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
- It detects corruption that is *already stored*. It cannot tell you whether your
  rendering pipeline is about to create some — for that, check
  `PIL.features.check("raqm")` at runtime in the environment doing the rendering.

## Related

Part of a series measuring where Arabic silently breaks in software.
See also [`arabic-tts-frontend`](https://pypi.org/project/arabic-tts-frontend/) —
numerals, dates and currency converted to spoken Arabic before synthesis.
