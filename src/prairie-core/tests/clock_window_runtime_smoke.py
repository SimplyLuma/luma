#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The LumaUI Clock opens saved cities and alarms in both size classes.

The saved data is built before the window, so this also catches constructor
ordering errors where World content reaches for chrome that is not ready yet.
Check mode changes in wide and narrow windows and in handheld presentation.

    xvfb-run -a env GSK_RENDERER=cairo PYTHONPATH=... python3 tests/clock_window_runtime_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

root = tempfile.TemporaryDirectory()
for key, sub in (("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"), ("XDG_CONFIG_HOME", "config")):
    os.environ[key] = str(Path(root.name) / sub)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from prairie_apps.clock_backend import Alarm, ClockStore, WEEKDAYS  # noqa: E402


def settle(rounds: int = 60) -> None:
    context = GLib.MainContext.default()
    for _ in range(rounds):
        while context.pending():
            context.iteration(False)


def seed() -> ClockStore:
    store = ClockStore()
    for label, zone in (("London", "Europe/London"), ("Kathmandu", "Asia/Kathmandu"), ("Auckland", "Pacific/Auckland")):
        store.add_world_clock(label, zone)
    data = store._read()
    # Written straight into the record list: this test is about opening onto
    # saved data, not about how an alarm gets scheduled.
    data["alarms"] = [{"uid": "f" * 32, "label": "Wake", "hour": 6, "minute": 30,
                       "days": list(WEEKDAYS), "enabled": True}]
    store._write(data)
    return store


def check(application: Adw.Application, presentation: str) -> str:
    os.environ["LUMA_PRESENTATION_MODE"] = presentation
    from prairie_apps.clock import ClockWindow

    results = []
    for width in (980, 500):
        window = ClockWindow(application)
        window.set_default_size(width, 700)
        window.present()
        for _ in range(200):
            settle(4)
            if abs(window.get_width() - width) < 40:
                break
        settle()
        assert abs(window.get_width() - width) < 40, (
            f"window is {window.get_width()}px, not {width}px: run on a screen at least 1280px wide")
        names = [record.label for record in window.store.world_clocks()]
        assert names == ["London", "Kathmandu", "Auckland"], names
        assert len(window._city_views) == len(names)
        assert [alarm.label for alarm in window.store.alarms()] == ["Wake"]
        window.set_mode("alarm")
        assert window.stack.get_visible_child_name() == "alarm"
        alarm_row = window.alarm_list.get_first_child()
        window._tick()
        assert window.alarm_list.get_first_child() is alarm_row
        window.set_mode("timer")
        window._toggle_timer()
        assert window.countdown.running
        window._choose_preset(60)
        assert not window.countdown.running and window.countdown.total == 60
        assert not window.store.timers()
        window.set_mode("stopwatch")
        window._toggle_stopwatch()
        window._lap_or_reset()
        assert window.stopwatch.laps and window.laps_list.get_first_child() is not None
        window.set_mode("world")
        assert window.stack.get_visible_child_name() == "world"
        results.append(f"{width}px")
        window.close()
        settle()
    return f"{presentation}: " + ", ".join(results)


def main() -> int:
    seed()
    application = Adw.Application(
        application_id="org.projectluma.ClockWindowTest", flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    assert application.register(None)
    lines = [check(application, mode) for mode in ("windowed", "fullscreen-mobile")]
    print("clock-window-runtime-smoke: opens with saved cities and alarms; " + "; ".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        root.cleanup()
