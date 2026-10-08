#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Capture the current v70 fixture on a private bus and disposable Xvfb display.

Requires LUMA_CALENDAR_PRIVATE_BUS=1 with dbus-run-session. OUTPUT WIDTH HEIGHT
    (default1211x874); CALENDAR_VISUAL_STATE: month/week/flow/flow-full/day/event/date-menu/calendars/empty/error.
No EDS access, real writes or cross-app launches.
"""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from calendar_preview_identity import require_private_bus, require_test_display
from calendar_responsive import capture_window


def main():
    require_private_bus()
    require_test_display()
    root = Path(__file__).resolve().parents[3]
    os.environ["LUMA_CALENDAR_FIXTURE"] = str(root / "tests/fixtures/calendar-v70.json")
    os.environ["LUMA_CALENDAR_STYLE_PATH"] = str(root / "src/prairie-core/style/calendar.css")
    from prairie_apps import calendar as app
    from prairie_apps.calendar_data import CalendarSource
    from prairie_apps.calendar_window import CalendarWindow
    from gi.repository import GLib, Gtk

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = map(int, sys.argv[2:4] or (1211, 874))
    mode = os.environ.get("CALENDAR_VISUAL_STATE", "month")
    if mode not in {"month", "week", "flow", "flow-full", "day", "event", "date-menu", "calendars", "empty", "error"}:
        raise ValueError(f"Unknown visual state: {mode}")
    app.APP_ID += ".VisualTest"
    application = app.CalendarApplication()
    failures = []
    captured = False

    def descendants(widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from descendants(child)
            child = child.get_next_sibling()

    def capture():
        nonlocal captured
        window = application.get_active_window()
        try:
            assert window.fixture and window.loaded
            assert window.get_application().get_application_id() == app.APP_ID
            assert window.get_width() == width, (window.get_width(), width)
            assert window.view == ("flow" if mode == "flow-full" else mode if mode in {"week", "flow"} else "month")
            if mode == "flow-full":
                column = window.surface.get_first_child()
                assert not window.pane.shown
                content_width = column.column.get_width() + column.column.get_margin_start() + column.column.get_margin_end()
                assert content_width == column.get_width(), (content_width, column.get_width())
                assert column.get_width() > 860, column.get_width()
            if mode == "calendars":
                synthetic = CalendarSource("synthetic", "Synthetic", "blue", "#00ff00")
                assert CalendarWindow._source_hue(SimpleNamespace(fixture=None), synthetic) == 120
                assert len(window.calendar_marks.items) == min(4, len(window.sources))
                for source in window.sources:
                    item = window.find_name(f"calendar.filter.{source.uid}")
                    assert item is not None and item.get_mapped(), source.name
                    mark = next(w for w in descendants(item) if w.__class__.__name__ == "Mark")
                    assert mark.get_width() >= 18, (source.name, mark.get_width())
                    count = sum(event.source_uid == source.uid for event in window.events)
                    if count:
                        tallies = [w for w in descendants(item)
                                   if isinstance(w, Gtk.Label) and w.get_text() == str(count)]
                        assert len(tallies) == 1 and not tallies[0].get_layout().is_ellipsized(), (
                            source.name, count)
            if mode in {"day", "event"}:
                assert window.pane.shown
                assert window.pane.is_drawer == (width <= 639)
                assert (window.selected_event is not None) == (mode == "event")
                if mode == "event" and width > 639:
                    assert window.pane.sheet.get_height() == window.island.get_height()
                    assert window.pane.sheet.get_margin_start() >= 8
                    fact = [w for w in descendants(window.pane)
                            if isinstance(w, Gtk.Label) and w.get_text() == "Work"]
                    assert fact and fact[0].get_width() >= 25 and not fact[0].get_layout().is_ellipsized()
            if mode in {"empty", "error"}:
                assert not window.events and not window.fixture.events
            if mode == "error":
                message = window.find_name("cal-load-error")
                assert message is not None and message.get_mapped()
                assert message.get_text() == "The visual fixture calendar service is unavailable."
                assert window.pane.shown and window.pane.sheet.get_mapped()
                assert window.pane.is_drawer == (width <= 639)
                assert window.pane.close_button.get_mapped()
            if mode == "date-menu":
                for month in range(1, 13):
                    choice = window.find_name(f"cal-month-{month}")
                    assert choice is not None and choice.get_mapped(), month
                    texts = [item for item in descendants(choice)
                             if isinstance(item, Gtk.Label) and item.get_text()]
                    assert texts, month
                    for item in texts:
                        assert item.get_mapped() and not item.get_layout().is_ellipsized(), (
                            month, item.get_text(), item.get_width())
            nodes = []
            for widget in descendants(window):
                if not widget.get_mapped():
                    continue
                ok, rect = widget.compute_bounds(window)
                if not ok:
                    continue
                if isinstance(widget, (Gtk.Button, Gtk.MenuButton)):
                    assert rect.get_x() >= -1, (widget.get_name(), rect.get_x())
                    assert rect.get_x() + rect.get_width() <= width + 1, (
                        widget.get_name(), rect.get_x(), rect.get_width())
                if isinstance(widget, Gtk.Label):
                    nodes.append(dict(text=widget.get_text(), name=widget.get_name(),
                                      x=rect.get_x(), y=rect.get_y(),
                                      width=rect.get_width(), height=rect.get_height()))
            capture_window(window, output)
            output.with_suffix(".json").write_text(json.dumps(dict(
                state=mode, width=window.get_width(), height=window.get_height(),
                fixture_events=len(window.fixture.events), labels=nodes), indent=2) + "\n")
            captured = True
            print(f"PASS: current Calendar {mode} fixture allocation and isolated capture")
        except Exception as error:
            import traceback
            traceback.print_exc()
            failures.append(repr(error))
        finally:
            window.close()
            application.quit()
        return GLib.SOURCE_REMOVE

    def prepare():
        window = application.get_active_window()
        try:
            assert window.fixture and window.loaded and len(window.fixture.events) == 42
            if mode in {"week", "flow", "flow-full"}:
                if mode == "flow-full":
                    window.pane.close()
                window.set_view("flow" if mode == "flow-full" else mode)
            elif mode == "day":
                window.select_day(window.today)
            elif mode == "event":
                window.open_event(next(e for e in window.events if e.editable and e.start.date() == window.today))
            elif mode == "date-menu":
                window.date_menu()
            elif mode == "calendars":
                window.calendars_menu()
            elif mode in {"empty", "error"}:
                window.fixture.events = ()
                if mode == "error":
                    window._loaded(window.generation, window.fixture.sources, (),
                                   "The visual fixture calendar service is unavailable.")
                    # Exercise the actual late, read-only Contacts callback after
                    # the backend failure; no provider is accessed in this fixture.
                    GLib.timeout_add(100, window._people_loaded, ())
                else:
                    window.reload()
            GLib.timeout_add(800, capture)
        except Exception as error:
            failures.append(repr(error))
            window.close()
            application.quit()
        return GLib.SOURCE_REMOVE

    def activated(_application):
        window = application.get_active_window()
        # Preserve the requested content width under Xvfb's5px-per-side CSD.
        border = 10 if window.has_css_class("solid-csd") else 0
        window.set_default_size(width + border, height)
        GLib.timeout_add(1600, prepare)

    def timeout():
        failures.append("Calendar visual test exceeded15seconds")
        for window in application.get_windows():
            window.close()
        application.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add_seconds(15, timeout)
    application.connect_after("activate", activated)
    application.run([])
    return 1 if failures or not captured else 0


if __name__ == "__main__":
    raise SystemExit(main())
