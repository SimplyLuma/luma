#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Gate the kit's filled controls (ADR-043) on the tokens and the stylesheets.

Ink used as a fill is symmetric by design: white on dark, near-black on paper.
On paper every selected segment, toggled button and slider handle became a
heavy black block, several to a view. ADR-043 keeps ink for one emphasis per
view and gives every on state one quieter fill, `state`, with `state_ink` on
it and a ringed `knob` for sliders.

Quieter must not mean ambiguous, so this measures, in all four modes:
  - state against every surface it can sit on, and against the fill track a
    segment draws, at 3:1 -- the on state must stand apart from the off state;
  - state_ink on state at 4.5:1;
  - the knob's ring against the surface at 3:1;
  - the sheet colours: destructive text at 4.5:1 at rest and under the pressed
    fill, and the edited dot at 3:1.
Frost and glass are translucent, so their surfaces are measured over pure
white, mid grey and pure black, the ends any wallpaper lies between. Their
surface roles are layers: the window paints the frame, the title band and the
panes paint over it, and cards, rows and islands paint over a pane, so each
surface is measured as the stack it actually composites to. The bare
see-through frame carries nothing and is not measured.

It also fails if a :checked or :selected rule in the kit stylesheets paints
ink as a fill again, which is how the problem first spread.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("LUMA_APPKIT_BASE_PATH",
                           "appkit/luma-appkit-base.css")).parent
MODES = {"light": "luma-appkit-tokens.css",
         "dark": "luma-appkit-dark-tokens.css",
         "frost": "luma-appkit-frost-tokens.css",
         "glass": "luma-appkit-glass-tokens.css"}
# Translucent modes: light, see-through panels over the wallpaper.
SMOKED = {"frost", "glass"}
SURFACES = ("window", "content", "card")
# On frost and glass the window itself is the see-through frame, and nothing
# is drawn on it bare: controls and text sit on a pane, a card, an island or
# the title band, each painted over the layers under it. These are the stacks,
# bottom first, that are measured over white, mid grey and black.
TRANSLUCENT_STACKS = {
    "content": ("window", "content"),
    "card": ("window", "content", "card"),
    "island": ("window", "content", "island"),
    "chrome": ("window", "chrome"),
    "pane header": ("window", "content", "chrome_secondary"),
}
DEFINE = re.compile(r"@define-color\s+luma_(\w+)\s+([^;]+);")
HEX = re.compile(r"#([0-9a-fA-F]{6})\Z")
RGBA = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*"
                  r"(?:,\s*([\d.]+)\s*)?\)\Z")
WHITE = (255.0, 255.0, 255.0)
GREY = (128.0, 128.0, 128.0)
BLACK = (0.0, 0.0, 0.0)


def parse(text: str) -> tuple[tuple[float, float, float], float]:
    text = text.strip()
    m = HEX.match(text)
    if m:
        raw = int(m.group(1), 16)
        return ((raw >> 16) & 255, (raw >> 8) & 255, raw & 255), 1.0
    m = RGBA.match(text)
    if not m:
        raise ValueError(f"not a literal colour: {text!r}")
    rgb = tuple(float(m.group(i)) for i in (1, 2, 3))
    return rgb, float(m.group(4)) if m.group(4) else 1.0


def over(top: str, bottom: tuple[float, float, float]):
    rgb, alpha = parse(top)
    return tuple(c * alpha + b * (1 - alpha) for c, b in zip(rgb, bottom))


def luminance(rgb) -> float:
    def lin(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def ratio(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def tokens(mode: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in DEFINE.findall((ROOT / MODES[mode]).read_text()):
        values[name] = value.strip()
    return values


def grounds(mode: str, t: dict[str, str]):
    """Every opaque colour the mode's surfaces can composite to."""
    if mode not in SMOKED:
        for name in SURFACES:
            if name in t:
                yield name, over(t[name], WHITE)
        return
    for name, stack in TRANSLUCENT_STACKS.items():
        for backdrop in (WHITE, GREY, BLACK):
            ground = backdrop
            for layer in stack:
                ground = over(t[layer], ground)
            yield name, ground


failures: list[str] = []
report: list[str] = []


def need(label: str, value: float, minimum: float) -> None:
    report.append(f"{label:58} {value:5.2f}  (>= {minimum})")
    if value < minimum:
        failures.append(f"{label}: {value:.2f} < {minimum}")


for mode in MODES:
    t = tokens(mode)
    state = over(t["state"], WHITE)
    need(f"{mode}: state_ink on state", ratio(over(t["state_ink"], WHITE), state), 4.5)
    for name, ground in grounds(mode, t):
        need(f"{mode}: state vs {name}", ratio(state, ground), 3.0)
        need(f"{mode}: state vs {name} + fill track",
             ratio(state, over(t["fill"], ground)), 3.0)
        need(f"{mode}: knob ring vs {name}",
             ratio(over(t["knob_ring"], WHITE), ground), 3.0)
        # Sheet colours are measured where a sheet is drawn: its card, and on
        # the opaque modes the window too. On frost and glass a sheet takes
        # the menu's veil, measured with the menus below.
        if mode in SMOKED and name != "card":
            continue
        need(f"{mode}: ember dot vs {name}",
             ratio(over(t["ember_dot"], WHITE), ground), 3.0)
        # The sheet's destructive button: at rest and under the pressed fill.
        for role in ("destructive_text", "destructive_text_dim"):
            ink = over(t[role], WHITE)
            need(f"{mode}: {role} on {name}", ratio(ink, ground), 4.5)
            need(f"{mode}: {role} on {name} pressed",
                 ratio(ink, over(t["pressed"], ground)), 4.5)

# Menus: a section heading or description and a shortcut cap are text (4.5:1),
# a disabled row reads as disabled at 3:1, on the menu and on its hover row.
for mode in MODES:
    t = tokens(mode)
    backdrops = (WHITE, GREY, BLACK) if mode in SMOKED else (WHITE,)
    for backdrop in backdrops:
        menu = over(t["menu"], backdrop)
        raised = over(t["menu_raised"], menu)
        for name, ground in (("menu", menu), ("hover row", raised)):
            need(f"{mode}: menu heading on {name}",
                 ratio(over(t["menu_heading"], ground), ground), 4.5)
            cap = over(t["passive"], ground)
            need(f"{mode}: shortcut cap text on {name}",
                 ratio(over(t["menu_shortcut"], cap), cap), 4.5)
            need(f"{mode}: disabled row on {name}",
                 ratio(over(t["menu_disabled"], ground), ground), 3.0)

# Sidebar and section headings (LIBRARY, CALENDARS): 9px text, so 4.5:1 on
# every surface a sidebar or section can be, and under a hover wash.
for mode in MODES:
    t = tokens(mode)
    for name, ground in grounds(mode, t):
        for label, g in ((name, ground), (name + " + hover", over(t["hover"], ground))):
            need(f"{mode}: section label on {label}",
                 ratio(over(t["section_label"], g), g), 4.5)

# Ink as a fill is the view's one emphasis (a primary button, play), never a
# state: no :checked or :selected rule may paint it.
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
for sheet in ("luma-appkit-base.css", "luma-appkit.css"):
    text = re.sub(r"/\*.*?\*/", "", (ROOT / sheet).read_text(), flags=re.S)
    for selectors, body in RULE.findall(text):
        if not re.search(r"background(-color)?\s*:\s*@luma_ink\s*;", body):
            continue
        for selector in (s.strip() for s in selectors.split(",")):
            if ":checked" in selector or ":selected" in selector:
                failures.append(f"{sheet}: {selector} fills a state with ink")

print("\n".join(report))
if failures:
    print("\nFAIL\n" + "\n".join(failures))
    sys.exit(1)
print(f"\nfilled controls: {len(report)} measurements passed in four modes")
