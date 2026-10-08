# SPDX-License-Identifier: Apache-2.0
"""Fixture evidence for v70 line923: Flow day headings scroll with their days."""
import json
import os
from pathlib import Path
import sys
from datetime import timedelta

sys.path.insert(0, str(Path.cwd() / "src/prairie-core/tests"))
from calendar_preview_identity import require_private_bus, require_test_display
from calendar_responsive import capture_window

require_private_bus()
require_test_display()
assert os.environ.get("PRAIRIE_EDS_MODE") == "disabled"
assert os.environ.get("LUMA_CALENDAR_FIXTURE")
width, theme, output = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
for name in ("LUMA_APPKIT_STYLE_PATH", "LUMA_APPKIT_TOKENS_PATH"):
    assert Path(os.environ[name]).is_file()
from prairie_apps import calendar as app
from gi.repository import Adw, GLib, Gtk
from prairie_apps.calendar_fixture import events_on

Adw.StyleManager.get_default().set_color_scheme(
    Adw.ColorScheme.FORCE_DARK if theme == "dark" else Adw.ColorScheme.FORCE_LIGHT)
app.APP_ID += ".FlowHeadingTest"
application = app.CalendarApplication()
report = {"width": width, "theme": theme, "failures": []}
window = None
captured = False

def finish():
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    for item in application.get_windows():
        item.close()
    application.quit()

def attempt(callback):
    try:
        callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        report["failures"].append(repr(error))
        finish()
    return GLib.SOURCE_REMOVE

def bounds():
    head = window.surface.flow_headers[window.today]
    ok, rect = head.compute_bounds(window.overlay)
    assert ok and rect.get_width() > 0 and rect.get_height() > 0
    return rect.get_y()

def heading_positions():
    positions = {}
    for day, head in window.surface.flow_headers.items():
        ok, rect = head.compute_bounds(window.overlay)
        assert ok and rect.get_height() > 0, day
        positions[str(day)] = rect.get_y()
    assert len(positions) == 14
    return positions

def after_scroll():
    global captured
    report["after_y"] = bounds()
    report["after_headings"] = heading_positions()
    report["after_scroll"] = window.scroll.get_vadjustment().get_value()
    capture_window(window, output.with_name(output.stem + "-after.png"))
    captured = True
    delta = report["after_scroll"] - report["before_scroll"]
    assert delta >= 100, ("scroll motion was not exercised", delta)
    motion = report["after_y"] - report["before_y"]
    assert abs(motion + delta) <= 2, ("day heading remained pinned", motion, delta)
    for day, before in report["before_headings"].items():
        movement = report["after_headings"][day] - before
        assert abs(movement + delta) <= 2, ("day heading remained pinned", day, movement, delta)
    assert len(window.surface.flow_headers) == len(window.surface.flow_sections) == 14
    for day, head in window.surface.flow_headers.items():
        assert head.get_parent() == window.surface.flow_sections[day], day
        expected = {f"cal-event-{event.uid}" for event in events_on(window.events, day, window.hidden_sources) if event.all_day}
        actual = set()
        child = head.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                actual.add(child.get_name())
            child = child.get_next_sibling()
        assert actual == expected, ("all-day actions changed", day, actual, expected)
    # v70 starts other days at their beginning; Today starts two hours before
    # the fixture's10:30 clock. At .9px/min this changes the heading by81px.
    window.anchor = window.today - timedelta(days=1)
    window.refresh()
    GLib.timeout_add(1600, lambda: attempt(previous_day))

def previous_day():
    head = window.surface.flow_headers[window.anchor]
    ok, rect = head.compute_bounds(window.overlay)
    assert ok
    report["previous_day_y"] = rect.get_y()
    capture_window(window, output.with_name(output.stem + "-previous-day.png"))
    assert abs(report["previous_day_y"] - report["before_y"] - 81) <= 2, (
        "non-today Flow scrolled into morning hours", report["previous_day_y"], report["before_y"])
    assert window.flow_at == window.anchor
    finish()

def before_scroll():
    assert window.get_width() == width
    assert window.fixture and window.loaded and window.view == "flow"
    report["before_y"] = bounds()
    report["before_headings"] = heading_positions()
    report["before_scroll"] = window.scroll.get_vadjustment().get_value()
    capture_window(window, output.with_name(output.stem + "-before.png"))
    adjustment = window.scroll.get_vadjustment()
    adjustment.set_value(report["before_scroll"] + 120)
    GLib.timeout_add(1000, lambda: attempt(after_scroll))

def enter_flow():
    window.set_view("flow")
    GLib.timeout_add(1600, lambda: attempt(before_scroll))

def ready():
    if not window.loaded or not window.get_mapped() or window.get_width() == 0:
        return GLib.SOURCE_CONTINUE
    border = 10 if window.has_css_class("solid-csd") else 0
    window.set_default_size(width + border, 820)
    GLib.timeout_add(1000, lambda: attempt(enter_flow))
    return GLib.SOURCE_REMOVE

def activated(_application):
    global window
    window = application.get_active_window()
    GLib.timeout_add(150, ready)

def timeout():
    report["failures"].append("Flow heading probe exceeded30seconds")
    finish()
    return GLib.SOURCE_REMOVE

application.connect_after("activate", activated)
GLib.timeout_add_seconds(30, timeout)
application.run([])
print(json.dumps(report))
raise SystemExit(1 if report["failures"] or not captured else 0)
