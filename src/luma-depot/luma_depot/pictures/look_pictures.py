# SPDX-License-Identifier: Apache-2.0
"""A Look drawn from its own data (handoff 6.1, 6.2, 9.3): a theme as a small "Files" window drawn in
it, icons as four sample icons, sounds as a waveform, a font set in itself, pointers at three sizes,
D-Ink's paper and ink. The same widget is a tile's picture and, scaled up, the item page's specimen.

No logos, no trademarks: Cupertino and Redmond are only nods.
"""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import icons, lumaui

from ..model.world import Look

__all__ = ["THEMES", "specimen", "theme_window"]

THEMES = ("luma", "cupertino", "redmond", "exile", "95")


def _box(css: str, orientation: Gtk.Orientation = Gtk.Orientation.HORIZONTAL, **kwargs) -> Gtk.Box:
    box = Gtk.Box(orientation=orientation, **kwargs)
    for name in css.split():
        box.add_css_class(name)
    return box


def theme_window(theme: str) -> Gtk.Widget:
    """A title bar with its controls, a sidebar, a list of bars, Cancel and the main button."""
    if theme not in THEMES:
        theme = "luma"
    window = _box(f"dq-thm t-{theme}", Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    window.set_size_request(200, 125)
    bar = _box("tb")
    left = _box("wc l")
    right = _box("wc r")
    for side in (left, right):
        for _ in range(3):
            side.append(_box("dot"))
    title = Gtk.Label(label="Files", hexpand=True, xalign=0.5 if theme in ("cupertino", "exile", "95") else 0)
    bar.append(left)
    bar.append(title)
    bar.append(right)
    left.set_visible(theme in ("cupertino", "exile"))
    right.set_visible(theme in ("luma", "redmond", "exile", "95"))
    window.append(bar)
    body = _box("bd", vexpand=True)
    side = _box("sd", Gtk.Orientation.VERTICAL)
    for _ in range(4):
        side.append(_box("ln"))
    main = _box("mn", Gtk.Orientation.VERTICAL, hexpand=True)
    for index in range(5):
        line = _box("ln" + (" short" if index % 2 == 0 else ""))
        main.append(line)
    body.append(side)
    body.append(main)
    window.append(body)
    foot = _box("ft", halign=Gtk.Align.END)
    for word, key in (("Cancel", False), ("Open", True)):
        button = Gtk.Label(label=word)
        button.add_css_class("em")
        if key:
            button.add_css_class("k")
        foot.append(button)
    window.append(foot)
    return window


def _swatches(colours: tuple[str, ...]) -> Gtk.Widget:
    row = _box("dq-lsw", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    for colour, glyph in zip(colours, ("music", "image", "folder-open", "mail")):
        cell = _box("sw", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        cell.add_css_class("lumaui-painted")
        lumaui.paint_class(cell, colour)
        mark = icons.image(glyph)
        mark.set_hexpand(True)
        cell.append(mark)
        row.append(cell)
    return row


def _wave() -> Gtk.Widget:
    row = _box("dq-lwave", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    for index in range(28):
        bar = _box("bar", valign=Gtk.Align.CENTER, hexpand=True)
        bar.set_size_request(-1, round(60 * (0.12 + abs(math.sin(index * 1.7)) * 0.46)))
        row.append(bar)
    return row


def _font() -> Gtk.Widget:
    column = _box("dq-lfont", Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    big = Gtk.Label(label="Aa Bb")
    big.add_css_class("big")
    small = Gtk.Label(label="The quick brown fox")
    small.add_css_class("small")
    column.append(big)
    column.append(small)
    return column


def _cursors() -> Gtk.Widget:
    row = _box("dq-lcur", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    for size in (22, 32, 44):
        row.append(icons.image("mouse-pointer-2", pixel_size=size))
    return row


def _ink() -> Gtk.Widget:
    paper = _box("dq-inkp", Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    paper.append(Gtk.Label(label="Aa", xalign=0))
    for index in range(3):
        paper.append(_box("rule" + (" short" if index == 2 else "")))
    return paper


def specimen(look: Look) -> Gtk.Widget:
    """The Look's picture on its own ground (the mockup's .dq-lspec), sized for a tile."""
    ground = _box(f"dq-lspec k-{look.kind.lower().replace(' ', '')}", hexpand=True, vexpand=True)
    if look.theme:
        ground.add_css_class(f"th-{look.theme if look.theme in THEMES else 'luma'}")
    elif look.kind != "Appearance":
        _ground_hue(ground, look.hue)
    if look.theme:
        inner = theme_window(look.theme)
    elif look.swatch:
        inner = _swatches(look.swatch)
    elif look.ink:
        inner = _ink()
    elif look.kind == "Sounds":
        inner = _wave()
    elif look.kind == "Font":
        inner = _font()
    elif look.kind == "Cursor":
        inner = _cursors()
    else:
        inner = icons.image("palette", pixel_size=40)
    inner.set_hexpand(True)
    inner.set_vexpand(True)
    ground.append(inner)
    return ground


def _ground_hue(widget: Gtk.Widget, hue: float) -> None:
    """linear-gradient(150deg, oklch(.36 .07 h), oklch(.2 .04 h+50))."""
    from ..parts.face import paint_gradient
    paint_gradient(widget, (0.36, 0.07, hue), (0.2, 0.04, hue + 50), 150)
