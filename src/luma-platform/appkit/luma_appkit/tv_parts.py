# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the shared top bar, tabs, buttons and the recessed field.

`TvTopBar` is the bar every TV screen shares with the shell's TV home, so its
tabs and clock sit at exactly the same place everywhere: pin it at
`TvTopBar.PIN` (96 in from the sides, 56 down, 74 tall). The left is a
recessed track of tabs; the focused tab is the bright key and the open tab is
a chip whenever the focus is elsewhere (another tab or the page), so exactly one
thing is bright.
The right holds the clock (28/700, tabular figures) in the bar's cluster: the
remote reaches it (Up to the bar, Right past the tabs) and OK opens the
shell's quick panel over the app, as on the TV home. `TvFocus` adopts every
bar's cluster by itself; an app registers nothing. The cluster is a key with
the home's padding while focused; its clock never moves.

`TvButton` is the 66-tall button (chip at rest, key when focused).
`TvField` is the recessed text field (a real Gtk.Text: keyboards type into it).
`TvSwitchRow` is a row with a title and a switch at its end; OK flips it.
`TvActionBar` is the TV action bar (tv.action_bar): the desktop's floating bar
grown for the couch, centred on the bottom safe line, its items quiet until the
remote reaches one (then the key).
"""
from __future__ import annotations

import weakref
from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk  # noqa: E402

from . import icons  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["TvTopBar", "TvTab", "TvButton", "TvField", "TvSwitchRow", "TvActionBar", "top_bars"]

TV = tokens.TV
# Every TV bar alive, for TvFocus to adopt its cluster.
_BARS: "weakref.WeakSet[TvTopBar]" = weakref.WeakSet()


def top_bars() -> list["TvTopBar"]:
    """The TV bars alive (TvFocus adopts the clusters of those inside its root)."""
    return list(_BARS)


class TvTab(Gtk.Box):
    """One tab in the top bar's track: an optional lead (icon name or widget) and a label."""

    __gtype_name__ = "LumaUITvTab"

    def __init__(self, label: str = "", *, icon: str | None = None, lead: Gtk.Widget | None = None,
                 accessible: str | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.TAB)
        self.add_css_class("tv-tab")
        inner = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
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
        # The cluster: what the bar's right holds (the clock), the quick panel's bar item.
        self.cluster = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.BUTTON)
        self.cluster.add_css_class("tv-cluster")
        self.cluster.update_property([Gtk.AccessibleProperty.LABEL], ["Quick panel"])
        self.end.append(self.cluster)
        # weather | time, as the TV home's bar: Home's reading, published by
        # the shell-state service; hidden until there is one.
        self.weather = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.weather.add_css_class("tv-weather")
        self.weather_icon = Gtk.Image()
        self.weather_label = Gtk.Label(valign=Gtk.Align.CENTER)
        self.weather_label.add_css_class("tv-clock")
        self.weather.append(self.weather_icon)
        self.weather.append(self.weather_label)
        self.divider = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.divider.add_css_class("tv-divider")
        self.cluster.append(self.weather)
        self.cluster.append(self.divider)
        self.clock = Gtk.Label(valign=Gtk.Align.CENTER)
        self.clock.add_css_class("tv-clock")
        self.cluster.append(self.clock)
        self.cluster.set_visible(clock)
        self.clock.set_visible(clock)
        _BARS.add(self)
        self.tabs: list[TvTab] = []
        self.open = -1
        self._tick_source = 0
        self._interface = _interface_settings()
        if clock:
            self._tick()
            self.connect("map", lambda *_a: self._start_clock())
            self.connect("unmap", lambda *_a: self._stop_clock())
            _weather_feed().watch(self)

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
        """One bright thing: the focused tab is the key; the open tab is a chip whenever it lacks the focus."""
        for index, tab in enumerate(self.tabs):
            _set(tab, "tv-key", tab.focused)
            _set(tab, "tv-chip", index == self.open and not tab.focused)
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
        self.show_weather(_weather_feed().reading)
        return True

    def show_weather(self, reading: dict) -> None:
        value = (reading or {}).get("value", "")
        self.weather.set_visible(bool(value))
        self.divider.set_visible(bool(value))
        if not value:
            return
        icon, tint = weather_glyph(reading.get("description", ""), GLib.DateTime.new_now_local().get_hour())
        self.weather_icon.set_from_icon_name(icon)
        for name in ("tv-weather-sun", "tv-weather-moon"):
            self.weather_icon.remove_css_class(name)
        if tint:
            self.weather_icon.add_css_class(f"tv-weather-{tint}")
        self.weather_label.set_label(value)
        description = reading.get("description", "")
        self.weather.update_property([Gtk.AccessibleProperty.LABEL],
                                     [f"{value}, {description}" if description else value])


def weather_glyph(condition: str, hour: int) -> tuple[str, str | None]:
    """The weather's icon and tint, as the TV home's bar draws it (tvModel.js weatherGlyph)."""
    day = 6 <= hour < 19
    text = (condition or "").lower()
    if "thunder" in text:
        return "lumaui-cloud-lightning-symbolic", None
    if "snow" in text or "sleet" in text:
        return "lumaui-snowflake-symbolic", None
    if "rain" in text:
        return "lumaui-cloud-rain-symbolic", None
    if "fog" in text:
        return "lumaui-cloud-fog-symbolic", None
    if text.startswith("cloudy"):
        return "lumaui-cloud-symbolic", None
    if "partly" in text or "fair" in text:
        return ("lumaui-cloud-sun-symbolic", "sun") if day else ("lumaui-cloud-moon-symbolic", "moon")
    return ("lumaui-sun-symbolic", "sun") if day else ("lumaui-moon-symbolic", "moon")


class _WeatherFeed:
    """Home's weather from the shell-state service (org.project_luma.ShellState1
    Weather), shared by every bar in the app; nothing when the service or the
    reading is absent, so the bar is the clock alone."""

    def __init__(self) -> None:
        self.reading: dict = {}
        self._bars: "weakref.WeakSet[TvTopBar]" = weakref.WeakSet()
        self._proxy = None
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                                  "org.project_luma.ShellState1", "/org/project_luma/ShellState1",
                                  "org.project_luma.ShellState1", None, self._on_proxy)

    def watch(self, bar: "TvTopBar") -> None:
        self._bars.add(bar)
        bar.show_weather(self.reading)

    def _on_proxy(self, _source, result) -> None:
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return
        self._proxy.connect("g-properties-changed", lambda *_a: self._read())
        self._read()

    def _read(self) -> None:
        value = self._proxy.get_cached_property("Weather") if self._proxy else None
        self.reading = dict(value.unpack()) if value is not None else {}
        for bar in list(self._bars):
            bar.show_weather(self.reading)


_FEED: _WeatherFeed | None = None


def _weather_feed() -> _WeatherFeed:
    global _FEED
    if _FEED is None:
        _FEED = _WeatherFeed()
    return _FEED


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
                 accent: bool = False, hexpand: bool = False) -> None:
        super().__init__(hexpand=hexpand, accessible_role=Gtk.AccessibleRole.BUTTON)
        self.set_hexpand_set(True)
        self.accent = accent
        for name in ("tv-btn",) + (("tv-accent",) if accent else ("tv-chip",)) + (("tv-danger",) if danger else ()):
            self.add_css_class(name)
        # Centred in the button however wide the button is drawn (hexpand, a homogeneous row).
        inner = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, spacing=0, hexpand=True)
        if icon:
            inner.append(icons.image(icon))
        self.label = Gtk.Label(label=label)
        inner.append(self.label)
        self.append(inner)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def set_tv_focus(self, on: bool) -> None:
        _set(self, "tv-key", on)
        _set(self, "tv-chip", not on and not self.accent)


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


class TvSwitchRow(Gtk.Box):
    """A row (84 tall, radius 24, 30/600) with a switch at its end; the key with the focus.

    Register it with `TvFocus.add(row, on_activate=row.toggle)`: OK flips it (a held,
    repeating OK does not, TvFocus guards that). `on_toggle(active)` follows every flip.
    The switch is the TV one: a 72 x 42 well, a 34 knob, the accent when on.
    """

    __gtype_name__ = "LumaUITvSwitchRow"

    def __init__(self, title: str, *, active: bool = False, on_toggle=None) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.SWITCH)
        self.add_css_class("tv-row")
        self.add_css_class("tv-switch-row")
        self.title = Gtk.Label(label=title, xalign=0, hexpand=True, wrap=True)
        self.title.add_css_class("tv-row-title")
        self.append(self.title)
        self.switch = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.switch.add_css_class("tv-switch")
        knob = Gtk.Box(valign=Gtk.Align.CENTER)
        knob.add_css_class("tv-switch-knob")
        self.switch.append(knob)
        self.append(self.switch)
        self._on_toggle = on_toggle
        self._active = False
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.set_active(active)

    def get_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        _set(self.switch, "on", self._active)
        self.update_state([Gtk.AccessibleState.CHECKED],
                          [int(Gtk.AccessibleTristate.TRUE if self._active else Gtk.AccessibleTristate.FALSE)])

    def toggle(self) -> None:
        self.set_active(not self._active)
        if self._on_toggle is not None:
            self._on_toggle(self._active)

    def set_tv_focus(self, on: bool) -> None:
        _set(self, "tv-key", on)


class TvActionBar(Gtk.Box):
    """The TV action bar: a bar-material panel of items, centred on the bottom safe line.

    The desktop's floating action bar, grown for the couch (Design Center
    mockups/tv/lumaui-tv.css `.tv-ab`; sizes `tv.action_bar`). Items are quiet
    at rest and the bright key while the remote is on one; `add_divider()`
    separates groups. Place it with `stage.place(bar, **TvActionBar.PIN)` and
    register each item with `TvFocus.add(item, on_activate=...)`; while it is
    placed the stage is docked "action-bar", so a toast rises above it.

        bar = TvActionBar("Terminal")
        focus.add(bar.add_item("New session", icon="plus"), on_activate=new_session)
        bar.add_divider()
        focus.add(bar.add_item(icon="arrow-up", label_for="Up"), on_activate=up)
    """

    __gtype_name__ = "LumaUITvActionBar"

    #: Where every TV screen pins its action bar (TvStage.place(bar, **TvActionBar.PIN)).
    PIN = {"bottom": TV["action_bar"]["bottom"], "center_x": True, "height": TV["action_bar"]["height"]}

    def __init__(self, label: str = "") -> None:
        super().__init__(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.TOOLBAR)
        for name in ("tv-ab", "tv-bar"):
            self.add_css_class(name)
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.connect("realize", lambda *_a: self._dock(True))
        self.connect("unrealize", lambda *_a: self._dock(False))

    def _dock(self, on: bool) -> None:
        stage = self.get_parent()
        if stage is None or not hasattr(stage, "docked"):
            return
        if on and stage.docked == "":
            stage.docked = "action-bar"
        elif not on and stage.docked == "action-bar":
            stage.docked = ""

    def add_item(self, label: str = "", *, icon: str | None = None, label_for: str = "",
                 mono: bool = False) -> Gtk.Box:
        """An item: an icon, a label or both. `label_for` names an icon-only item for
        accessibility; `mono` sets a key's label in the monospace face (esc, tab, ⌃C)."""
        item = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.BUTTON)
        item.add_css_class("tv-ab-item")
        if not label:
            item.add_css_class("tv-ab-icon")
        if mono:
            item.add_css_class("tv-ab-mono")
        inner = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
        if icon:
            inner.append(icons.image(icon))
        item.label = Gtk.Label(label=label, visible=bool(label))
        inner.append(item.label)
        item.append(inner)
        item.update_property([Gtk.AccessibleProperty.LABEL], [label or label_for])
        item.set_tv_focus = lambda on, i=item: _set(i, "tv-key", on)
        self.append(item)
        return item

    def add_divider(self) -> Gtk.Widget:
        divider = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.SEPARATOR)
        divider.add_css_class("tv-ab-divider")
        self.append(divider)
        return divider

    def items(self) -> list[Gtk.Widget]:
        found, child = [], self.get_first_child()
        while child is not None:
            if child.has_css_class("tv-ab-item"):
                found.append(child)
            child = child.get_next_sibling()
        return found
