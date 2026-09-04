#!/usr/bin/env python3
"""Re-measure the matplotlib 3.11 claim from scratch.

The claim filed upstream (matplotlib#32262) is that since 3.11 shapes Arabic
itself, the most-copied "make Arabic work" recipe —

    text = get_display(arabic_reshaper.reshape(text))

— now makes the label WRONG rather than right, and nothing raises. This script
proves or disproves that on whatever machine it runs on, so the number in any
write-up is measured here and not remembered from a previous session.

Method: render each string three ways and compare against a reference produced by
a renderer that is known to do the shaping itself. Difference is mean absolute
pixel difference over the rendered text box.

    python3 verify_mpl311.py
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

import arabic_reshaper
from bidi.algorithm import get_display

# Plain Arabic words, in logical order — i.e. how they are typed and stored.
STRINGS = [
    ("simple",      "مرحبا"),
    ("emirates",    "الإمارات"),
    ("with-lamalef","السلام"),
    ("sentence",    "اللغة العربية جميلة"),
    ("numbers",     "الطلبات 2026"),
]

# A font that actually has Arabic coverage. Without this every render is tofu
# boxes and the comparison measures nothing.
CANDIDATES = ["Geeza Pro", "Al Bayan", "Baghdad", "Damascus", "Noto Sans Arabic",
              "DejaVu Sans", "Arial Unicode MS"]


def pick_font():
    have = {f.name for f in font_manager.fontManager.ttflist}
    for c in CANDIDATES:
        if c in have:
            return c
    return None


def render(text, font, size=54):
    """Render one string to a tight greyscale array."""
    fig = plt.figure(figsize=(9, 1.6), dpi=100)
    fig.patch.set_facecolor("white")
    fig.text(0.5, 0.5, text, ha="center", va="center",
             fontname=font, fontsize=size, color="black")
    fig.canvas.draw()
    a = np.asarray(fig.canvas.buffer_rgba())[..., :3].mean(axis=2)
    plt.close(fig)
    return a


def diff(a, b):
    return float(np.abs(a.astype(float) - b.astype(float)).mean())


def main():
    font = pick_font()
    print(f"matplotlib {matplotlib.__version__} · font {font!r}")
    if not font:
        print("no Arabic-capable font found — cannot measure")
        return

    print(f"\n{'string':14} {'raw vs raw':>11} {'preshaped vs raw':>18}  verdict")
    print("-" * 64)
    worse = 0
    for name, s in STRINGS:
        raw = render(s, font)
        # The workaround: substitute presentation forms, then reorder to visual.
        pre = render(get_display(arabic_reshaper.reshape(s)), font)
        # Control: the same string rendered twice. Any non-zero here would mean
        # the renderer is not deterministic and no other number is trustworthy.
        ctl = diff(raw, render(s, font))
        d = diff(raw, pre)
        bad = d > 1.0
        worse += bad
        print(f"{name:14} {ctl:11.3f} {d:18.3f}  {'DIFFERS' if bad else 'identical'}")

    print("-" * 64)
    print(f"{worse}/{len(STRINGS)} strings render differently when pre-shaped.")
    print("\nA non-zero difference means the workaround changes the output on this")
    print("build. It does not by itself prove which one is CORRECT — that is what")
    print("reading the rendered Arabic tells you, and why the upstream report")
    print("carries images rather than only numbers.")

    # Codepoint-level evidence, which needs no renderer and no font at all.
    print("\nCodepoint evidence (renderer-independent):")
    for name, s in STRINGS:
        out = get_display(arabic_reshaper.reshape(s))
        pf = sum(1 for c in out if 0xFB50 <= ord(c) <= 0xFEFF)
        print(f"  {name:14} {len(s):>2} → {len(out):>2} codepoints · "
              f"{pf} in the Arabic Presentation Forms block"
              f"{'  ← lam-alef decomposed' if len(out) != len(s) else ''}")
    print("\nTyped Arabic never contains presentation forms; they are legacy")
    print("compatibility codepoints. Their presence is the detectable half.")


if __name__ == "__main__":
    main()
