# SPDX-License-Identifier: Apache-2.0
"""Separate welcome/editor geometry while retaining the original app record."""
import json
import os
from pathlib import Path
import tempfile
import time
import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GLib
from luma_appkit import AppWindow, CommandRegistry
from luma_appkit import window_policy

# Scoped geometry is the kit's own record, which is the fallback store when
# Tiling Shell's window memory is not the one placing windows.
os.environ['LUMA_WINDOW_MEMORY'] = 'app'


def settle(predicate=lambda: True):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Window geometry did not settle')


with tempfile.TemporaryDirectory() as root:
    os.environ['XDG_STATE_HOME'] = root
    app = Adw.Application(application_id='org.projectluma.GeometryTest', flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    record = Path(root) / 'luma/windows/org.projectluma.GeometryTest.json'
    record.parent.mkdir(parents=True)
    editor = {'width': 1240, 'height': 800, 'maximized': True, 'selected': 'keep this'}
    record.write_text(json.dumps(editor))
    def window(scope):
        return AppWindow(application=app, app_id='org.projectluma.GeometryTest', title='Geometry',
                         icon_name='application-x-executable', commands=CommandRegistry(()),
                         geometry_scope=scope, default_width=900, default_height=560,
                         minimum_width=360, minimum_height=294)
    welcome = window('welcome')
    # A surface with nothing remembered opens by the shared rule (ADR-042),
    # not at the pixels the application named.
    monitor = welcome.get_display().get_monitors().get_item(0).get_geometry()
    opening = window_policy.opening_size(
        screen=window_policy.Screen(monitor.width, monitor.height),
        default=(900, 560), minimum=(360, 294), narrow=0, declared='auto')
    calls = []
    welcome.maximize = lambda: calls.append('maximize')
    welcome.present(); settle(lambda: welcome.get_allocated_width() == opening[0])
    assert welcome.get_default_size() == opening, (welcome.get_default_size(), opening)
    assert welcome.get_allocated_height() == opening[1]
    assert not calls, 'The welcome inherited the editor’s maximization'
    dialog = Adw.AlertDialog(heading='Save this private fixture?')
    dialog.add_response('cancel', 'Cancel'); dialog.add_response('save', 'Save')
    dialog.set_close_response('cancel'); dialog.set_default_response('save')
    dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
    dialog.present(welcome); settle(lambda: dialog.get_mapped())
    assert dialog.has_css_class('luma-controls-quiet')
    dialog.close(); settle(lambda: welcome.get_visible_dialog() is None)
    assert json.loads(record.read_text()) == editor
    assert welcome.recall('selected') == 'keep this'
    welcome.remember('selected', 'new selection')
    saved = json.loads(record.read_text())
    assert (saved['width'], saved['height'], saved['maximized']) == (1240, 800, True)
    welcome.set_geometry_scope('', default_width=1240, default_height=800)
    settle(lambda: bool(calls))
    assert calls == ['maximize'], 'Switching to the editor lost its explicit maximization'
    assert welcome.get_default_size() == (1240, 800)
    welcome.destroy(); settle()
    # Direct file-open/editor windows continue to use the original record.
    direct = window('')
    direct.maximize = lambda: calls.append('direct maximize')
    settle(lambda: len(calls) == 2)
    assert direct.get_default_size() == (1240, 800)
    direct.destroy(); settle()
    again = window('welcome')
    again.maximize = lambda: calls.append('wrong welcome maximize')
    again.present(); settle(lambda: again.get_allocated_width() > 0)
    assert (again.get_allocated_width(), again.get_allocated_height()) == opening
    assert len(calls) == 2
    again.destroy(); settle()
    try:
        window('../outside')
    except ValueError:
        pass
    else:
        raise AssertionError('Unsafe geometry scope accepted')
print('PASS: fresh welcome at the shared opening size, separate saved editor/maximize, direct editor, metadata preservation, stable restored bounds')
