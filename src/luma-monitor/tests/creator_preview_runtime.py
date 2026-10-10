#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual raised destructive controls and cancel path in a private fixture."""
import os
from pathlib import Path
import tempfile
import time

with tempfile.TemporaryDirectory(prefix='monitor-creator-') as temporary:
    for variable, leaf in (('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state'), ('XDG_DATA_HOME', 'data')):
        os.environ[variable] = str(Path(temporary) / leaf)
    os.environ['LUMA_MONITOR_FIXTURE'] = str(Path(__file__).resolve().parents[3] / 'tests/fixtures/monitor-v70.json')
    from luma_monitor.application import MonitorApplication, MonitorWindow, GLib, Gtk
    from luma_appkit import TextButton

    def settle():
        until = time.monotonic() + .15
        context = GLib.MainContext.default()
        while time.monotonic() < until:
            while context.pending(): context.iteration(False)
            time.sleep(.005)

    def descendants(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from descendants(child)
            child = child.get_next_sibling()

    app = MonitorApplication()
    assert app.register(None)
    window = MonitorWindow(app)
    window.present()
    settle()
    window._pick('a:viola')
    settle()
    control = next(w for w in descendants(window.center.bar) if w.get_name() == 'mn-force')
    assert isinstance(control, TextButton)
    assert control.has_css_class('raised') and control.has_css_class('danger')
    assert control.get_width() > 0 and control.get_height() > 0
    before = tuple(a['id'] for a in window.source.visible_apps())
    control.emit('clicked')
    settle()
    assert window.confirm_dialog.handle is not None
    window.confirm_dialog.handle.cancel()
    settle()
    assert tuple(a['id'] for a in window.source.visible_apps()) == before
    # The normal unit lane stays headless; repeat sampler failure/recovery
    # checks on actual GTK labels inside this existing private display lane.
    import unittest
    from test_initial_samples import InitialSampleTests
    InitialSampleTests.label_factory=staticmethod(Gtk.Label)
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(InitialSampleTests))
    assert result.wasSuccessful() and not result.skipped
    window.close()
    settle()
    assert not app.get_windows()
    print('PASS actual shared raised Force quit, destructive style and cancel without signaling a process')
