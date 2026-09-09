"""Answer the question the rest of this tool creates: what does MY setup do?

The source check reports that pre-shaping is wrong *if* the renderer already
shapes, and whether it does is not knowable from reading code. It depends on the
installed matplotlib version and on whether the installed Pillow was built with
Raqm, which is a property of the wheel, not of the version number.

So a reader who finds a finding has an immediate second question, and until now
the honest answer was "go and check five things yourself". This answers it:

    arabic-lint --doctor

It imports nothing at module load. Every renderer is optional, the package has no
dependencies, and a missing library is a legitimate answer rather than a crash.
"""
from __future__ import annotations

from dataclasses import dataclass

# matplotlib gained libraqm shaping in 3.11 (matplotlib#30000). At or above this,
# pre-shaping is applied a second time and the text renders reversed.
MPL_SHAPING_FROM = (3, 11)


@dataclass
class Check:
    name: str
    installed: bool
    version: str | None
    shapes: bool | None      # None = installed but undeterminable
    detail: str

    @property
    def verdict(self) -> str:
        if not self.installed:
            return "not installed"
        if self.shapes is None:
            return "could not determine"
        return "pre-shaping BREAKS text here" if self.shapes else "pre-shaping is required here"


def _parse(v: str) -> tuple[int, ...]:
    """Leading numeric components only. '3.11.0rc1' -> (3, 11, 0)."""
    out: list[int] = []
    for part in v.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def check_matplotlib() -> Check:
    try:
        import matplotlib
    except Exception:
        return Check("matplotlib", False, None, None,
                     "not installed, so nothing here applies to it")
    v = getattr(matplotlib, "__version__", "")
    parsed = _parse(v)
    if not parsed:
        return Check("matplotlib", True, v or "unknown", None,
                     "version string could not be parsed")
    shapes = parsed >= MPL_SHAPING_FROM
    detail = ("3.11 and later shape text with libraqm and apply bidi themselves"
              if shapes else
              "before 3.11 matplotlib does no shaping, so the recipe is doing necessary work")
    return Check("matplotlib", True, v, shapes, detail)


def check_pillow() -> Check:
    try:
        import PIL
        from PIL import features
    except Exception:
        return Check("Pillow", False, None, None,
                     "not installed, so nothing here applies to it")
    v = getattr(PIL, "__version__", "unknown")
    try:
        raqm = bool(features.check("raqm"))
    except Exception as exc:
        return Check("Pillow", True, v, None, f"could not query Raqm support: {exc}")
    detail = ("this build has Raqm, so ImageDraw.text() shapes and reorders for you. "
              "Note this is a property of the BUILD: the same version on another "
              "machine can answer differently"
              if raqm else
              "this build has no Raqm, so ImageDraw.text() draws codepoints in order "
              "and the recipe is doing necessary work")
    return Check("Pillow", True, v, raqm, detail)


def check_recipe_libs() -> list[Check]:
    out = []
    for mod, label in (("arabic_reshaper", "arabic-reshaper"), ("bidi", "python-bidi")):
        try:
            m = __import__(mod)
            out.append(Check(label, True, getattr(m, "__version__", "unknown"), None,
                             "installed"))
        except Exception:
            out.append(Check(label, False, None, None, "not installed"))
    return out


def report() -> tuple[list[str], bool]:
    """Return (lines, any_renderer_breaks)."""
    mpl, pil = check_matplotlib(), check_pillow()
    libs = check_recipe_libs()

    lines = ["arabic-lint --doctor", ""]
    lines.append("What this environment does with pre-shaped Arabic:")
    lines.append("")
    for c in (mpl, pil):
        ver = f" {c.version}" if c.version else ""
        lines.append(f"  {c.name}{ver}")
        lines.append(f"      {c.verdict}")
        lines.append(f"      {c.detail}")
        lines.append("")

    have_recipe = [c for c in libs if c.installed]
    if have_recipe:
        lines.append("  the recipe itself: " + ", ".join(f"{c.name} {c.version}" for c in have_recipe))
    else:
        lines.append("  the recipe itself: arabic-reshaper and python-bidi are not installed here")
    lines.append("")

    breaks = [c for c in (mpl, pil) if c.shapes is True]
    safe = [c for c in (mpl, pil) if c.shapes is False]

    if breaks:
        names = " and ".join(c.name for c in breaks)
        lines.append(f"Verdict: on {names}, remove the reshape/bidi step and pass the")
        lines.append("logical string straight through. Leaving it in reverses the text, silently.")
    if safe:
        names = " and ".join(c.name for c in safe)
        lines.append(f"Verdict: on {names}, keep the reshape/bidi step. Removing it")
        lines.append("would leave the letters disjointed and in the wrong order.")
    if not breaks and not safe:
        lines.append("Verdict: neither renderer is installed here, so there is nothing to say.")
    if breaks and safe:
        lines.append("")
        lines.append("Both are present and they disagree, so the correct code differs per")
        lines.append("renderer in this one environment. Do not share a single helper between them.")

    return lines, bool(breaks)
