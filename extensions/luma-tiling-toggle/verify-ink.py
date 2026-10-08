#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Measure the Tiling picker's stylesheet in all four appearance modes.

The picker sits in Quick Options' detail pane. Every layout must read as a
layout (its tile outlines 3:1 against the pane), the chosen one must stand
apart from the others (3:1 between the state slate and an unchosen tile, and
its ring 3:1 against the pane), and a display's name is text (4.5:1). Frost
and glass panes are measured over pure white and pure black as well as over
their opaque detail fill, since the pane can be either.
"""

import re
import sys
from pathlib import Path

SHEET = Path(sys.argv[1] if len(sys.argv) > 1 else
             Path(__file__).with_name("stylesheet.css"))
# The pane a detail is drawn on: Quick Options' raised fill per mode, and for
# the smoked modes the panel veil itself (SURFACE_INK veilRaised).
PANES = {
    "light": ["#ffffff", "#f1f2f3"],
    "dark": ["#2a2f36", "#21252b"],
    "frost": ["#2a2f36", "rgba(28,33,40,0.88)"],
    "glass": ["#2a2f36", "rgba(14,19,26,0.82)"],
}
COLOUR = re.compile(r"#[0-9a-fA-F]{6}|rgba?\([^)]*\)")


def rgba(text):
    text = text.strip()
    if text.startswith("#"):
        v = int(text[1:], 16)
        return ((v >> 16) & 255, (v >> 8) & 255, v & 255, 1.0)
    parts = [p.strip() for p in text[text.index("(") + 1:-1].split(",")]
    return (*(float(p) for p in parts[:3]),
            float(parts[3]) if len(parts) > 3 else 1.0)


def over(top, bottom):
    a = top[3]
    return tuple(t * a + b * (1 - a) for t, b in zip(top[:3], bottom[:3])) + (1.0,)


def lum(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])


def ratio(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


css = re.sub(r"/\*.*?\*/", "", SHEET.read_text(), flags=re.S)
rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def value(mode, selector_tail, prop):
    for head, body in rules:
        if f"luma-surface-{mode}" in head and head.strip().endswith(selector_tail):
            m = re.search(rf"(?<![-\w]){prop}\s*:\s*([^;]+);", body)
            if m:
                return rgba(COLOUR.search(m.group(1)).group(0))
    raise SystemExit(f"{mode}: no {prop} for {selector_tail}")


failures, count = [], 0
for mode, panes in PANES.items():
    tile = value(mode, ".luma-tiling-preview-tile", "background-color")
    tile_line = value(mode, ".luma-tiling-preview-tile", "border-color")
    chosen = value(mode, ".luma-tiling-preview:checked .luma-tiling-preview-tile",
                   "background-color")
    ring = COLOUR.search(dict(
        (h.strip(), b) for h, b in rules)[
        next(h.strip() for h, _ in rules
             if f"luma-surface-{mode}" in h and h.strip().endswith(".luma-tiling-preview:checked"))
    ]).group(0)
    ring = rgba(ring)
    name = value(mode, ".luma-tiling-display-name", "color")
    backdrops = [(255, 255, 255, 1.0), (0, 0, 0, 1.0)]
    for pane in panes:
        for backdrop in backdrops:
            ground = over(rgba(pane), backdrop)
            checks = {
                "tile outline vs pane": (ratio(over(tile_line, ground), ground), 3.0),
                "chosen vs other tile": (ratio(over(chosen, ground),
                                               over(tile, ground)), 3.0),
                "chosen ring vs pane": (ratio(over(ring, ground), ground), 3.0),
                "display name vs pane": (ratio(over(name, ground), ground), 4.5),
            }
            for label, (got, need) in checks.items():
                count += 1
                if got < need:
                    failures.append(f"{mode} {pane}: {label} {got:.2f} < {need}")

if failures:
    print("FAIL\n" + "\n".join(failures))
    sys.exit(1)
print(f"PASS: {count} measurements of the Tiling picker in four modes")
