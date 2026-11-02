# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the shared top bar, tabs, buttons and the recessed field.

`TvTopBar` is the bar every TV screen shares with the shell's TV home, so its
tabs and clock sit at exactly the same place everywhere: pin it at
`TvTopBar.PIN` (96 in from the sides, 56 down, 74 tall). The left is a
recessed track of tabs; the open tab is the bright key, and it steps back to
a chip only while the remote is on another bar item, so one thing is bright.
The right holds the clock (28/700, tabular figures).

`TvButton` is the 66-tall button (chip at rest, key when focused).
`TvField` is the recessed text field (a real Gtk.Text: keyboards type into it).
"""
from __future__ import annotations

from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk  # noqa: E402

from . import icons  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["TvTopBar", "TvTab", "TvButton", "TvField"]

TV = tokens.TV


class TvTab(Gtk.Box):
    """One tab in the top bar's track: an optional lead (icon name or widget) and a label."""

    __gtype_name__ = "LumaUITvTab"

    def __init__(self, label: str = "", *, icon: str | None = None, lead: Gtk.Widget | None = None,
                 accessible: str | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.TAB)
        self.add_css_class("tv-tab")
        inner = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
        self.append(inner)
        self.lead = lead if lead is not None else (icons.image(icon) if icon else None)
        if self.lead is not None:
            self.lead.set_valign(Gtk.Align.CENTER)
            inner.append(self.lead)
        self.label = Gtk.Label(label=label, visible=bool(label))
        inner.append(self.label)
        self.update_property([Gtk.AccessibleProperty.LABEL], [accessible or label])
        self._bar: TvTopBar | None = None
        self.focused = False

    def set_label(self, text: str) -> None:
        self.label.set_label(text)
        self.label.set_visible(bool(text))

    def set_tv_focus(self, on: bool) -> None:
        self.focused = on
        if self._bar is not None:
            self._bar.repaint()


class TvTopBar(Gtk.Box):
    """The TV top bar: a track of tabs on the left, the clock on the right."""

    __gtype_name__ = "LumaUITvTopBar"

    #: Where every TV screen pins its bar (TvStage.place(bar, **TvTopBar.PIN)).
    PIN = {"left": TV["layout"]["safe_x"], "right": TV["layout"]["safe_x"],
           "top": TV["bar"]["top"], "height": TV["bar"]["height"]}

    def __init__(self, *, clock: bool = True) -> None:
        super().__init__(valign=Gtk.Align.FILL)
        self.add_css_class("tv-topbar")
        self.track = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.TAB_LIST)
        self.track.add_css_class("tv-track")
        self.track.add_css_class("tv-well")
        self.append(self.track)
        self.append(Gtk.Box(hexpand=True))
        self.end = Gtk.Box(valign=Gtk.Align.CENTER)
        self.append(self.end)
        self.clock = Gtk.Label(valign=Gtk.Align.CENTER)
        self.clock.add_css_class("tv-clock")
        self.end.append(self.clock)
        self.clock.set_visible(clock)
        self.tabs: list[TvTab] = []
        self.open = -1
        self._tick_source = 0
        self._interface = _interface_settings()
        if clock:
            self._tick()
            self.connect("map", lambda *_a: self._start_clock())
            self.connect("unmap", lambda *_a: self._stop_clock())

    def set_tabs(self, tabs: Sequence[TvTab]) -> None:
        while child := self.track.get_first_child():
            if isinstance(child, TvTab):
                child._bar = None
            self.track.remove(child)
        self.tabs = list(tabs)
        for tab in self.tabs:
            tab._bar = self
            self.track.append(tab)
        self.repaint()

    def set_open(self, index: int) -> None:
        self.open = index
        self.repaint()

    def repaint(self) -> None:
        """The materials rule: focus is the key; the open tab is the key unless another tab has the focus."""
        other = any(t.focused for i, t in enumerate(self.tabs) if i != self.open)
        for index, tab in enumerate(self.tabs):
            key = tab.focused or (index == self.open and not other)
            chip = index == self.open and not key
            _set(tab, "tv-key", key)
            _set(tab, "tv-chip", chip)
            tab.update_state([Gtk.AccessibleState.SELECTED], [int(index == self.open)])

    # the clock follows the desktop's 12/24-hour choice
    def _start_clock(self) -> None:
        if not self._tick_source:
            self._tick()
            self._tick_source = GLib.timeout_add_seconds(5, self._tick)

    def _stop_clock(self) -> None:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0

    def _tick(self) -> bool:
        self.clock.set_label(clock_text())
        return True


def _interface_settings():
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.interface", True) if source else None
    return Gio.Settings.new("org.gnome.desktop.interface") if schema and schema.has_key("clock-format") else None


def clock_text(now: GLib.DateTime | None = None) -> str:
    """The bar's clock: "6:13 PM", or "18:13" when the desktop uses 24-hour time."""
    now = now or GLib.DateTime.new_now_local()
    settings = _interface_settings()
    if settings is not None and settings.get_string("clock-format") == "24h":
        return now.format("%H:%M")
    hour = now.get_hour() % 12 or 12
    return f"{hour}:{now.get_minute():02d} {'AM' if now.get_hour() < 12 else 'PM'}"


def _set(widget: Gtk.Widget, name: str, on: bool) -> None:
    if on:
        widget.add_css_class(name)
    else:
        widget.remove_css_class(name)


class TvButton(Gtk.Box):
    """The TV button: 66 tall, radius 22, 25/650; chip at rest, the key with the focus.

    `danger=True` colours the label for a destructive action (Remove).
    Register it with `TvFocus.add(button, on_activate=...)`.
    """

    __gtype_name__ = "LumaUITvButton"

    def __init__(self, label: str, *, icon: str | None = None, danger: bool = False,
                 hexpand: bool = False) -> None:
        super().__init__(hexpand=hexpand, accessible_role=Gtk.AccessibleRole.BUTTON)
        for name in ("tv-btn", "tv-chip") + (("tv-danger",) if danger else ()):
            self.add_css_class(name)
        inner = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True, spacing=0)
        if icon:
            inner.append(icons.image(icon))
        self.label = Gtk.Label(label=label)
        inner.append(self.label)
        self.append(inner)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def set_tv_focus(self, on: bool) -> None:
        _set(self, "tv-key", on)
        _set(self, "tv-chip", not on)


class TvField(Gtk.Box):
    """A recessed text field for TV: a lead icon and a real `Gtk.Text` (`field.text`).

    Register `field.text` with `TvFocus.add(..., editable=True)`: while it has
    the remote's focus it holds GTK focus, so a real keyboard types straight
    in and the system's TV keyboard presents itself for it. `action` labels
    the keyboard's action key ("Search", "Add"; tv_keyboard.set_keyboard_action).
    The field draws its ring while focus is inside it. `changed(text)` follows
    every edit.
    """

    __gtype_name__ = "LumaUITvField"

    def __init__(self, *, placeholder: str = "", icon: str | None = "search",
                 purpose: Gtk.InputPurpose = Gtk.InputPurpose.FREE_FORM,
                 action: str | None = None) -> None:
        super().__init__(valign=Gtk.Align.START)
        self.add_css_class("tv-field")
        self.add_css_class("tv-well")
        if icon:
            lead = icons.image(icon)
            lead.set_valign(Gtk.Align.CENTER)
            self.append(lead)
        self.text = Gtk.Text(placeholder_text=placeholder, hexpand=True, valign=Gtk.Align.CENTER,
                             input_purpose=purpose)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], [placeholder])
        self.append(self.text)
        if action:
            from .tv_keyboard import set_keyboard_action
            set_keyboard_action(self.text, action)

    def get_value(self) -> str:
        return self.text.get_text()

    def set_value(self, value: str) -> None:
        self.text.set_text(value)
        self.text.set_position(-1)
