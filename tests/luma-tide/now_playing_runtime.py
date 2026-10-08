# SPDX-License-Identifier: Apache-2.0
"""Actual mouse/keyboard entry through the deck, on a private bus and X display.

dbus-run-session -- xvfb-run -a env GDK_BACKEND=x11 TIDE_NOW_PRIVATE_BUS=1 \
    PYTHONPATH=src/luma-platform/appkit:src/luma-tide \
    python3 tests/luma-tide/now_playing_runtime.py
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path

address = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
if (not __debug__ or os.environ.get('TIDE_NOW_PRIVATE_BUS') != '1'
        or not address.startswith(('unix:path=/tmp/dbus-', 'unix:abstract=/tmp/dbus-'))):
    raise SystemExit('Use the documented private-bus/display command')
sandbox = tempfile.TemporaryDirectory(prefix='tide-now-actions-')
for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
    os.environ[key] = sandbox.name + '/' + key
os.environ['GSETTINGS_BACKEND'] = 'memory'
for key in ('LUMA_TIDE_NOW', 'LUMA_TIDE_QUERY', 'LUMA_TIDE_ARTWORK_ROOT', 'LUMAUI_CONFORM_STUDIO_ROOT'):
    os.environ.pop(key, None)

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GLib
from luma_appkit import add_style_sheet, install_appkit, install_lumaui
from luma_tide.fixture import FixtureLibrary
from luma_tide.ui import TideWindow

repo = Path(__file__).resolve().parents[2]
title = 'TideNowPlayingRuntime'


def xdo(*args):
    return subprocess.run(['xdotool', *args], check=True, capture_output=True,
                          text=True, timeout=5).stdout.strip()


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def settle(predicate, reason):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pump()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError(reason)


def find(widget, name):
    if widget.get_name() == name:
        return widget
    child = widget.get_first_child()
    while child is not None:
        result = find(child, name)
        if result is not None:
            return result
        child = child.get_next_sibling()
    return None


def control(window, name):
    settle(lambda: (item := find(window, name)) is not None and item.get_mapped()
           and item.get_width() > 0 and item.get_height() > 0,
           name + ' did not become reachable')
    return find(window, name)


def click(window, widget, window_id):
    ok, bounds = widget.compute_bounds(window)
    assert ok and bounds.get_width() > 0 and bounds.get_height() > 0
    geometry = dict(line.split('=', 1) for line in
                    xdo('getwindowgeometry', '--shell', window_id).splitlines() if '=' in line)
    x = round(int(geometry['X']) + bounds.get_x() + bounds.get_width() / 2)
    y = round(int(geometry['Y']) + bounds.get_y() + bounds.get_height() / 2)
    xdo('mousemove', '--sync', str(x), str(y))
    xdo('mousedown', '1')
    time.sleep(.05)
    xdo('mouseup', '1')
    pump()


def key(widget, value, window_id):
    if widget is not None:
        assert widget.grab_focus()
    xdo('windowfocus', '--sync', window_id)
    xdo('key', value)
    pump()


app = Adw.Application(application_id='org.projectluma.Tide.NowPlayingTest',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit()
install_lumaui()
add_style_sheet(str(repo / 'src/luma-tide/data/tide.css'))
try:
    for width, height in ((1180, 740), (398, 820)):
        source = FixtureLibrary(repo / 'tests/fixtures/tide-v70.json')
        original = source.player
        window = TideWindow(app, source)
        try:
            assert window.get_application() is app
            assert window._geometry_app_id == app.get_application_id()
            window.set_title(title)
            window.set_default_size(width, height)
            window.present()
            settle(lambda: bool(window.library.albums) and window.deck.title_action.get_mapped(),
                   'fixture/deck did not load')
            window_id = xdo('search', '--name', '^' + title + '$').splitlines()[-1]
            assert window.now_tab is None
            click(window, window.deck.title_action, window_id)
            settle(lambda: window.now_tab == 'next' and window.now_layer.get_visible(),
                   'mouse title entry did not open Up next')
            if width > 639:
                click(window, control(window, 'td-now-about'), window_id)
                settle(lambda: window.now_tab == 'about', 'mouse About action failed')
                click(window, control(window, 'td-now-next'), window_id)
                settle(lambda: window.now_tab == 'next', 'mouse Up next action failed')
            click(window, control(window, 'td-now-close'), window_id)
            settle(lambda: window.now_tab is None and not window.now_layer.get_visible(),
                   'mouse close failed')
            assert source.player == original

            key(window.deck.title_action, 'Return', window_id)
            settle(lambda: window.now_tab == 'next', 'Enter title entry failed')
            if width > 639:
                key(control(window, 'td-now-about'), 'Return', window_id)
                settle(lambda: window.now_tab == 'about', 'keyboard About action failed')
                key(control(window, 'td-now-next'), 'Return', window_id)
                settle(lambda: window.now_tab == 'next', 'keyboard Up next action failed')
            key(None, 'Escape', window_id)
            settle(lambda: window.now_tab is None, 'Escape did not close Now Playing')
            assert source.player == original

            key(window.deck.title_action, 'space', window_id)
            settle(lambda: window.now_tab == 'next', 'Space title entry failed')
            assert source.player == original, 'Space entry changed playback'
            key(control(window, 'td-now-close'), 'Return', window_id)
            settle(lambda: window.now_tab is None and not window.now_layer.get_visible(),
                   'keyboard close failed')
            assert source.player == original
            tabs_checked = 'mouse/keyboard Up next/About; ' if width > 639 else ''
            print(f'PASS {width}x{height}: mouse/Enter/Space title entry; {tabs_checked}close/Escape; playback unchanged')
        finally:
            window.close_resources()
            window.destroy()
            pump()
    assert app.get_windows() == []
finally:
    app.quit()
    sandbox.cleanup()
print('PASS: fixture-only action checks complete; all windows closed')
