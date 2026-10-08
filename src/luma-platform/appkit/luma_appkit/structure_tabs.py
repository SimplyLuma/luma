# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the tab bar for parallel destinations (v71 phone.js `tabBar`, `.fbar.phtabs`).

Three or more peer places (Clock's World, Alarms, Stopwatch, Timer; Phone's
Favorites, Recents, Contacts, Keypad) are tabs on a phone: one full-width bar
16 from each side, each place an icon over its name, 56 tall, the chosen one
lit on a quiet fill. `compact=True` is the icon-only row that sits inside an
action bar (v71 Clock `.cktabbar`): 48 tall tabs, the chosen one the raised
chip with its glyph lit, a count as a corner badge and a green dot while
something runs.

    tabs = TabBar([("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock"),
                   ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass")],
                  current="world", on_change=self.show_place)
    tabs.float_over(page)            # full width at the foot (a phone)
    tabs.set_count("alarms", 3)
    tabs.set_status("stopwatch", "running")

`TabBar.wants_tabs(places)` says whether a place switch qualifies (3 or more,
each with an icon), for the action bar's own conversion on a phone.
CSS lives in luma-appkit-base.css under `/* LumaUI: Tab bar */`.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["TabBar"]

#: (key, label, Lucide icon)
Place = tuple


class TabBar(Gtk.Box):
    """Peer places as tabs: an icon over its name, the full width (or icons only, compact)."""

    __gtype_name__ = "LumaUITabBar"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, (str,))}

    def __init__(self, places: Sequence[Place], *, current: str | None = None,
                 on_change: Callable[[str], None] | None = None, compact: bool = False) -> None:
        if not places:
            raise ValueError("a tab bar has places")
        for place in places:
            if len(place) != 3:
                raise ValueError("a place is (key, label, icon)")
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=not compact,
                         accessible_role=Gtk.AccessibleRole.TAB_LIST)
        self.add_css_class("lumaui-tab-bar")
        lumaui.set_css_class(self, "compact", compact)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Places"])
        self._on_change = on_change
        self._places = [tuple(p) for p in places]
        self.tabs: dict[str, Gtk.ToggleButton] = {}
        self._counts: dict[str, Gtk.Widget] = {}
        self._dots: dict[str, Gtk.Widget] = {}
        self._current = current if current is not None else self._places[0][0]
        if self._current not in {p[0] for p in self._places}:
            raise ValueError(f"unknown place {self._current!r}")
        group: Gtk.ToggleButton | None = None
        for key, label, icon in self._places:
            tab = Gtk.ToggleButton(hexpand=not compact, accessible_role=Gtk.AccessibleRole.TAB,
                                   tooltip_text=label if compact else None)
            tab.add_css_class("lumaui-tab")
            tab.remove_css_class("image-button")
            tab.update_property([Gtk.AccessibleProperty.LABEL], [label])
            if group is not None:
                tab.set_group(group)
            group = group or tab
            overlay = Gtk.Overlay()
            face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            face.add_css_class("lumaui-tab-face")
            face.append(icons.image(icon))
            if not compact:
                name = Gtk.Label(label=label, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
                name.add_css_class("lumaui-tab-label")
                face.append(name)
            overlay.set_child(face)
            dot = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START, visible=False, can_target=False)
            dot.add_css_class("lumaui-tab-dot")
            overlay.add_overlay(dot)
            tab.set_child(overlay)
            tab.set_active(key == self._current)
            tab.connect("toggled", self._toggled, key)
            self.tabs[key] = tab
            self._dots[key] = dot
            self._overlays = getattr(self, "_overlays", {})
            self._overlays[key] = overlay
            self.append(tab)

    @staticmethod
    def wants_tabs(places: Sequence[Place]) -> bool:
        """v71 phone.js: a switch between 3 or more places, each with an icon, becomes a tab bar."""
        return len(places) >= tokens.TAB_BAR["min_places"] and all(len(p) == 3 and p[2] for p in places)

    @property
    def current(self) -> str:
        return self._current

    def set_current(self, key: str, *, notify: bool = False) -> None:
        if key not in self.tabs:
            raise ValueError(f"unknown place {key!r}")
        changed = key != self._current
        self._current = key
        tab = self.tabs[key]
        if not tab.get_active():
            tab.handler_block_by_func(self._toggled)
            tab.set_active(True)
            tab.handler_unblock_by_func(self._toggled)
        if notify and changed:
            self._changed(key)

    def _toggled(self, tab: Gtk.ToggleButton, key: str) -> None:
        if not tab.get_active():
            return
        if key != self._current:
            self._current = key
            self._changed(key)

    def _changed(self, key: str) -> None:
        if self._on_change is not None:
            self._on_change(key)
        self.emit("changed", key)

    def set_count(self, key: str, count: int | None, *, attention: bool = False) -> None:
        """A count badge on the tab (Alarms: 3); `attention=True` is the red one (Phone's
        Voicemail); None or 0 removes it."""
        from .content_badges import CountBadge

        if key not in self.tabs:
            raise ValueError(f"unknown place {key!r}")
        badge = self._counts.pop(key, None)
        if badge is not None:
            self._overlays[key].remove_overlay(badge)
        if count:
            badge = CountBadge(count, attention=attention)
            badge.add_css_class("lumaui-tab-count")
            badge.set_halign(Gtk.Align.END if self.has_css_class("compact") else Gtk.Align.CENTER)
            badge.set_valign(Gtk.Align.START)
            badge.set_can_target(False)
            self._overlays[key].add_overlay(badge)
            self._counts[key] = badge

    def set_status(self, key: str, status: str | None) -> None:
        """"running" shows a green dot on the tab (a running stopwatch or timer); None hides it."""
        if status not in (None, "running"):
            raise ValueError("status is 'running' or None")
        self._dots[key].set_visible(status == "running")

    def float_over(self, where: Gtk.Widget) -> "TabBar":
        """Sit full width at the foot of the window `where` is in (16 from each side, 34 up)."""
        from .structure_layers import LayerHost

        host = where if isinstance(where, Gtk.Overlay) else LayerHost.window_host(where)
        self.add_css_class("floating")
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.END)
        host.add_overlay(self)
        return self
