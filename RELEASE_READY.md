# Releasing

The upload path is the tag workflow, documented in
[CONTRIBUTING.md](CONTRIBUTING.md#releasing). Do not upload with twine from a
laptop, and do not reuse the steps that used to live in this file: 0.7.0 and
0.8.0 are already on PyPI, and a second upload of the same files will be rejected.

Pushing `vX.Y.Z` runs the tests, builds the sdist and wheel, and publishes with
PyPI trusted publishing. The tag has to match `version` in `pyproject.toml`.
Creating a GitHub Release does not publish.
