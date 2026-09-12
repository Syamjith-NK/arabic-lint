# Contributing

Bug reports, renderer knowledge and real-world test cases are all welcome. This file
is short because most of what a contributor needs to know is one rule about
dependencies and one rule about evidence.

## Running the tests

```bash
git clone https://github.com/Syamjith-NK/arabic-lint
cd arabic-lint
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest -q
```

The whole suite should pass on a clean clone. If it does not, that is a bug worth
reporting on its own.

`[test]` is `pytest`, plus `arabic-reshaper` and `python-bidi`. Those last two are
there because a few tests deliberately re-derive their fixtures by running the **real**
reshaper rather than asserting against a recorded mock of it. A mock of the library
whose behaviour this tool is built around would only ever confirm what the author
already believed: the Forms-A undercount that the detector now handles was found by
running the actual reshaper over every Arabic letter in all four joining positions and
counting what came out. They are test-only extras, and `pip install arabic-lint`
installs neither.

Run the linter on itself too, since it is the fastest way to see whether a change made
it noisy:

```bash
arabic-lint src
```

`src` rather than `.` on purpose. The README, `demo/` and the tests all contain
deliberately corrupted Arabic, and `verify_mpl311.py` contains the recipe on purpose,
so the repository as a whole is expected to exit 1.

## Rule 1: zero runtime dependencies, and this one is not negotiable

`pyproject.toml` says `dependencies = []` and it has to stay that way. The reasons are
specific to what this tool is:

- **It is pointed at broken environments.** A checker that needs a working dependency
  tree cannot run in the place where the tree is the problem.
- **It must never install the libraries it audits.** `arabic-reshaper` and
  `python-bidi` are the two packages whose misuse this tool detects. Pulling either
  into the environment being audited would change the thing being measured.
- **It runs in other people's CI and in their pre-commit hooks.** Every dependency is
  an install that has to resolve on their Python, on their runner, behind their proxy.
  Zero is the only number that always resolves.

So the source check is `ast` from the standard library, not a parser package, and the
Unicode classification comes from `unicodedata`, not a table someone has to maintain.
Test-time dependencies are fine. Runtime ones are not, and a pull request that adds
one will be asked to remove it however good the feature is.

If you need a renderer to *verify* something, import it inside the function and treat
its absence as an answer rather than an error. `src/arabic_lint/doctor.py` is the
worked example: it reports on matplotlib and Pillow without importing either at module
load, and "not installed" is a legitimate result there.

## Rule 2: validate against real repositories, never invented snippets

This is the actual quality bar, and it is not a style preference.

Every case in `tests/test_source.py` was taken from a repository found by code search.
That was not for realism points. When the source check was first written it passed a
full suite of hand-written fixtures, and then failed against six real projects. Five
separate defects came out of that one afternoon, and every single one was a thing real
code does that textbook code does not:

1. **Import aliases.** A project had written
   `from bidi.algorithm import get_display as _bidi_get_display`. The checker only knew
   the canonical spelling, so it missed the file entirely, silently.
2. **`reshape()` and `get_display()` on separate lines.** Looking inside the
   `get_display()` call is not enough, because half the real code assigns the reshaped
   string to a variable first. Variables bound to a `reshape()` result have to be
   tracked.
3. **Picking the sink alphabetically blamed the wrong renderer.** A file that imports
   both matplotlib and Pillow got a matplotlib bug reported against Pillow, because
   `sorted()` put PIL first. The renderer has to be chosen from the drawing calls that
   are actually present.
4. **The drawing gate ran before renderer classification**, which made the ReportLab
   branch unreachable. A correct ReportLab project was reported with a misleading
   reason instead of being passed over with the right one.
5. **"Unreferenced means dead code" was a false negative in library modules.** A helper
   that nothing in its own file calls is dead in a script and perfectly alive in a
   module that other files import. Treating both the same way silenced real bugs.

None of those five are visible in a fixture you write yourself, because when you write
the fixture you already know what the checker is looking for.

That was not a one-off. A later sweep ran the source check over 26 more real
repositories and hand-read eight of them, and disagreeing with the tool on real files
produced four more defects, open as issues #1 to #4. Three of those are files the tool
calls clean and that are genuinely broken. Every one was found by reading the repository
and rendering the string, not by reasoning about the checker.

Practically, that means:

- A new detection rule should come with a test case copied from a real file, with a
  link to where it came from in the docstring or the commit message.
- A new **negative** case matters as much as a positive one. Silence is the feature
  here: the recipe appears in thousands of indexed Python files and most of those are
  not bugs. The tool stays quiet on ReportLab, on a non-Raqm Pillow, on terminal
  `print()`, and on a helper that nothing calls, and a change that makes any of those
  noisy is a regression even if it finds one more real bug.
- Claims about a renderer need a measurement, not a reading of its documentation.
  Whether Pillow shapes depends on whether that wheel was built with Raqm, which is a
  property of the build and not of the version number. If you want to add a renderer
  to the source check, say how you established that it does or does not shape, ideally
  by rendering an Arabic string on it and looking at the result.

## Things that are deliberate, not oversights

Before filing these as bugs, note they were decided on purpose and the reasoning is in
the README:

- **Stored corruption is reported and never rewritten.** Undoing it round-trips until
  the text contains a lam-alef ligature, where the "recovery" produces a real but
  different Arabic word that survives a proofread.
- **`--fix` refuses the split form** where `reshape()` and `get_display()` are on
  different lines. Removing only the second call leaves the shaping applied, which is
  still wrong and looks fixed.
- **Arabic word ligatures and the ornate Quranic parentheses are not corruption
  signals.** They live inside the same Unicode block as the positional forms, and
  treating the block as a signal reports Islamic heritage text as broken.

## Pull requests

- One change per pull request, with the reasoning in the commit message rather than
  only in the diff.
- Run `pytest -q` before pushing, and run `arabic-lint src` on your own branch.
- Keep the exit codes as they are: 0 clean, 1 findings, 2 usage or IO error. They are
  what makes the tool usable in CI, and changing them breaks every hook in the wild.
- New output fields belong in `--json` as well as in the human output, since CI
  consumers read the JSON.
- No em dashes in prose, and no version numbers written into documentation. A README
  that names a version goes stale on the next release and nothing warns you.

## Reporting a finding you believe is wrong

False positives are the most valuable bug report this project can get, because the
whole design is built around staying quiet. Include the smallest file that reproduces
it, the output of `arabic-lint --doctor`, and what the text is supposed to be. If the
text is corrupted at rest, say where it came from, since the fix usually belongs in
the pipeline that wrote it rather than in the file.
