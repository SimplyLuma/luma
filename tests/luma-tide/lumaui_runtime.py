# SPDX-License-Identifier: Apache-2.0
"""Shared setup for package runtime checks of the v70 port.

The package copies these checks into tests/ beside luma_tide/. Resolve styles
from the imported package, not a checkout-relative path. Every store and
window-state write belongs to a fresh temporary directory.
"""
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkX11', '4.0')
from gi.repository import Adw, GdkX11, Gio, GLib, Gtk
from luma_appkit import add_style_sheet, install_appkit, install_lumaui
import luma_tide
from luma_tide.model import LibraryStore
from luma_tide.resources import stylesheet_path
from luma_tide.playback import PlaybackController
from test_playback import FakeEngine


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def settle(predicate, timeout=10, what='Tide'):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError(what + ' did not settle')


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def named(window, name):
    return next((widget for widget in walk(window) if widget.get_name() == name), None)


def titles(window):
    return [song.title for album in window.library.albums for song in album.songs]


def count(window):
    return sum(len(album.songs) for album in window.library.albums)


def xdo(*args):
    return subprocess.run(['xdotool', *args], check=True, capture_output=True,
                          text=True, timeout=5).stdout.strip()


def window_id(window):
    identifier = str(GdkX11.X11Surface.get_xid(window.get_surface()))
    xdo('windowfocus', '--sync', identifier)
    pump()
    return identifier


def click(window, widget, presses=1):
    assert widget is not None
    settle(lambda: widget.get_mapped() and widget.get_width() > 0 and widget.get_height() > 0,
           what='pointer target')
    # Let the frame clock finish allocations after a view or playback change.
    time.sleep(.15)
    pump()
    identifier = window_id(window)
    geometry = dict(line.split('=', 1) for line in
                    xdo('getwindowgeometry', '--shell', identifier).splitlines() if '=' in line)
    ok, bounds = widget.compute_bounds(window)
    assert ok
    # GTK widget coordinates exclude the native CSD surface translation.
    offset_x, offset_y = window.get_surface_transform()
    x = round(int(geometry['X']) + offset_x + bounds.get_x() + bounds.get_width() / 2)
    y = round(int(geometry['Y']) + offset_y + bounds.get_y() + bounds.get_height() / 2)
    xdo('mousemove', '--sync', str(x), str(y))
    pump()
    time.sleep(.05)
    pump()
    for index in range(presses):
        xdo('mousedown', '1')
        time.sleep(.05)
        xdo('mouseup', '1')
        if index + 1 < presses:
            time.sleep(.03)
    pump()


def key(window, widget, value):
    if widget is not None:
        assert widget.grab_focus()
    window_id(window)
    xdo('key', value)
    pump()


@contextmanager
def test_application(identifier):
    keys = ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'GSETTINGS_BACKEND')
    previous = {key: os.environ.get(key) for key in keys}
    with tempfile.TemporaryDirectory(prefix='tide-runtime-') as temporary:
        root = Path(temporary)
        for key in keys[:-1]:
            os.environ[key] = str(root / key)
        os.environ['GSETTINGS_BACKEND'] = 'memory'
        app = Adw.Application(application_id=identifier, flags=Gio.ApplicationFlags.NON_UNIQUE)
        assert app.register(None)
        install_appkit()
        install_lumaui()
        add_style_sheet(str(stylesheet_path()))
        app.store = LibraryStore(root / 'library.db')
        app.controller = PlaybackController(app.store, FakeEngine())
        app.can_make_offline = lambda ids: False
        app.has_offline = lambda ids: False
        app.notify_error = lambda message: None
        app._syncing = set()
        app.remote = None
        try:
            yield app, root
        finally:
            for window in app.get_windows():
                window.close_resources()
                window.destroy()
            app.controller.close()
            app.store.close()
            app.quit()
            pump()
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
