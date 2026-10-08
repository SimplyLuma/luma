"""Fixture-only regression for row selection on desktop and phone."""
import os
import time
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib
from luma_monitor.application import MonitorApplication, MonitorWindow
from luma_appkit import ModeSwitch

assert os.environ.get('LUMA_MONITOR_FIXTURE'), 'Run only against a fixture'


def settle():
    end = time.monotonic() + .4
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()


app = MonitorApplication()
assert app.register(None)
window = MonitorWindow(app)
window.present(); settle()
for width in (1180, 402):
    window.set_default_size(width, 874); settle()
    window._deselect(); settle()
    row = next(w for w in descendants(window) if w.get_name() == 'mn-app-viola')
    assert row.has_css_class('mn-app-row')
    assert row.has_css_class('mn-phone-row') == (width == 402)
    row.activate(); settle()
    assert window.selected == 'a:viola'
    print('row selection', width, 'PASS')
window._deselect(); window._resource('en'); settle()
for key in ('saver', 'perf', 'bal'):
    mode = next(w for w in descendants(window) if isinstance(w, ModeSwitch))
    mode.buttons[key].activate(); settle()
    mode = next(w for w in descendants(window) if isinstance(w, ModeSwitch))
    assert mode.current == key and mode.buttons[key].get_active()
print('power mode activation PASS')
window._pick('p:3113'); settle()
window._ask_force(); settle()
assert window.confirm_dialog.handle is not None
assert window.confirm_dialog.has_css_class('frame')
window.confirm_dialog.cancel_button.activate(); settle()
assert window.selected == 'p:3113'
print('phone process Kill modal and cancel PASS')
window.close()
