# SPDX-License-Identifier: Apache-2.0
"""The closed information pane must not leave a blank frame at the right."""
import time

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib

from luma_viewer.application import ViewerApplication


def until(condition):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if condition():
            return
        time.sleep(.01)
    raise AssertionError('Viewer layout did not settle')


def right_edge(widget, ancestor):
    valid, bounds = widget.compute_bounds(ancestor)
    assert valid
    return bounds.origin.x + bounds.size.width


app = ViewerApplication()
assert app.register(None) and not app.get_is_remote()
app.activate()
window = app.get_windows()[0]
window.set_default_size(1180, 740)
until(lambda: window.main.get_width() > 0)
assert window.sidebar.get_visible()
assert window.sidebar_toggle.shown
window.sidebar_toggle.toggle()
until(lambda: not window.sidebar_toggle.shown)
window.sidebar_toggle.toggle()
until(lambda: window.sidebar_toggle.shown)
assert not window.info.get_visible()
assert abs(window.layout.get_width() - right_edge(window.main, window.layout)) <= 1

window.info.show(open=True, subject=window.facts)
until(lambda: window.info.get_visible() and window.info.get_width() > 0)
until(lambda: abs(window.layout.get_width() - right_edge(window.info, window.layout)) <= 1)

window.info.close()
until(lambda: not window.info.get_visible() and
      abs(window.layout.get_width() - right_edge(window.main, window.layout)) <= 1)
for width in (720,402,360):
    window.set_default_size(width,740)
    until(lambda: window.get_width()==width)
    until(lambda: not window.sidebar_toggle.shown)
    window.sidebar_toggle.toggle()
    until(lambda: window.sidebar_toggle.shown and window.sidebar.get_mapped())
    window.sidebar_toggle.toggle()
    until(lambda: not window.sidebar_toggle.shown)
window.destroy()
app.quit()
print('VIEWER PANE LAYOUT PASS', flush=True)
