# SPDX-License-Identifier: Apache-2.0
"""LumaUI action: stacked buttons.

Icon on top, label below, on the raised chip (the Contacts action look), in
equal pairs or trios at the end of a pane. `small=True` for tight panes. A
danger button is red and still asks (DestructiveDialog) before it acts.
Hover lifts, press sinks, keyboard focus rings, disabled fades in place.
Never mixed with inline buttons in one row. v70 `lStack(items, 'sm')`.

    row = StackedButtons([
        StackedButton("log-out", "Leave", on_click=leave),
        StackedButton("trash-2", "Delete", danger=True, on_click=ask_delete),
    ])
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from . import icons  # noqa: E402

__all__ = ["StackedButton", "StackedButtons"]


class StackedButton(Gtk.Button):
    """One action: a Lucide glyph over a short label."""

    __gtype_name__ = "LumaUIStackedButton"

    def __init__(self, icon: str, label: str, *, danger: bool = False,
                 on_click: Callable[[], None] | None = None, sensitive: bool = True) -> None:
        super().__init__(hexpand=True, sensitive=sensitive)
        self.add_css_class("lumaui-stacked-button")
        if danger:
            self.add_css_class("danger")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.add_css_class("lumaui-stacked-content")
        glyph = icons.image(icon)
        glyph.add_css_class("lumaui-stacked-icon")
        glyph.set_halign(Gtk.Align.CENTER)
        # A short label always fits (its width is asked for); a long one ellipsizes.
        text = Gtk.Label(label=label, ellipsize=Pango.EllipsizeMode.END,
                         width_chars=min(len(label), 14), max_width_chars=14)
        text.add_css_class("lumaui-stacked-label")
        box.append(glyph)
        box.append(text)
        self.set_child(box)
        if len(label) > 14:
            self.set_tooltip_text(label)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.danger = danger
        if on_click is not None:
            self.connect("clicked", lambda _b: on_click())


class StackedButtons(Gtk.Box):
    """An equal pair or trio of StackedButton; anything else is refused."""

    __gtype_name__ = "LumaUIStackedButtons"

    def __init__(self, buttons: Sequence[StackedButton], *, small: bool = False, size: str | None = None) -> None:
        """`size="tile"` (v71 Calendar's event view, `.cevtiles`): up to four equal tiles, 64 tall at 16 on the
        raised chip, a 20 glyph over 12.5 words, 8 apart."""
        if size not in (None, "tile"):
            raise ValueError('stacked buttons are the default size or "tile"')
        if len(buttons) not in ((2, 3, 4) if size == "tile" else (2, 3)):
            raise ValueError("stacked buttons come in equal pairs or trios (tiles: up to four)")
        if not all(isinstance(b, StackedButton) for b in buttons):
            raise TypeError("a row of stacked buttons holds only StackedButton")
        # Spacing is the stack token (border-spacing in the sheet), not a number here.
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.add_css_class("lumaui-stacked-buttons")
        if size == "tile":
            self.add_css_class("tile")
        if small:
            self.add_css_class("lumaui-stack-small")  # not "small": Adw.Clamp puts .small on its child at narrow widths
        for button in buttons:
            if size == "tile":
                # Tiles share a narrow pane: labels may ellipsize instead of
                # imposing the standalone button's character-count minimum.
                content = button.get_child()
                content.set_halign(Gtk.Align.FILL)
                label = content.get_last_child()
                label.set_width_chars(-1)
                label.set_xalign(0.5)
            self.append(button)
