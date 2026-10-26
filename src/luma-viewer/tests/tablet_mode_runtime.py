# SPDX-License-Identifier: Apache-2.0
"""Actual Viewer mode controls and safe area with a generated large image.

Run on an isolated server display/home. No physical input/device is exercised.
"""
import hashlib
import cProfile
import os
from pathlib import Path
import tempfile
import time
import pstats

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib
from luma_viewer.application import ViewerApplication


def settle(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('Viewer did not settle')


with tempfile.TemporaryDirectory(prefix='viewer-tablet-mode-') as temporary:
    root = Path(temporary)
    for variable in ('HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        path = root / variable
        path.mkdir()
        os.environ[variable] = str(path)
    os.environ['LUMA_DEVICE_CLASS'] = 'tablet'
    image = root / 'generated.png'
    pixels = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 3200, 2400)
    pixels.fill(0x7396b2ff)
    pixels.savev(str(image), 'png', [], [])
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    app = ViewerApplication()
    assert app.register(None) and not app.get_is_remote()
    app.opening = str(image)
    app.activate()
    window = app.get_windows()[0]
    window.set_default_size(1280, 800)
    settle(lambda: window.loaded and window.corner_slot.get_width() > 0)
    import unittest
    from gi.repository import Gtk
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_image_*.py')
    assert unittest.TextTestRunner().run(suite).wasSuccessful()
    controllers = window.marks_area.observe_controllers()
    installed = [controllers.get_item(i) for i in range(controllers.get_n_items())]
    assert sum(isinstance(c, Gtk.GestureStylus) for c in installed) == 1
    assert sum(isinstance(c, Gtk.GestureDrag) for c in installed) == 1
    assert next(c for c in installed if isinstance(c, Gtk.GestureStylus)).get_stylus_only()
    stable_targets = dict(window.mode_buttons)
    durations = []
    callbacks = []
    profile = cProfile.Profile() if os.environ.get('VIEWER_PROFILE') else None
    for mode in ['markup', 'adjust', 'view'] * 5:
        button = window.mode_buttons[mode]
        started = time.monotonic()
        if profile:
            profile.enable()
        button.emit('clicked')
        callbacks.append(time.monotonic() - started)
        settle(lambda: window.mode == mode and window.corner_slot.get_child() is not None
               and window.corner_slot.get_child().get_width() > 0)
        if profile:
            profile.disable()
        durations.append(time.monotonic() - started)
        assert all(window.mode_buttons[key] is button for key, button in stable_targets.items()), "Mode targets replaced during editing"
        valid, bounds = window.corner_slot.compute_bounds(window)
        _x, transform_y = window.get_surface_transform()
        assert valid and bounds.get_y() - transform_y >= 52, (mode, bounds.get_y(), transform_y)
        # Let the actual native frame/layout settle before the next control.
        until = time.monotonic() + .08
        settle(lambda: time.monotonic() >= until)
    # Callback latency is separate from software-renderer frame quiescence.
    # Draining the main context also renders the mode indicator animation.
    assert max(callbacks) < 1, callbacks
    assert max(durations) < 3, durations
    assert hashlib.sha256(image.read_bytes()).hexdigest() == digest
    window.close()
    app.quit()
    print(f'PASS 15 actual mode activations; maximum callback {max(callbacks):.4f}s; frame settle {max(durations):.4f}s; Tablet status safe area and original retained')
    if profile:
        pstats.Stats(profile).sort_stats('cumulative').print_stats(25)
