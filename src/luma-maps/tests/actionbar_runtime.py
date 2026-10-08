#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual mapped Maps controls, input and responsive ownership on private data."""
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

_private = tempfile.TemporaryDirectory(prefix='maps-actionbar-')
root = Path(_private.name)
for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
    os.environ[key] = str(root / key)
schema = Path(os.environ['LUMA_CREATOR_SCHEMA_FILE'])
(root / schema.name).write_bytes(schema.read_bytes())
subprocess.run(['glib-compile-schemas', str(root)], check=True)
os.environ.update(GSETTINGS_BACKEND='memory', GSETTINGS_SCHEMA_DIR=str(root), LUMA_FORM_FACTOR='desktop')
assert os.environ.get('LUMA_MAPS_FIXTURE'), 'The map must use private fixture data'
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkX11', '4.0')
from gi.repository import Adw, Gtk, GLib, Gio, GdkX11
from luma_appkit import IconOnlyButton, icons
from luma_maps.application import MapsApplication, MapsWindow


def settle(seconds=.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def screenshot(window, name):
    directory = os.environ.get('LUMA_CREATOR_REVIEW_CAPTURE')
    if not directory:
        return
    from gi.repository import Gsk
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    assert node is not None
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(window.get_surface())
    try:
        renderer.render_texture(node, None).save_to_png(str(destination / (name + '.png')))
    finally:
        renderer.unrealize()


def click(window, button):
    ok, bounds = button.compute_bounds(window)
    assert ok and button.get_mapped()
    surface = window.get_surface()
    assert isinstance(surface, GdkX11.X11Surface), 'Native XTest input needs Xvfb'
    x11, xtst = ctypes.CDLL('libX11.so.6'), ctypes.CDLL('libXtst.so.6')
    x11.XOpenDisplay.restype = ctypes.c_void_p
    display = x11.XOpenDisplay(None)
    assert display
    try:
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        assert x11.XTranslateCoordinates(display, surface.get_xid(), x11.XDefaultRootWindow(display),
            int(bounds.get_x() + bounds.get_width() / 2), int(bounds.get_y() + bounds.get_height() / 2),
            ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeMotionEvent(display, -1, x.value, y.value, 0)
        xtst.XTestFakeButtonEvent(display, 1, 1, 0)
        xtst.XTestFakeButtonEvent(display, 1, 0, 0)
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XSync(display, 0)
    finally:
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay(display)
    settle()


app = MapsApplication()
app.set_application_id('org.projectluma.Maps.ActionbarTest')
assert app.register(None)
checks = 0
with patch('urllib.request.urlopen', side_effect=AssertionError('No network in this gate')):
    for dark in (False, True):
        Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment', 'dark' if dark else 'light')
        window = MapsWindow(app)
        window.present()
        buttons = {}
        for width in (1040, 720, 500, 360, 720, 1040):
            window.set_default_size(width, 828)
            settle(.6)
            assert window.get_surface().get_width() == width
            assert Adw.StyleManager.get_default().get_dark() is dark
            if window.phone:
                assert all(not button.get_mapped() for button in buttons.values())
                if window.action_center.grown == 'search':
                    window.action_center.fold()
                    settle(.5)
                assert window.action_center.bar_row.get_mapped(), (window.action_center.state, window.action_center.grown)
                checks += 1
                continue
            buttons = {key: next(child for child in walk(window.action_center)
                if isinstance(child, Gtk.Button) and child.get_tooltip_text() == label)
                for key, label in [('zoom-in', 'Zoom in'), ('zoom-out', 'Zoom out'), ('locate', 'Where am I')]}
            screenshot(window, f'maps-actionbar-{width}-{int(dark)}')
            previous = None
            for key, label in [('zoom-in', 'Zoom in'), ('zoom-out', 'Zoom out'), ('locate', 'Where am I')]:
                button = buttons[key]
                assert isinstance(button, IconOnlyButton) and button.has_css_class('raised')
                assert button.get_tooltip_text() == label and button.get_mapped()
                assert Gtk.IconTheme.get_for_display(window.get_display()).has_icon(icons.icon_name(button.icon))
                assert button.get_width() == button.get_height() and button.get_width() >= 32
                ok, bounds = button.compute_bounds(window.action_center.bar)
                assert ok and bounds.get_x() >= 0 and bounds.get_y() >= 0
                assert bounds.get_x() + bounds.get_width() <= window.action_center.bar.get_width()
                if previous is not None:
                    assert bounds.get_x() >= previous.get_x() + previous.get_width()
                previous = bounds
                assert button.grab_focus() and window.get_focus() is button
                checks += 1
            zoom = window.fixture_map.view['zoom']
            click(window, buttons['zoom-in'])
            assert window.fixture_map.view['zoom'] > zoom
            click(window, buttons['zoom-out'])
            assert abs(window.fixture_map.view['zoom'] - zoom) < .0001
            click(window, buttons['locate'])
            assert window.fixture_map.view == {'x': 760, 'y': 900, 'zoom': 1.3}
            window.search_field.entry.set_text('coffee')
            settle()
            assert window.state.query == 'coffee' and window.results
            window.search_field.entry.set_text('')
            settle()
            assert window.state.query == ''
            checks += 4
        window.close()
        settle()
app.quit()
print(f'PASS Maps raised shared controls, native zoom/location clicks, search and desktop/phone transitions: {checks} checks; private map data only', flush=True)
