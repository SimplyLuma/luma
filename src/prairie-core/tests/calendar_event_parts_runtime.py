#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Rendered event controls use the shared v71 geometry at every width.

Run on a private test display and bus; all calendar state comes from the
in-memory fixture. No provider or real calendar is opened.
"""
import os
from pathlib import Path
import tempfile
import time

root = Path(__file__).resolve().parents[3]
os.environ['PRAIRIE_EDS_MODE'] = 'disabled'
os.environ['LUMA_CALENDAR_FIXTURE'] = str(root / 'tests/fixtures/calendar-v70.json')
os.environ['LUMA_CALENDAR_STYLE_PATH'] = str(root / 'src/prairie-core/style/calendar.css')
os.environ['LUMA_CALENDAR_SELECTED_EVENT'] = '27'
os.environ['LUMA_CALENDAR_TZID'] = 'UTC'
os.environ['GSK_RENDERER'] = 'cairo'
state = tempfile.mkdtemp(prefix='calendar-event-parts-')
for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
    os.environ[name] = str(Path(state) / name)

from prairie_apps import calendar
from prairie_apps.calendar_window import CalendarWindow
from gi.repository import GLib, Gtk


def settle():
    until = time.monotonic() + .8
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


calendar.APP_ID += '.EventPartsTest'
app = calendar.CalendarApplication()
assert app.register(None)
for width in (360, 500, 720, 1024, 1180):
    win = CalendarWindow(app)
    win.set_default_size(width, 874)
    win.present()
    settle()
    assert win.loaded and win.selected_event, (width, 'fixture did not load')
    tiles = win.find_name('cal-event-tiles')
    assert tiles is not None, (width, 'event controls missing')
    button = tiles.get_first_child()
    count = 0
    while button:
        assert button.measure(Gtk.Orientation.VERTICAL, 66)[0] == 64, (width, 'tile height', button.measure(Gtk.Orientation.VERTICAL, 66))
        count += 1
        button = button.get_next_sibling()
    assert count == 4, (width, 'event actions lost', count)
    assert tiles.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= 288, (width, 'four controls cannot fit details pane')
    answer = win.find_name('cal-rsvp')
    assert answer is not None and answer.measure(Gtk.Orientation.VERTICAL, 288)[0] == 44, (width, 'RSVP control height', answer.measure(Gtk.Orientation.VERTICAL, 288) if answer else None)
    win.set_view('flow')
    settle()
    heading = win.surface.flow_headers[win.today].get_first_child()
    weekday = heading.get_first_child()
    date_label = weekday.get_next_sibling()
    first_ok, first_bounds = weekday.compute_bounds(heading)
    date_ok, date_bounds = date_label.compute_bounds(heading)
    assert first_ok and date_ok
    assert abs(date_bounds.get_x() - first_bounds.get_x() - first_bounds.get_width() - 12) <= 1
    assert weekday.get_width() <= weekday.measure(Gtk.Orientation.HORIZONTAL, -1)[1] + 1, (width, 'weekday consumes spare width')
    found, now_ink = win.get_style_context().lookup_color('luma_calendar_now')
    assert found and now_ink.red > now_ink.blue, (width, 'current-time marker lost its fixed red ink')
    win.quick.set_text('Lunch with Priya Fri 1pm')
    win.preview_quick(win.quick.text)
    assert win.quick._chips == ['Fri, Sep 25 · 1 PM'], (width, win.quick._chips)
    win.new_event(text=win.quick.text)
    settle()
    assert win.editor.submit_on_return
    assert win.editor.hint.get_label() == 'Esc to fold · Return to create'
    win.discard_draft()
    win.close()
    settle()
    print(f'PASS event controls at {width}px', flush=True)
