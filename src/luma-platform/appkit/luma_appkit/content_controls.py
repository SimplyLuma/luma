# SPDX-License-Identifier: Apache-2.0
"""LumaUI controls: the text button (v70 `.bt`) and the switch (v70 `.cfsw`).

    TextButton("Share", icon="share-2")                 # plain: ink, fill-1 on hover
    TextButton("Change…", style="fill", small=True)     # .bt.fill.sm
    TextButton("Keep", style="key")                     # the key
    TextButton("Sign out…", danger=True)                # red ink
    TextButton("Forget", style="danger")                # the red key (v70 .cfwin .bt.danger)
    Switch(active=True, on_toggle=set_wifi)             # 38x22; big=True is 44x26

Twins: C luma_text_button_new() and luma_switch_new().
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import icons  # noqa: E402

__all__ = ["ICON_BUTTON_SIZES", "IconOnlyButton", "Switch", "TEXT_BUTTON_STYLES", "TextButton"]

ICON_BUTTON_SIZES = ("regular", "row", "small", "large")

TEXT_BUTTON_STYLES = ("plain", "fill", "key", "danger", "raised")


class TextButton(Gtk.Button):
    """Words, with a glyph before them when `icon` is given (v70 .bt)."""

    __gtype_name__ = "LumaUITextButton"

    def __init__(self, label: str, *, icon: str | None = None, style: str = "plain", small: bool = False,
                 danger: bool = False, on_click: Callable[[], None] | None = None, sensitive: bool = True,
                 hero: bool = False, size: str = "regular") -> None:
        if size not in ("regular", "small", "hero", "large", "touch"):
            raise ValueError("text button size is regular, small, hero, large or touch")
        if size != "regular" and (small or hero):
            raise ValueError("use size or the legacy small/hero flags, not both")
        small, hero = small or size == "small", hero or size == "hero"
        if style not in TEXT_BUTTON_STYLES:
            raise ValueError(f"style must be one of {', '.join(TEXT_BUTTON_STYLES)}, not {style!r}")
        super().__init__(valign=Gtk.Align.CENTER, sensitive=sensitive)
        self.add_css_class("lumaui-text-button")
        if size in ("large", "touch"):
            self.add_css_class(size)
        if style == "danger":
            self.add_css_class("danger-solid")
        elif style != "plain":
            self.add_css_class(style)
        if danger:
            self.add_css_class("danger")
        if small:
            self.add_css_class("small")
        if hero:  # a header's or hero's button: 34, 11 round (v70 .cfnowa .bt)
            self.add_css_class("hero")
        self.label_widget = Gtk.Label(label=label)
        if icon:
            line = Gtk.Box(halign=Gtk.Align.CENTER)
            line.add_css_class("lumaui-text-button-content")
            line.append(icons.image(icon))
            line.append(self.label_widget)
            self.set_child(line)
        else:
            self.set_child(self.label_widget)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if on_click is not None:
            self.connect("clicked", lambda _b: on_click())


class IconOnlyButton(Gtk.Button):
    """A glyph alone (v70 .ib): `label` names it for screen readers and its tooltip. `size` is
    "regular" (36, an 18 glyph), "row" (28 with 14: a copy key in a settings row) "small" (26 with 15) or "large" (40 with 18)."""

    __gtype_name__ = "LumaUIIconOnlyButton"

    def __init__(self, icon: str, label: str, *, size: str = "regular", on_click: Callable[[], None] | None = None,
                 sensitive: bool = True, active: bool | None = None, raised: bool = False) -> None:
        if size not in ICON_BUTTON_SIZES:
            raise ValueError(f"size must be one of {', '.join(ICON_BUTTON_SIZES)}, not {size!r}")
        super().__init__(valign=Gtk.Align.CENTER, sensitive=sensitive, tooltip_text=label)
        self.add_css_class("lumaui-icon-only")
        self.set_raised(raised)
        if size != "regular":
            self.add_css_class(size)
        self.icon = icon
        self.set_child(icons.image(icon))
        if active is not None:
            self.set_active(active)
        self.remove_css_class("image-button")  # a LumaUI part, not the legacy icon-button look
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if on_click is not None:
            self.connect("clicked", lambda _b: on_click())

    def set_raised(self, raised: bool) -> None:
        """Use the neutral dimensional surface shared with stacked actions."""
        (self.add_css_class if raised else self.remove_css_class)("raised")

    def set_active(self, active: bool) -> None:
        """Expose an app-owned toggle state; a loved heart uses its filled glyph and ink."""
        (self.add_css_class if active else self.remove_css_class)("on")
        self.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE if active else Gtk.AccessibleTristate.FALSE)])
        if self.icon == "heart":
            self.add_css_class("love")
            self.set_child(icons.image("heart-filled" if active else "heart"))


class Switch(Gtk.Switch):
    """An on/off switch: the pill and its round knob (v70 .cfsw); `big` is v70 .cfsw.big."""

    __gtype_name__ = "LumaUISwitch"

    def __init__(self, *, active: bool = False, big: bool = False,
                 on_toggle: Callable[[bool], None] | None = None, label: str | None = None) -> None:
        super().__init__(active=active, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.add_css_class("lumaui-switch")
        if big:
            self.add_css_class("big")
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if on_toggle is not None:
            self.connect("notify::active", lambda s, _p: on_toggle(s.get_active()))
