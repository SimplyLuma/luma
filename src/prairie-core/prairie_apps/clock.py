# SPDX-License-Identifier: Apache-2.0
"""Clock: four real clock tools composed from LumaUI parts.

The ClockStore and AlarmService retain the installed app's alarm and timer
behavior. LUMA_CLOCK_FIXTURE selects an in-memory v70 sample and starts no
background service; it never opens the real Clock store.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, AppWindow, BarAction, Card, Command, CommandGroup, PanelField, PanelHeading, PanelRow, TabBar,
    CommandRegistry, EmptyState, Island, ModeSwitch, PlaceResult,
    SEPARATOR, ScrollView, Toast, ToastHost, TypeLabel,
    add_style_sheet, apply_type, icons, install_lumaui, rank_places,
)
from luma_appkit.content_place import NO_PLACES  # noqa: E402
from luma_appkit.structure_adapt import WidthWatch  # noqa: E402
from .clock_alarms import (  # noqa: E402
    APP_ID, BACKGROUND_DENIED, BACKGROUND_UNAVAILABLE, BACKGROUND_REASON, IDLE_EXIT_MS,
    AlarmService, register_host_application,
)
from .clock_backend import (  # noqa: E402
    Alarm, ClockStore, Countdown, Stopwatch, TimerRecord, WorldClock,
    format_clock, format_countdown, format_stopwatch, home_zone_name,
    next_occurrence, uses_24_hour, zone_city, zone_offset,
)
from .clock_editor import AlarmForm  # noqa: E402
from .clock_fixture import FixtureClockStore  # noqa: E402
from .weather_backend import LocationSearch  # noqa: E402


MODES = (("world", "World", "globe"), ("alarm", "Alarms", "alarm-clock"),
         ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass"))
PRESETS = ((60, "1 min"), (180, "3 min"), (300, "5 min"),
           (600, "10 min"), (1500, "Focus 25"), (3600, "1 hour"))
#: Cities offered in Add a city before anything is typed (v71 suggests a few you don't have).
SUGGESTED_PLACES = (
    ("Los Angeles", "California, United States", "America/Los_Angeles"),
    ("New York", "New York, United States", "America/New_York"),
    ("London", "United Kingdom", "Europe/London"),
    ("Paris", "France", "Europe/Paris"),
    ("Tokyo", "Japan", "Asia/Tokyo"),
    ("Sydney", "New South Wales, Australia", "Australia/Sydney"),
    ("Singapore", "Singapore", "Asia/Singapore"),
    ("Mumbai", "India", "Asia/Kolkata"),
)


def city_meta(zone: str, now: datetime, home: str) -> str:
    """The v70 day and UTC difference, derived from actual zone rules."""
    here, there = now.astimezone(ZoneInfo(home)), now.astimezone(ZoneInfo(zone))
    delta = (there.date() - here.date()).days
    day = "Tomorrow" if delta > 0 else "Yesterday" if delta < 0 else "Today"
    hours = zone_offset(zone, now=now, home=home).total_seconds() / 3600
    value = f"{hours:+g}" if hours else "0"
    return f"{day} · {value} h"


def split_stopwatch(value: float) -> tuple[str, str]:
    whole, hundredths = format_stopwatch(value).split(".", 1)
    return whole, f".{hundredths}"


def next_enabled(alarms: tuple[Alarm, ...], now: datetime) -> tuple[Alarm, datetime] | None:
    scheduled = [(alarm, next_occurrence(alarm, now)) for alarm in alarms if alarm.enabled]
    return min(scheduled, key=lambda pair: pair[1]) if scheduled else None


def capture_clock():
    """Pin a running fixture to 1.05 s so captures are repeatable."""
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        calls += 1
        return 1000.0 if calls == 1 else 1001.051

    return monotonic


class ClockPlaces:
    """Adapt the offline city database; publish it only after the worker loads it."""

    def __init__(self, fixture: FixtureClockStore | None = None) -> None:
        self.fixture = fixture
        self.searcher: LocationSearch | None = None

    def load(self) -> None:
        if self.searcher is None:
            searcher = LocationSearch()
            searcher.load()
            self.searcher = searcher

    def search(self, query: str) -> list[PlaceResult]:
        results: list[PlaceResult] = []
        if self.fixture is not None:
            places = [PlaceResult(item["name"], item.get("region", ""), item["zone"])
                      for item in self.fixture.places()]
            results.extend(rank_places(places, query))
        if self.searcher is not None:
            # Libgweather has city names but not postal codes. Resolve US ZIPs
            # to a city first, then take the city's real time zone from the
            # local database. Keep the ordinary online lookup as a fallback.
            if len(query.strip()) == 5 and query.strip().isascii() and query.strip().isdigit():
                try:
                    with urlopen(f"https://api.zippopotam.us/us/{query.strip()}", timeout=4) as response:
                        postal = json.load(response)
                    for item in postal.get("places", ()):
                        city = item.get("place name", "")
                        state = item.get("state", "")
                        candidates = self.searcher.search(city) if city else ()
                        results.extend(PlaceResult(place.name, ", ".join(part for part in (place.region, place.country) if part),
                                                   place.timezone) for place in candidates
                                       if place.timezone and place.name.casefold() == city.casefold()
                                       and (not state or state.casefold() in place.region.casefold()))
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            found = self.searcher.search(query)
            if not found and len(query.strip()) >= 3:
                try:
                    found = self.searcher.lookup(query)
                except (OSError, ValueError, KeyError, TypeError):
                    found = ()
            results.extend(PlaceResult(place.name, ", ".join(part for part in (place.region, place.country) if part),
                                       place.timezone) for place in found if place.timezone)
        distinct: list[PlaceResult] = []
        seen: set[tuple[str, str]] = set()
        for place in results:
            key = (place.name.casefold(), place.value)
            if key not in seen:
                seen.add(key)
                distinct.append(place)
        return distinct


class TimerRing(Gtk.DrawingArea):
    """Clock's sole instrument: the v70 timer track and remaining arc.

    The coordinator triaged clock-02 as an app drawing. All ink comes from
    LumaUI theme colours; the time in its centre remains a TypeLabel.
    """

    def __init__(self) -> None:
        super().__init__(content_width=300, content_height=300)
        self.fraction = 1.0
        self.set_draw_func(self._draw)
        self.update_state([Gtk.AccessibleState.HIDDEN], [True])

    def set_fraction(self, value: float) -> None:
        self.fraction = max(0.0, min(1.0, value))
        self.queue_draw()

    def _draw(self, _area, cr, width: int, height: int) -> None:
        colours = self.get_style_context()
        radius = min(width, height) * 0.46
        centre = width / 2, height / 2
        cr.set_line_width(min(width, height) * 0.03)
        found, orange = colours.lookup_color("luma_mark_orange")
        if found:
            glow = cairo.RadialGradient(*centre, 0, *centre, min(width, height) / 2)
            glow.add_color_stop_rgba(0, orange.red, orange.green, orange.blue, 0.12)
            glow.add_color_stop_rgba(1, orange.red, orange.green, orange.blue, 0)
            cr.set_source(glow)
            cr.arc(*centre, min(width, height) / 2, 0, 2 * math.pi)
            cr.fill()
        for token, fraction in (("luma_well", 1.0), ("luma_mark_orange", self.fraction)):
            found, colour = colours.lookup_color(token)
            if not found:
                continue
            cr.set_source_rgba(colour.red, colour.green, colour.blue, colour.alpha)
            cr.set_line_cap(cairo.LINE_CAP_ROUND if token == "luma_mark_orange" else cairo.LINE_CAP_BUTT)
            cr.arc(*centre, radius, -math.pi / 2, -math.pi / 2 + 2 * math.pi * fraction)
            cr.stroke()


class ClockWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        fixture_path = os.environ.get("LUMA_CLOCK_FIXTURE")
        self.fixture = FixtureClockStore.from_path(fixture_path) if fixture_path else None
        self.store = self.fixture if self.fixture is not None else ClockStore()
        self.alarms: AlarmService | None = None if self.fixture else getattr(application, "alarms", None)
        self.capture_time = self.fixture is not None and os.environ.get("LUMA_CLOCK_CAPTURE_TIME") == "1"
        self.home = (self.fixture.home_zone if self.capture_time else home_zone_name()) or "UTC"
        self.home_label = self.fixture.home_label if self.capture_time else zone_city(self.home)
        if self.fixture is None:
            from .setup_location import read_setup_location
            setup = read_setup_location()
            self.store.seed_home_clock(setup.timezone if setup else home_zone_name())
        self.hour24 = False if self.capture_time else uses_24_hour()
        # Visual captures pin elapsed time; interactive fixture previews must
        # use the real monotonic clock so their stopwatch and timer keep moving.
        self.stopwatch = Stopwatch(clock=capture_clock()) if self.capture_time else Stopwatch()
        self.countdown = (Countdown(self.fixture.initial_timer, clock=capture_clock())
                          if self.capture_time else Countdown(self.fixture.initial_timer if self.fixture else 300))
        self.timer_label = "Timer"
        self.timer_done = False
        self.timer_uid: str | None = None
        self._restore_timer()
        self._tick_source = 0
        self._mode = "world"
        self._city_views: list[tuple[str, TypeLabel, TypeLabel, Gtk.Image]] = []
        self._city_drag_uid: str | None = None
        self._city_drop_uid: str | None = None
        self._alarm_minute: tuple[int, int] | None = None
        self._background_notice_shown = False
        self._updating_presets = False
        self._place_provider = ClockPlaces(self.fixture)
        self._phone = False
        self._editing_cities = False
        self._hover_uid: str | None = None
        self._drag_press: tuple[str, float, float, bool] | None = None
        self._city_parts: dict[str, tuple[Gtk.Widget, Gtk.Widget, Gtk.Widget, Gtk.Widget]] = {}
        self._takeover: str | None = None  # "alarm" or "find": what the bar has grown into
        self._alarm_form: AlarmForm | None = None
        self._find_gen = 0
        self._find_timer = 0
        super().__init__(
            application=application, app_id=APP_ID, title="Clock", icon_name=APP_ID,
            commands=self._commands(), default_width=1180, default_height=740,
            minimum_width=360, minimum_height=420,
        )

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vexpand=True)
        self.stack.add_named(self._world_page(), "world")
        self.stack.add_named(self._alarm_page(), "alarm")
        self.stack.add_named(self._stopwatch_page(), "stopwatch")
        self.stack.add_named(self._timer_page(), "timer")
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.page.set_name("ck-page")
        self.page.set_margin_top(29)
        self.page.set_margin_start(32)
        self.page.set_margin_end(32)
        self.page.set_margin_bottom(110)
        self.page.append(self.stack)
        clamp = Adw.Clamp(maximum_size=772, child=self.page)
        scroll = ScrollView(clamp)
        scroll.set_name("ck-body")
        self.scroll = scroll
        self.island = Island()
        self.island.set_name("ck-island")
        self.island.append(scroll)
        self.host = ToastHost(self.island)
        self.set_body(self.host)
        self.action = ActionCenter()
        self.action.set_name("ck-action")
        self.action.attach(self.host, inset="tight")
        mode_items = tuple((*mode, sum(alarm.enabled for alarm in self.store.alarms()))
                           if mode[0] == "alarm" else mode for mode in MODES)
        self.modes = ModeSwitch(mode_items, current="world", on_change=self.set_mode,
                                label="Clock")
        self.modes.set_name("ck-modes")
        for name, button in self.modes.buttons.items():
            button.set_name(f"ck-mode-{name}")
        # On a phone the places are icon-only tabs in the bar, as Phone (v71 .cktabbar).
        self.tabs = TabBar([(key, label, icon) for key, label, icon in MODES], current="world",
                           on_change=self.set_mode, compact=True)
        self.tabs.set_name("ck-tabs")
        # The same names as the desktop switch's buttons: only one of the two is in the window.
        for key, tab in self.tabs.tabs.items():
            tab.set_name(f"ck-mode-{key}")
        self.tabs.update_property([Gtk.AccessibleProperty.LABEL], ["Clock"])
        self._show_actions()
        # Clock decides its phone bar when it draws and redraws when the window crosses 560 (v71).
        self._tier_watch = WidthWatch(self, on_tier=lambda tier: self._set_phone(tier == "phone"))
        tap = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        tap.connect("pressed", self._page_pressed)
        self.page.add_controller(tap)

        self.connect("map", lambda *_: self._retick())
        self.connect("unmap", lambda *_: self._retick())
        self.connect("close-request", self._closing)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)
        if self.alarms is not None:
            self.alarms.connect_background(self._background_changed)
            self.connect("map", lambda *_: self._background_changed(self.alarms.background))
        self.set_mode("world")

    def _commands(self) -> CommandRegistry:
        return CommandRegistry((CommandGroup("", (
            Command("clock.close", "Close window", self.close, "log-out", shortcut=("Ctrl", "W")),
        )),))

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        while child := box.get_first_child():
            box.remove(child)

    def _now(self) -> datetime:
        frozen = os.environ.get("LUMA_CLOCK_NOW") if self.capture_time else None
        return datetime.fromisoformat(frozen) if frozen else datetime.now().astimezone()

    def _section(self, title: str) -> tuple[Gtk.Box, Gtk.Box]:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading_box.add_css_class("ck-section-head")
        heading = TypeLabel(title, role="label")
        heading_box.append(heading)
        section.append(heading_box)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        section.append(content)
        return section, content

    def _world_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        page.set_name("ck-world")
        page.set_valign(Gtk.Align.START)
        hero = Gtk.Overlay(halign=Gtk.Align.CENTER)
        hero.set_name("ck-world-hero")
        hero.set_size_request(-1, 124)
        hero.set_margin_bottom(19)
        self.world_time = TypeLabel("", role="display")
        self.world_time.set_name("ck-world-time")
        time_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER,
                            valign=Gtk.Align.START)
        time_line.set_margin_top(13)
        time_line.append(self.world_time)
        self.world_period = TypeLabel("", role="display")
        time_line.append(self.world_period)
        hero.set_child(time_line)
        self.world_date = TypeLabel("", role="body")
        apply_type(self.world_date.label, "body", muted=True)
        self.world_date.set_name("ck-world-date")
        self.world_date.set_halign(Gtk.Align.CENTER)
        self.world_date.set_valign(Gtk.Align.START)
        self.world_date.set_margin_top(97)
        hero.add_overlay(self.world_date)
        hero.set_measure_overlay(self.world_date, True)
        page.append(hero)
        self.cities = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                  column_spacing=10, row_spacing=10,
                                  min_children_per_line=1, max_children_per_line=3,
                                  homogeneous=True)
        self.cities.set_name("ck-cities")
        self.cities.set_vexpand(False)
        self.cities.set_valign(Gtk.Align.START)
        page.append(self.cities)
        self._load_cities()
        return page

    def _city_card(self, record: WorldClock) -> Card:
        card = Card()
        card.set_name("ck-city")
        card.set_size_request(170, 118)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title = TypeLabel(record.label, role="title-2")
        title.set_hexpand(True)
        glyph = icons.image("sun")
        apply_type(glyph, "body", muted=True)
        head.append(title)
        head.append(glyph)
        # ✕ sits where the day/night glyph is: on hover on the desktop, on every card while editing on a phone.
        remove = Gtk.Button(child=icons.image("x"), halign=Gtk.Align.END, valign=Gtk.Align.CENTER, visible=False)
        remove.remove_css_class("image-button")
        remove.set_name("ck-city-remove")
        remove.set_tooltip_text(f"Remove {record.label}")
        remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {record.label}"])
        remove.connect("clicked", lambda _button: self._remove_city(record))
        top = Gtk.Overlay(child=head)
        top.add_overlay(remove)
        card.append(top)
        # The name, the time and the day line spread over the card's height (v71 space-between).
        card.append(Gtk.Box(vexpand=True))
        clock = TypeLabel("", role="numeric")
        clock.set_name("ck-city-time")
        clock.set_margin_top(8)
        card.append(clock)
        card.append(Gtk.Box(vexpand=True))
        meta = TypeLabel("", role="caption")
        grip = icons.image("grip-vertical")
        grip.set_name("ck-city-grip")
        grip.set_halign(Gtk.Align.END)
        grip.set_valign(Gtk.Align.END)
        grip.set_visible(False)
        apply_type(grip, "body", muted=True)
        foot = Gtk.Overlay(child=meta)
        foot.add_overlay(grip)
        card.append(foot)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_a: self._city_hover(record.uid, True))
        hover.connect("leave", lambda *_a: self._city_hover(record.uid, False))
        card.add_controller(hover)
        # Drag a card to move it: with a mouse, press and move; on a phone, hold 300 ms
        # (which also starts editing) or drag the grip while editing (v71 ckDrag).
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda _gesture, x, y:
                     self._city_drag_begin(record.uid, card, remove, grip, x, y))
        drag.connect("drag-update", lambda gesture, dx, dy:
                     self._city_drag_update(record.uid, card, gesture, dx, dy))
        drag.connect("drag-end", lambda _gesture, _dx, _dy: self._city_drag_end(record.uid))
        card.add_controller(drag)
        hold = Gtk.GestureLongPress(delay_factor=0.6)
        hold.connect("pressed", lambda *_a: self._city_hold(record.uid, card))
        card.add_controller(hold)
        hold.group(drag)
        self._city_views.append((record.zone, clock, meta, glyph))
        self._city_parts[record.uid] = (glyph, remove, grip, card)
        return card

    def _city_hover(self, uid: str, inside: bool) -> None:
        if inside:
            self._hover_uid = uid
        elif self._hover_uid == uid:
            self._hover_uid = None
        self._show_city_controls()

    def _show_city_controls(self) -> None:
        editing = self._phone and self._editing_cities
        for uid, (glyph, remove, grip, _card) in self._city_parts.items():
            shown = editing or (not self._phone and self._hover_uid == uid and self._city_drag_uid is None)
            remove.set_visible(shown)
            glyph.set_opacity(0 if shown else 1)
            grip.set_visible(editing)

    def _set_city_editing(self, editing: bool) -> None:
        if self._editing_cities != editing:
            self._editing_cities = editing
            self._show_city_controls()

    def _page_pressed(self, _gesture, _count: int, x: float, y: float) -> None:
        """A tap outside the cards ends editing (v71)."""
        if not self._editing_cities:
            return
        node = self.page.pick(x, y, Gtk.PickFlags.DEFAULT)
        while node is not None:
            if node.get_name() == "ck-city":
                return
            node = node.get_parent()
        self._set_city_editing(False)

    def _city_drag_begin(self, uid: str, card: Gtk.Widget, remove: Gtk.Widget, grip: Gtk.Widget,
                         x: float, y: float) -> None:
        picked = card.pick(x, y, Gtk.PickFlags.DEFAULT)
        if picked is not None and (picked is remove or picked.is_ancestor(remove)):
            self._drag_press = None
            return
        on_grip = grip.get_visible() and picked is not None and (picked is grip or picked.is_ancestor(grip))
        self._drag_press = (uid, x, y, on_grip)

    def _city_hold(self, uid: str, card: Gtk.Widget) -> None:
        if not self._phone or self._drag_press is None or self._drag_press[0] != uid:
            return
        self._set_city_editing(True)
        self._city_drag_start(uid, card)

    def _city_drag_start(self, uid: str, card: Gtk.Widget) -> None:
        self._city_drag_uid = uid
        self._city_drop_uid = None
        card.add_css_class("ck-city-dragging")
        self._show_city_controls()

    def _city_drag_update(self, uid: str, card: Gtk.Widget, gesture: Gtk.GestureDrag,
                          dx: float, dy: float) -> None:
        if self._city_drag_uid != uid:
            press = self._drag_press
            if press is None or press[0] != uid:
                return
            # A mouse moves a card at once; a finger only from the grip (a hold starts it otherwise).
            if (self._phone and not press[3]) or math.hypot(dx, dy) < 6:
                return
            self._city_drag_start(uid, card)
        started, x, y = gesture.get_start_point()
        if not started:
            return
        found, point = card.compute_point(self.cities, Graphene.Point().init(x + dx, y + dy))
        if not found:
            return
        nearest: tuple[float, str, Gtk.Widget] | None = None
        records = self.store.world_clocks()
        for index, record in enumerate(records):
            child = self.cities.get_child_at_index(index)
            if child is None or record.uid == uid:
                continue
            found, origin = child.compute_point(self.cities, Graphene.Point().init(0, 0))
            if not found:
                continue
            if not (origin.x - 6 <= point.x <= origin.x + child.get_width() + 6
                    and origin.y - 6 <= point.y <= origin.y + child.get_height() + 6):
                continue
            centre_x = origin.x + child.get_width() / 2
            centre_y = origin.y + child.get_height() / 2
            distance = (point.x - centre_x) ** 2 + (point.y - centre_y) ** 2
            if nearest is None or distance < nearest[0]:
                nearest = (distance, record.uid, child)
        self._city_drop_uid = nearest[1] if nearest else None
        for index in range(len(records)):
            child = self.cities.get_child_at_index(index)
            if child is not None:
                child.remove_css_class("ck-city-drop")
        if nearest is not None:
            nearest[2].add_css_class("ck-city-drop")

    def _city_drag_end(self, uid: str) -> None:
        self._drag_press = None
        if self._city_drag_uid != uid:
            return
        target_uid = self._city_drop_uid
        self._city_drag_clear()
        if not target_uid:
            return
        records = self.store.world_clocks()
        source_index = next((i for i, record in enumerate(records) if record.uid == uid), -1)
        target_index = next((i for i, record in enumerate(records) if record.uid == target_uid), -1)
        if source_index < 0 or target_index < 0 or source_index == target_index:
            return
        try:
            if self.store.move_world_clock(uid, target_index - source_index):
                self._load_cities()
        except OSError as error:
            self._problem(f"City wasn’t moved: {error}")

    def _city_drag_clear(self) -> None:
        self._city_drag_uid = None
        self._city_drop_uid = None
        child = self.cities.get_first_child()
        while child is not None:
            child.remove_css_class("ck-city-drop")
            card = child.get_child()
            if card is not None:
                card.remove_css_class("ck-city-dragging")
            child = child.get_next_sibling()
        self._show_city_controls()

    def _load_cities(self) -> None:
        self._city_drag_uid = None
        self._city_drop_uid = None
        self._drag_press = None
        while child := self.cities.get_first_child():
            self.cities.remove(child)
        self._city_views.clear()
        self._city_parts.clear()
        for record in self.store.world_clocks():
            self.cities.insert(self._city_card(record), -1)
        # Add a city is the last card: a blank tile with + (v71 .ckcity.add).
        self.add_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                valign=Gtk.Align.FILL)
        self.add_card.set_name("ck-add-city")
        self.add_card.set_size_request(170, 118)
        self.add_card.add_css_class("ck-add")
        add = Gtk.Button(child=icons.image("plus"))
        add.remove_css_class("image-button")
        add.get_child().set_pixel_size(26)
        apply_type(add.get_child(), "body", muted=True)
        add.set_hexpand(True)
        add.set_vexpand(True)
        add.set_name("ck-add-button")
        add.set_tooltip_text("Add a city")
        add.update_property([Gtk.AccessibleProperty.LABEL], ["Add a city"])
        add.connect("clicked", lambda _button: self._open_place_search())
        self.add_card.append(add)
        self.cities.insert(self.add_card, -1)
        self._show_city_controls()
        self._refresh_world()

    def _remove_city(self, record: WorldClock) -> None:
        records = self.store.world_clocks()
        index = next((i for i, item in enumerate(records) if item.uid == record.uid), -1)
        if index < 0:
            return
        try:
            self.store.remove_world_clock(record.uid)
        except OSError as error:
            self._problem(f"City wasn’t removed: {error}")
            return
        self._hover_uid = None
        self._load_cities()

        def undo() -> None:
            try:
                self.store.insert_world_clock(record, index)
                self._load_cities()
            except OSError as error:
                self._problem(f"City wasn’t restored: {error}")

        Toast.show(self.host, f"Removed {record.label}", kind="deleted", undo=undo)

    def _open_place_search(self) -> None:
        """Add a city turns the bar into the search (v71): "Add a city" and its matches grow above,
        the field is the row. Until you type it offers a few cities you don't have."""
        self._set_city_editing(False)
        if self.action.grown is not None:
            self.action.fold()
        self._find_existing = {(record.label.casefold(), record.zone) for record in self.store.world_clocks()}
        self._find_results: list[PlaceResult] = []
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.set_name("ck-find-panel")
        panel.append(PanelHeading("Add a city"))
        self._find_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._find_list.set_name("ck-find-list")
        panel.append(self._find_list)
        field = PanelField("search", "City or ZIP", on_change=self._find_changed,
                           on_submit=lambda _text: self._find_pick())
        field.set_name("ck-place-search")
        self._takeover = "find"
        self._find_show(self._find_offers(), "")
        self.action.grow("find", panel, entry=field, close_word="Cancel", on_fold=self._takeover_folded)

    def _find_offers(self) -> list[PlaceResult]:
        offers = ([PlaceResult(item["name"], item.get("region", ""), item["zone"]) for item in self.fixture.places()]
                  if self.fixture is not None else [PlaceResult(*place) for place in SUGGESTED_PLACES])
        have = {label for label, _zone in self._find_existing}
        return [place for place in offers if place.name.casefold() not in have][:5]

    def _find_changed(self, text: str) -> None:
        self._find_gen += 1
        if self._find_timer:
            GLib.source_remove(self._find_timer)
            self._find_timer = 0
        if not text.strip():
            self._find_show(self._find_offers(), "")
            return
        # The place search's own pause before searching, then the lookup off the main thread.
        self._find_timer = GLib.timeout_add(180, self._find_run, text, self._find_gen)

    def _find_run(self, text: str, generation: int) -> bool:
        self._find_timer = 0

        def work() -> None:
            try:
                self._place_provider.load()
                found = self._place_provider.search(text)
            except (OSError, ValueError, RuntimeError):
                found = []
            GLib.idle_add(self._find_answer, text, generation, found)

        threading.Thread(target=work, daemon=True).start()
        return False

    def _find_answer(self, text: str, generation: int, found: list[PlaceResult]) -> bool:
        if generation == self._find_gen and self._takeover == "find":
            self._find_show([p for p in found if (p.name.casefold(), p.value) not in self._find_existing][:5], text)
        return False

    def _find_show(self, places: list[PlaceResult], text: str) -> None:
        self._find_results = places
        self._clear(self._find_list)
        for place in places:
            # v71 lPlaceSug: "Kansas City, MO" on one line.
            row = PanelRow(f"{place.name}, {place.subtitle}" if place.subtitle else place.name, icon="map-pin",
                           closes=False, on_activate=lambda p=place: self._add_place(p))
            row.set_name("ck-find-place")
            self._find_list.append(row)
        if text and not places:
            self._find_list.append(PanelHeading(NO_PLACES))

    def _find_pick(self) -> None:
        if self._find_results:
            self._add_place(self._find_results[0])

    def _close_place_search(self) -> None:
        if self._takeover == "find" and self.action.grown is not None:
            self.action.fold()

    def _add_place(self, place: PlaceResult) -> None:
        try:
            if not place.value:
                raise ValueError("That place has no time zone")
            self.store.add_world_clock(place.name, place.value)
        except (OSError, ValueError) as error:
            self._problem(f"City wasn’t added: {error}")
            return
        self._close_place_search()
        self._load_cities()
        Toast.show(self.host, f"Added {place.name}", kind="place")

    def _refresh_world(self) -> None:
        now = self._now()
        here = now.astimezone(ZoneInfo(self.home))
        value, period = format_clock(here, hour24=self.hour24)
        self.world_time.set_text(value)
        self.world_time.set_unit(f":{here.second:02d}")
        self.world_period.set_unit(period)
        self.world_period.set_visible(bool(period))
        self.world_date.set_text(f"{here.strftime('%A, %B')} {here.day} · {self.home_label}")
        for zone, label, meta, glyph in self._city_views:
            if self.capture_time and zone in self.fixture.offsets:
                hours = self.fixture.offsets.get(zone, 0)
                there = here + timedelta(hours=hours)
                day = "Tomorrow" if there.date() > here.date() else "Yesterday" if there.date() < here.date() else "Today"
                city_line = f"{day} · {hours:+g} h"
            else:
                there = now.astimezone(ZoneInfo(zone))
                city_line = city_meta(zone, now, self.home)
            clock, unit = format_clock(there, hour24=self.hour24)
            label.set_text(clock)
            label.set_unit(unit)
            meta.set_text(city_line)
            glyph.set_from_icon_name(icons.icon_name("moon" if there.hour < 7 or there.hour >= 19 else "sun"))

    def _alarm_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.alarm_page = page
        page.set_name("ck-alarms")
        page.set_vexpand(True)
        hero = Gtk.Overlay(halign=Gtk.Align.CENTER)
        self.alarm_hero = hero
        hero.set_name("ck-alarm-hero")
        hero.set_child(Gtk.Box())
        hero.set_size_request(-1, 141)
        hero.set_margin_bottom(19)
        self.alarm_eyebrow = TypeLabel("", role="label")
        self.alarm_time = TypeLabel("", role="display")
        self.alarm_note = TypeLabel("", role="body")
        apply_type(self.alarm_note.label, "body", muted=True)
        for child, top in ((self.alarm_eyebrow, 5), (self.alarm_time, 26),
                           (self.alarm_note, 110)):
            child.set_halign(Gtk.Align.CENTER)
            child.set_valign(Gtk.Align.START)
            child.set_margin_top(top)
            hero.add_overlay(child)
            hero.set_measure_overlay(child, True)
        page.append(hero)
        section, self.alarm_list = self._section("Alarms")
        self.alarm_section = section
        section.set_name("ck-alarm-list")
        self.alarm_list.set_margin_top(3)
        page.append(section)
        self.alarm_empty = EmptyState("No alarms yet", "Add an alarm for when you need it.",
            "lumaui-alarm-clock-symbolic", primary=("New alarm", self._new_alarm))
        self.alarm_empty.set_name("ck-alarm-empty")
        page.append(self.alarm_empty)
        self._load_alarms()
        return page

    def _load_alarms(self) -> None:
        self._clear(self.alarm_list)
        empty = not self.store.alarms()
        self.alarm_empty.set_visible(empty)
        self.alarm_hero.set_visible(not empty)
        self.alarm_section.set_visible(not empty)
        now = self._now()
        self._alarm_minute = (now.hour, now.minute)
        if self.fixture:
            minute = now.hour * 60 + now.minute
            options = [(alarm, (alarm.hour * 60 + alarm.minute - minute) % 1440 or 1440)
                       for alarm in self.store.alarms() if alarm.enabled]
            soonest = min(options, key=lambda pair: pair[1]) if options else None
            upcoming = (soonest[0], now + timedelta(minutes=soonest[1])) if soonest else None
        else:
            upcoming = next_enabled(self.store.alarms(), now)
        self.alarm_eyebrow.set_text("Next alarm" if upcoming else "No alarms on")
        self.alarm_time.set_visible(upcoming is not None)
        self.alarm_note.set_visible(upcoming is not None)
        if upcoming:
            alarm, moment = upcoming
            clock, period = format_clock(moment, hour24=self.hour24)
            self.alarm_time.set_text(clock)
            self.alarm_time.set_unit(period)
            duration = int((moment - now).total_seconds() // 60)
            self.alarm_note.set_text(f"{alarm.label or 'Alarm'} · in {duration // 60} h {duration % 60} min")
        for alarm in self.store.alarms():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            row.set_name("ck-alarm-row")
            row.set_size_request(-1, 58)
            row.set_opacity(1.0 if alarm.enabled else 0.5)
            name = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
            name.set_margin_start(4)
            name.append(TypeLabel(alarm.label or "Alarm", role="list-title"))
            days = self.fixture.alarm_days.get(alarm.uid) if self.fixture else None
            if days is None:
                if alarm.days:
                    days = alarm.repeat_text()
                else:
                    days = ("Today" if next_occurrence(alarm, now).date() == now.date()
                            else "Tomorrow")
            name.append(TypeLabel(days, role="caption"))
            row.append(name)
            moment = now.replace(hour=alarm.hour, minute=alarm.minute)
            value, period = format_clock(moment, hour24=self.hour24)
            clock = TypeLabel(value, role="numeric", unit=period)
            row.append(clock)
            toggle = Gtk.Switch(active=alarm.enabled, valign=Gtk.Align.CENTER)
            toggle.set_margin_end(2)
            toggle.set_name("ck-alarm-switch")
            toggle.update_property([Gtk.AccessibleProperty.LABEL],
                                   [f"Turn {alarm.label or 'alarm'} {'off' if alarm.enabled else 'on'}"])
            toggle.connect("notify::active", lambda switch, _p, a=alarm:
                           self._toggle_alarm(a, switch.get_active()))
            row.append(toggle)
            # Tapping an alarm grows the bar into its editor (v71); the switch only switches.
            row.set_cursor_from_name("pointer")
            tap = Gtk.GestureClick()
            tap.connect("released", lambda _g, _n, x, y, r=row, s=toggle, a=alarm:
                        self._alarm_row_tapped(r, s, a, x, y))
            row.add_controller(tap)
            self.alarm_list.append(row)

    def _alarm_row_tapped(self, row: Gtk.Widget, switch: Gtk.Widget, alarm: Alarm, x: float, y: float) -> None:
        picked = row.pick(x, y, Gtk.PickFlags.DEFAULT)
        if picked is not None and (picked is switch or picked.is_ancestor(switch)):
            return
        self._open_alarm_editor(alarm)

    def _toggle_alarm(self, alarm: Alarm, enabled: bool) -> None:
        if alarm.enabled == enabled:
            return
        try:
            if not self.store.update_alarm_enabled(alarm.uid, enabled):
                raise ValueError("Alarm no longer exists")
        except (OSError, ValueError) as error:
            self._problem(f"Alarm wasn’t changed: {error}")
        self._reschedule()
        self._show_actions()
        GLib.idle_add(lambda: (self._load_alarms(), False)[1])

    def _new_alarm(self) -> None:
        self._open_alarm_editor(None)

    def _open_alarm_editor(self, alarm: Alarm | None) -> None:
        """New alarm and a tapped alarm grow the bar into the editor (v71 "a real alarm editor").

        The editor takes over the bar: the panel holds the form, the row is Cancel and Save.
        """
        if self.action.grown is not None:
            self.action.fold()
        form = AlarmForm(alarm, uid=uuid.uuid4().hex, hour24=self.hour24,
                         on_delete=(lambda: self._delete_alarm(alarm)) if alarm is not None else None,
                         on_submit=self._save_alarm)
        self._alarm_form = form
        self._takeover = "alarm"
        self.action.show_bar([BarAction("", "Cancel", self._close_alarm_editor, fill=True, filled=True),
                              BarAction("", "Save", self._save_alarm, primary=True, keep_label=True, fill=True)])
        self._name_bar_buttons()
        self.action.grow("alarm", form, on_fold=self._takeover_folded)

    def _close_alarm_editor(self) -> None:
        if self.action.grown is not None:
            self.action.fold()
        else:
            self._takeover_folded()

    def _takeover_folded(self) -> None:
        """The editor or the city search folded (Cancel, Esc, ✕, or after acting): the bar comes back."""
        self._takeover = None
        self._alarm_form = None
        self._find_gen += 1
        GLib.idle_add(lambda: (self._show_actions(), False)[1])

    def _save_alarm(self) -> None:
        form = self._alarm_form
        if form is None:
            return
        record = form.result()
        index = next((i for i, item in enumerate(self.store.alarms()) if item.uid == record.uid), None)
        try:
            self.store.save_alarm(record, index=index)
        except (OSError, ValueError) as error:
            self._problem(f"Alarm wasn’t saved: {error}")
            return
        self._reschedule()
        self._close_alarm_editor()
        self._load_alarms()
        now = self._now()
        minutes = max(1, math.ceil((next_occurrence(record, now) - now).total_seconds() / 60))
        Toast.show(self.host, f"Alarm set for {minutes // 60} h {minutes % 60} min from now", kind="notified")

    def _delete_alarm(self, alarm: Alarm) -> None:
        index = next((i for i, item in enumerate(self.store.alarms()) if item.uid == alarm.uid), None)
        try:
            self.store.delete_alarm(alarm.uid)
        except OSError as error:
            self._problem(f"Alarm wasn’t deleted: {error}")
            return
        self._reschedule()
        self._close_alarm_editor()
        self._load_alarms()

        def undo() -> None:
            try:
                self.store.save_alarm(alarm, index=index)
            except (OSError, ValueError) as error:
                self._problem(f"Alarm wasn’t restored: {error}")
                return
            self._reschedule()
            self._load_alarms()
            self._show_actions()

        Toast.show(self.host, "Alarm deleted", kind="deleted", undo=undo)

    def _stopwatch_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        page.set_name("ck-stopwatch")
        page.set_valign(Gtk.Align.START)
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        hero.set_margin_top(13)
        hero.set_margin_bottom(12)
        self.sw_time = TypeLabel("00:00", role="display", unit=".00")
        self.sw_time.set_name("ck-stopwatch-time")
        hero.append(self.sw_time)
        page.append(hero)
        self.laps_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.laps_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.laps_section.set_name("ck-laps")
        self.laps_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.laps_header.set_name("ck-laps-header")
        labels = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        labels.set_margin_start(4)
        labels.set_margin_end(4)
        lap_label = TypeLabel("Lap", role="label")
        lap_label.set_hexpand(True)
        labels.append(lap_label)
        for title in ("Time", "Total"):
            column = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            column.set_size_request(120, -1)
            column.set_hexpand(False)
            column.append(Gtk.Box(hexpand=True))
            column.append(TypeLabel(title, role="label"))
            labels.append(column)
        self.laps_header.append(labels)
        self.laps_section.prepend(self.laps_header)
        self.laps_section.append(self.laps_list)
        page.append(self.laps_section)
        self._load_laps()
        return page

    def _load_laps(self) -> None:
        self._clear(self.laps_list)
        laps = self.stopwatch.laps
        self.laps_section.set_visible(bool(laps))
        if not laps:
            return
        lengths = [entry[0] for entry in laps]
        best, worst = min(lengths), max(lengths)
        for index, (split, total) in reversed(list(enumerate(laps, 1))):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.set_name("ck-lap-row")
            row.set_size_request(-1, 44)
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14,
                              hexpand=True)
            content.set_margin_start(4)
            content.set_margin_end(4)
            row.append(content)
            number = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                             valign=Gtk.Align.CENTER, hexpand=True)
            number.append(TypeLabel(str(index), role="list-title"))
            if len(laps) > 2 and split == best:
                number.append(TypeLabel("Fastest", role="label", weight=600))
            elif len(laps) > 2 and split == worst:
                number.append(TypeLabel("Slowest", role="label", weight=600))
            content.append(number)
            for value in (split, total):
                whole, hundredths = split_stopwatch(value)
                column = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                column.set_size_request(120, -1)
                column.set_hexpand(False)
                cell = TypeLabel(whole, role="body", weight=450)
                # The hundredths are v71's <small>: a sixth smaller than the time.
                small = TypeLabel(hundredths, role="body", weight=450)
                small.set_name("ck-lap-hundredths")
                small.set_valign(Gtk.Align.BASELINE_FILL)
                cell.set_valign(Gtk.Align.BASELINE_FILL)
                column.append(Gtk.Box(hexpand=True))
                column.append(cell)
                column.append(small)
                content.append(column)
            self.laps_list.append(row)

    def _toggle_stopwatch(self) -> None:
        self.stopwatch.stop() if self.stopwatch.running else self.stopwatch.start()
        self._refresh_stopwatch()
        self._show_actions()
        self._retick()

    def _lap_or_reset(self) -> None:
        if self.stopwatch.running:
            self.stopwatch.lap()
        else:
            self.stopwatch.reset()
        self._load_laps()
        self._refresh_stopwatch()
        self._show_actions()

    def _refresh_stopwatch(self) -> None:
        text, unit = split_stopwatch(self.stopwatch.elapsed())
        self.sw_time.set_text(text)
        self.sw_time.set_unit(unit)

    def _timer_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        page.set_name("ck-timer")
        # A centred Stack child with a taller minimum than the viewport starts
        # at a negative y, clipping the top of the ring on shorter windows.
        page.set_valign(Gtk.Align.START)
        self.timer_stage = Gtk.Overlay(halign=Gtk.Align.CENTER)
        self.timer_stage.set_name("ck-timer-ring")
        self.timer_stage.set_margin_top(32)
        self.timer_ring = TimerRing()
        self.timer_stage.set_child(self.timer_ring)
        self.timer_time = TypeLabel("05:00", role="display")
        self.timer_time.set_halign(Gtk.Align.CENTER)
        self.timer_time.set_valign(Gtk.Align.CENTER)
        self.timer_time.set_margin_bottom(0)
        self.timer_stage.add_overlay(self.timer_time)
        self.timer_title = TypeLabel("Timer", role="label")
        self.timer_title.set_halign(Gtk.Align.CENTER)
        self.timer_title.set_valign(Gtk.Align.CENTER)
        self.timer_title.set_margin_top(96)
        self.timer_stage.add_overlay(self.timer_title)
        self.timer_note = TypeLabel("Ready", role="body")
        apply_type(self.timer_note.label, "body", muted=True)
        self.timer_note.set_halign(Gtk.Align.CENTER)
        self.timer_note.set_valign(Gtk.Align.CENTER)
        self.timer_note.set_margin_top(144)
        self.timer_stage.add_overlay(self.timer_note)
        page.append(self.timer_stage)
        section, choices = self._section("Quick timers")
        section.set_name("ck-presets")
        choices.set_margin_top(16)
        self.preset_buttons: dict[int, Gtk.ToggleButton] = {}
        presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                               row_spacing=6, column_spacing=6,
                               max_children_per_line=6)
        presets.set_name("ck-preset-grid")
        presets.set_halign(Gtk.Align.START)
        self.presets = presets
        # TODO(kit-request clock-04-choice-chip.md): ChoiceChips(...).
        for seconds, label in PRESETS:
            button = Gtk.ToggleButton(label=label)
            button.set_name(f"ck-preset-{seconds}")
            button.set_active(seconds == int(self.countdown.total))
            button.connect("toggled", lambda b, value=seconds:
                           self._choose_preset(value) if b.get_active() and not self._updating_presets else None)
            presets.insert(button, -1)
            self.preset_buttons[seconds] = button
        choices.append(presets)
        page.append(section)
        self._refresh_timer()
        return page

    def _choose_preset(self, seconds: int) -> None:
        self._release_timer()
        self.timer_label = next(label for value, label in PRESETS if value == seconds)
        self.timer_done = False
        self.countdown.reset(seconds)
        self._refresh_timer()
        self._show_actions()

    def _toggle_timer(self) -> None:
        if self.countdown.running:
            self.countdown.pause()
            self._release_timer()
        else:
            if self.timer_done or self.countdown.remaining() <= 0:
                self.countdown.reset()
                self.timer_done = False
            self.countdown.start()
            self._arm_timer()
        self._refresh_timer()
        self._show_actions()
        self._retick()

    def _cancel_timer(self) -> None:
        self.timer_done = False
        self.countdown.reset()
        self._release_timer()
        self._refresh_timer()
        self._show_actions()
        self._retick()

    def _arm_timer(self) -> None:
        self._release_timer()
        uid = uuid.uuid4().hex
        record = TimerRecord(uid, self.timer_label, int(time.time() + self.countdown.remaining()),
                             int(self.countdown.total))
        try:
            self.store.save_timer(record)
        except (OSError, ValueError) as error:
            self.countdown.pause()
            self._problem(f"Timer wasn’t scheduled: {error}")
            return
        self.timer_uid = uid
        self._reschedule()

    def _release_timer(self) -> None:
        if self.timer_uid is None:
            return
        uid, self.timer_uid = self.timer_uid, None
        try:
            self.store.clear_timer(uid)
        except OSError as error:
            self._problem(f"Timer wasn’t cleared: {error}")
        self._reschedule()

    def _restore_timer(self) -> None:
        for record in self.store.timers():
            remaining = record.fires_at - time.time()
            if remaining > 0:
                self.countdown = Countdown(record.total_seconds or remaining)
                self.countdown.resume_with(remaining)
                self.timer_label = record.label
                self.timer_uid = record.uid
                break

    def _refresh_timer(self) -> None:
        remaining = self.countdown.remaining()
        self.timer_time.set_text(format_countdown(remaining))
        if hasattr(self, "modes"):
            self.modes.set_status("timer", format_countdown(remaining) if self.countdown.running else None)
        self.timer_ring.set_fraction(self.countdown.fraction())
        self.timer_title.set_text(self.timer_label)
        if self.timer_done:
            self.timer_note.set_text(f"{self.timer_label} is done")
        elif self.countdown.running:
            rings = (self._now() + timedelta(seconds=remaining)).strftime("%-I:%M %p")
            self.timer_note.set_text(f"Rings at {rings}")
        else:
            self.timer_note.set_text("Paused" if remaining < self.countdown.total else "Ready")
        self._updating_presets = True
        try:
            for seconds, button in self.preset_buttons.items():
                selected = not self.countdown.running and int(self.countdown.total) == seconds
                if button.get_active() != selected:
                    button.set_active(selected)
        finally:
            self._updating_presets = False

    def _set_phone(self, phone: bool) -> None:
        """Draw Clock's phone or desktop shape; called again whenever the window crosses 560."""
        self._phone = phone
        (self.add_css_class if phone else self.remove_css_class)("ck-phone")
        gutter = 16 if phone else 32
        self.page.set_margin_start(gutter)
        self.page.set_margin_end(gutter)
        # On a phone the kit's one safe area gives the scroller its room above the bar (v71).
        self.page.set_margin_bottom(0 if phone else 110)
        # Quick timers: an even 3-column grid of 48 px tiles on a phone, a row of chips otherwise.
        self.presets.set_min_children_per_line(3 if phone else 1)
        self.presets.set_max_children_per_line(3 if phone else 6)
        self.presets.set_homogeneous(phone)
        self.presets.set_halign(Gtk.Align.FILL if phone else Gtk.Align.START)
        self.presets.set_row_spacing(8 if phone else 6)
        self.presets.set_column_spacing(8 if phone else 6)
        if not phone:
            self._editing_cities = False
        self._hover_uid = None
        self._show_city_controls()
        self._show_actions()

    def _phone_items(self) -> list[object]:
        """v71's phone bar: after the four places, this place's actions as icons (Phone's shape)."""
        if self._mode == "alarm":
            return [BarAction("plus", tooltip="New alarm", on_activate=self._new_alarm, primary=True)]
        if self._mode == "stopwatch":
            running, elapsed = self.stopwatch.running, self.stopwatch.elapsed()
            return [BarAction("flag" if running else "rotate-ccw", tooltip="Lap" if running else "Reset",
                              on_activate=self._lap_or_reset, sensitive=running or elapsed > 0),
                    BarAction("pause" if running else "play",
                              tooltip="Stop" if running else "Resume" if elapsed else "Start",
                              on_activate=self._toggle_stopwatch, primary=True, danger=running)]
        if self._mode == "timer":
            running, remaining = self.countdown.running, self.countdown.remaining()
            return [BarAction("x", tooltip="Cancel", on_activate=self._cancel_timer,
                              sensitive=running or remaining < self.countdown.total),
                    BarAction("pause" if running else "rotate-ccw" if self.timer_done else "play",
                              tooltip="Pause" if running else "Again" if self.timer_done else
                              "Resume" if remaining < self.countdown.total else "Start",
                              on_activate=self._toggle_timer, primary=True, danger=running)]
        return []

    def _show_actions(self) -> None:
        self.modes.set_status("stopwatch", "running" if self.stopwatch.running else None)
        self.modes.set_status("timer", format_countdown(self.countdown.remaining()) if self.countdown.running else None)
        if hasattr(self, "tabs"):
            alarms_on = sum(alarm.enabled for alarm in self.store.alarms())
            self.modes.set_count("alarm", alarms_on)
            self.tabs.set_count("alarm", alarms_on or None)
            self.tabs.set_status("stopwatch", "running" if self.stopwatch.running else None)
            self.tabs.set_status("timer", "running" if self.countdown.running else None)
        if self._takeover is not None:
            return  # the alarm editor or the city search has the bar
        if self._phone:
            items = self._phone_items()
            self.action.show_bar(([SEPARATOR, *items] if items else []), modes=self.tabs)
            self._name_bar_buttons()
            return
        if self._mode == "alarm":
            items = [BarAction("plus", "New alarm", self._new_alarm, primary=True)]
        elif self._mode == "stopwatch":
            elapsed = self.stopwatch.elapsed()
            items = [BarAction("", "Lap" if self.stopwatch.running else "Reset", self._lap_or_reset,
                               sensitive=self.stopwatch.running or elapsed > 0),
                     BarAction("", "Stop" if self.stopwatch.running else "Resume" if elapsed else "Start",
                               self._toggle_stopwatch, primary=True, danger=self.stopwatch.running)]
        elif self._mode == "timer":
            remaining = self.countdown.remaining()
            items = [BarAction("", "Cancel", self._cancel_timer,
                               sensitive=self.countdown.running or remaining < self.countdown.total),
                     BarAction("", "Pause" if self.countdown.running else "Again" if self.timer_done else
                               "Resume" if remaining < self.countdown.total else "Start",
                               self._toggle_timer, primary=True, danger=self.countdown.running)]
        else:
            items = []
        self.action.show_bar(([SEPARATOR, *items] if items else []), modes=self.modes)
        self._name_bar_buttons()

    def _name_bar_buttons(self) -> None:
        child = self.action.bar_row.get_first_child()
        while child is not None:
            item = getattr(child, "bar_item", None)
            if item is not None:
                child.set_name("ck-action-primary" if item.primary else "ck-action-secondary")
            child = child.get_next_sibling()

    def set_mode(self, name: str) -> None:
        if name not in {key for key, _label, _icon in MODES}:
            return
        self._mode = name
        self.stack.set_visible_child_name(name)
        self.modes.set_current(name)
        self.tabs.set_current(name)
        self._show_actions()
        self._retick()

    def _reschedule(self) -> None:
        if self.alarms is not None:
            self.alarms.reschedule()

    def _background_changed(self, state: str) -> None:
        if state not in {BACKGROUND_DENIED, BACKGROUND_UNAVAILABLE} or self._background_notice_shown:
            return
        if not self.get_mapped():
            return
        self._background_notice_shown = True
        Toast.show(self.host, "Alarms and timers can ring only while Clock is open", kind="warning")

    def _problem(self, message: str) -> None:
        Toast.show(self.host, message, kind="error")

    def _retick(self) -> None:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0
        if not self.get_mapped():
            return
        interval = (31 if self._mode == "stopwatch" and self.stopwatch.running else
                    200 if self._mode == "timer" and self.countdown.running else 1000)
        self._tick_source = GLib.timeout_add(interval, self._tick)

    def _tick(self) -> bool:
        if self.countdown.running:
            self._refresh_timer()
            if self.countdown.finished():
                self.countdown.pause()
                self.timer_done = True
                self._release_timer()
                self._refresh_timer()
                self._show_actions()
                Toast.show(self.host, f"{self.timer_label} is done", kind="notified")
                self._retick()
                return False
        if self._mode == "world":
            self._refresh_world()
        elif self._mode == "alarm":
            now = self._now()
            if self._alarm_minute != (now.hour, now.minute):
                self._load_alarms()
        elif self._mode == "stopwatch":
            self._refresh_stopwatch()
        elif not self.countdown.running:
            self._refresh_timer()
        return True

    def _key_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        if isinstance(self.get_focus(), Gtk.Editable):
            return False
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_4:
            self.set_mode(MODES[keyval - Gdk.KEY_1][0])
            return True
        if keyval == Gdk.KEY_space:
            if self._mode == "stopwatch":
                self._toggle_stopwatch()
                return True
            if self._mode == "timer":
                self._toggle_timer()
                return True
        return False

    def _closing(self, *_args) -> bool:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0
        if self.alarms is not None:
            self.alarms.disconnect_background(self._background_changed)
        if getattr(self.store, "host_scheduled", False):
            self.store.close()
            self.get_application()._release_clock_foreground()
        return False


class ClockApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.alarms: AlarmService | None = None
        self._fixture_state: tempfile.TemporaryDirectory | None = None
        self._host_required = False
        self._host_ready = True
        self._host_pending = False
        self._host_generation = 0
        self._host_boot = None
        self._host_boot_transition = False
        self._foreground_acquired = False
        self._pending_tab = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        if self.get_flags() & Gio.ApplicationFlags.IS_SERVICE:
            self.set_inactivity_timeout(IDLE_EXIT_MS)
        if os.environ.get("LUMA_CLOCK_FIXTURE"):
            self._fixture_state = tempfile.TemporaryDirectory(prefix="clock-v70-")
            os.environ["XDG_STATE_HOME"] = self._fixture_state.name
        install_lumaui()
        path = os.environ.get("LUMA_CLOCK_STYLE_PATH", "/usr/share/prairie-core/clock.css")
        if os.path.isfile(path):
            add_style_sheet(path)
        opener = Gio.SimpleAction.new("clock-open", GLib.VariantType.new("s"))
        opener.connect("activate", self._open_tab)
        self.add_action(opener)
        if not os.environ.get("LUMA_CLOCK_FIXTURE"):
            from .clock_host import host_available
            self._host_required = Path('/.flatpak-info').exists() and host_available()
            self._host_ready = not self._host_required
            if self._host_ready:
                self.alarms = AlarmService(self)
                self.alarms.start()

    def do_activate(self) -> None:
        if self._host_required and not self._host_ready:
            self._show_host_boot()
            self._request_clock_foreground()
            return
        self._window().present()

    def do_shutdown(self) -> None:
        self._host_generation += 1
        self._release_clock_foreground()
        if self.alarms is not None:
            self.alarms.shutdown()
        Adw.Application.do_shutdown(self)
        if self._fixture_state is not None:
            self._fixture_state.cleanup()

    def _window(self) -> ClockWindow:
        return self.props.active_window or ClockWindow(self)

    def _show_host_boot(self, failed=False) -> None:
        if self._host_boot is None:
            self._host_boot = AppWindow(application=self, app_id=APP_ID,
                title='Clock', icon_name=APP_ID, commands=CommandRegistry(()), default_width=1180,
                default_height=740, minimum_width=360, minimum_height=420)
            self._host_boot.connect('close-request', self._host_boot_closed)
        state = EmptyState('Clock needs its alarm service' if failed else 'Opening Clock',
            'Retry, or open Depot to repair Clock’s host support.' if failed else 'Connecting to your alarms and timers…',
            'lumaui-alarm-clock-symbolic',
            primary=('Retry', self._retry_clock_foreground) if failed else None)
        self._host_boot.set_body(state)
        self._host_boot.present()

    def _retry_clock_foreground(self, *_args) -> None:
        if not self._host_pending:
            self._show_host_boot()
            self._request_clock_foreground()

    def _host_boot_closed(self, *_args) -> bool:
        if not self._host_boot_transition:
            self._host_generation += 1
            self._host_boot = None
            self._release_clock_foreground()
        return False

    def _request_clock_foreground(self) -> None:
        if self._host_pending:
            return
        self._host_pending = True
        self._host_generation += 1
        generation = self._host_generation
        connection = self.get_dbus_connection()
        self.hold()
        def request():
            from .background_agent import ensure_agent
            ready = False
            try:
                ready = ensure_agent(APP_ID, f'{APP_ID}.Agent',
                    reason=BACKGROUND_REASON, connection=connection, foreground=True)
            except Exception:
                logging.getLogger(__name__).exception('Clock foreground service was unavailable')
            GLib.idle_add(self._clock_foreground_ready, generation, ready)
        threading.Thread(target=request, name='clock-native-foreground', daemon=True).start()

    def _clock_foreground_ready(self, generation, ready) -> bool:
        self._host_pending = False
        try:
            if generation != self._host_generation or self._host_boot is None:
                self._release_clock_foreground(force=True)
                if self._host_boot is not None:
                    self._request_clock_foreground()
                return False
            if not ready:
                self._release_clock_foreground(force=True)
                self._show_host_boot(failed=True)
                return False
            self._foreground_acquired = True
            try:
                self.alarms = AlarmService(self)
                self.alarms.start()
            except Exception:
                logging.getLogger(__name__).exception('Clock could not read the native alarm plan')
                self._release_clock_foreground()
                self._show_host_boot(failed=True)
                return False
            self._host_ready = True
            self._host_boot_transition = True
            self._host_boot.close()
            self._host_boot = None
            self._host_boot_transition = False
            window = self._window()
            if self._pending_tab is not None:
                window.set_mode(self._pending_tab)
                self._pending_tab = None
            window.present()
            return False
        finally:
            self.release()

    def _release_clock_foreground(self, *, force=False) -> None:
        if not self._foreground_acquired and not force:
            return
        self._foreground_acquired = False
        self._host_ready = not self._host_required
        if self.alarms is not None:
            self.alarms.shutdown()
            self.alarms = None
        from .background_agent import release_foreground
        connection = self.get_dbus_connection()
        if connection is not None:
            release_foreground(connection)

    def _open_tab(self, _action, parameter) -> None:
        tab = parameter.get_string() if parameter else "alarm"
        if self._host_required and not self._host_ready:
            self._pending_tab = tab
            self.do_activate()
            return
        if self.alarms is not None and self.alarms.ringing:
            self.alarms.stop(self.alarms.ringing.split("-", 1)[1])
        window = self._window()
        window.set_mode(tab)
        window.present()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO if os.environ.get("LUMA_CLOCK_DEBUG") else logging.WARNING)
    if not os.environ.get("LUMA_CLOCK_FIXTURE"):
        register_host_application(APP_ID)
    arguments = list(sys.argv if argv is None else argv)
    if "--agent" in arguments[1:]:
        from .clock_agent import main as agent_main
        return agent_main(arguments)
    return ClockApplication().run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
