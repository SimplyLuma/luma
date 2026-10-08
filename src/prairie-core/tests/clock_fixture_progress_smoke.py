#!/usr/bin/python3
"""Interactive fixture stopwatch and timer advance beyond the capture instant."""

from __future__ import annotations

import os
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gio, GLib, Graphene

from prairie_apps.clock import ClockPlaces, ClockWindow
from prairie_apps.clock_fixture import FixtureClockStore


def main() -> int:
    fixture = Path(__file__).resolve().parents[3] / "tests/fixtures/clock-v70.json"
    places = ClockPlaces(FixtureClockStore.from_path(fixture))
    places.searcher = SimpleNamespace(
        search=lambda _query: (),
        lookup=lambda _query: (SimpleNamespace(name="Kansas City", region="Missouri",
                                                country="United States", timezone="America/Chicago"),),
    )
    assert places.search("64105")[0].name == "Kansas City"
    places.searcher = SimpleNamespace(
        search=lambda query: (SimpleNamespace(name="Kansas City", region="Missouri",
                                              country="United States", timezone="America/Chicago"),)
        if query == "Kansas City" else (),
        lookup=lambda _query: (),
    )
    postal = {"places": [{"place name": "Kansas City", "state": "Missouri"}]}
    with mock.patch("prairie_apps.clock.urlopen", return_value=io.BytesIO(json.dumps(postal).encode())):
        assert places.search("64105")[0].value == "America/Chicago"
    with tempfile.TemporaryDirectory() as private:
        for key, folder in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"),
                            ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
            os.environ[key] = str(Path(private) / folder)
        os.environ["LUMA_CLOCK_FIXTURE"] = str(fixture)
        os.environ.pop("LUMA_CLOCK_CAPTURE_TIME", None)
        app = Adw.Application(application_id="org.projectluma.ClockFixtureProgressTest",
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        assert app.register(None)
        window = ClockWindow(app)
        window.present()
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

        first_city = window.store.world_clocks()[0]
        target_city = window.store.world_clocks()[2]
        card = window.cities.get_child_at_index(0).get_child()
        target = window.cities.get_child_at_index(2)
        glyph, remove, grip, _card = window._city_parts[first_city.uid]
        # A mouse moves a card at once (v71): press, then move past 6 px.
        window._city_drag_begin(first_city.uid, card, remove, grip, 10, 10)
        found, centre = target.compute_point(card, Graphene.Point().init(
            target.get_width() / 2, target.get_height() / 2))
        assert found
        gesture = SimpleNamespace(get_start_point=lambda: (True, 10, 10))
        window._city_drag_update(first_city.uid, card, gesture, centre.x - 10, centre.y - 10)
        assert window._city_drop_uid == target_city.uid
        window._city_drag_end(first_city.uid)
        assert window.store.world_clocks()[2].uid == first_city.uid
        assert window._city_drag_uid is None
        # ✕ shows on hover on the desktop, and on every card while editing on a phone.
        window._city_hover(first_city.uid, True)
        assert window._city_parts[first_city.uid][1].get_visible()
        window._city_hover(first_city.uid, False)
        window._set_phone(True)
        assert window.has_css_class("ck-phone") and window.page.get_margin_start() == 16
        window._set_city_editing(True)
        assert all(parts[1].get_visible() and parts[2].get_visible() for parts in window._city_parts.values())
        window._set_phone(False)
        assert not window._editing_cities and window.page.get_margin_start() == 32
        window._remove_city(first_city)
        assert first_city.uid not in {city.uid for city in window.store.world_clocks()}
        # Add a city grows the bar: v71's suggestions until you type; Return adds the first.
        window._open_place_search()
        assert window.action.grown == "find"
        assert window._find_results[0].name == "Kansas City" and len(window._find_results) == 5, window._find_results
        window._find_pick()
        assert window.action.grown is None and window.store.world_clocks()[-1].label == "Kansas City"

        # New alarm grows the bar into the editor; Save adds it and says how far away it is.
        window.set_mode("alarm")
        count = len(window.store.alarms())
        window._new_alarm()
        assert window._takeover == "alarm" and window.action.grown == "alarm"
        window._alarm_form.hour_wheel._turn(1)
        window._alarm_form.day_buttons[0].set_active(True)
        window._save_alarm()
        assert window._takeover is None and len(window.store.alarms()) == count + 1
        added = next(a for a in window.store.alarms() if a.days == (0,))
        assert (added.hour, added.minute) == (8, 0), added
        # Tapping an alarm edits it; Delete alarm removes it, with Undo.
        window._open_alarm_editor(added)
        window._delete_alarm(added)
        assert added.uid not in {a.uid for a in window.store.alarms()}
        window._set_phone(True)
        assert [getattr(c, "bar_item", None) and c.bar_item.tooltip for c in
                [window.action.bar_row.get_last_child()]] == ["New alarm"]
        window._set_phone(False)

        window.set_mode("stopwatch")
        window._toggle_stopwatch()
        first = window.stopwatch.elapsed()
        time.sleep(0.15)
        window._tick()
        later = window.stopwatch.elapsed()
        assert later - first > 0.08, (first, later)
        assert window.stopwatch.running
        window._toggle_stopwatch()

        window.set_mode("timer")
        while context.pending():
            context.iteration(False)
        found, ring_origin = window.timer_ring.compute_point(
            window.scroll, Graphene.Point().init(0, 0))
        assert found and ring_origin.y >= 0, ring_origin
        window._toggle_timer()
        first = window.countdown.remaining()
        time.sleep(0.15)
        window._tick()
        later = window.countdown.remaining()
        assert first - later > 0.08, (first, later)
        assert window.countdown.running
        window._cancel_timer()
        window.close()
    print("clock-fixture-progress: stopwatch and timer advance while running")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
