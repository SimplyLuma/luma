# SPDX-License-Identifier: Apache-2.0
"""Releasing the native original-preview control must end the preview."""
import hashlib
import os
import time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib, Gtk
from luma_viewer.application import ViewerApplication

def drain(seconds=.1):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def descendants(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from descendants(child)
        child=child.get_next_sibling()

app=ViewerApplication()
assert app.register(None) and not app.get_is_remote()
app.activate()
window=app.get_windows()[0]
width=int(os.environ['VIEWER_CHECK_WIDTH'])
window.set_default_size(width,740)
deadline=time.monotonic()+30
while not window.loaded and time.monotonic()<deadline:drain()
assert window.loaded and window.fixture
drain(.3)
window._set_mode('adjust')
before=hashlib.sha256(Path(window.facts.path).read_bytes()).hexdigest()
button=next(w for w in descendants(window.action_center)
    if isinstance(w,Gtk.Button) and w.get_tooltip_text()=='Hold to see the original')
controllers=button.observe_controllers()
held=next(controllers.get_item(i) for i in range(controllers.get_n_items())
    if isinstance(controllers.get_item(i),Gtk.GestureClick))
for clicked_first in (False,True):
    held.emit('pressed',1,4.,4.)
    assert window.hold
    # GtkButton and app gesture release handlers can run in either order.
    if clicked_first:button.emit('clicked')
    held.emit('released',1,4.,4.)
    if not clicked_first:button.emit('clicked')
    assert not window.hold, ('original remained visible after release',width,clicked_first)
    drain(.25)
    assert not window.hold
    print('ACTUAL HOLD RELEASE',width,'clicked first',clicked_first,'PASS',flush=True)
held.emit('pressed',1,4.,4.)
assert window.hold
held.emit('cancel',None)
assert not window.hold
drain(.1)
button.emit('clicked')
assert window.hold
# A pointer hold starting during an accessibility/keyboard preview takes
# ownership; the earlier180ms timer cannot end it while still pressed.
held.emit('pressed',1,4.,4.)
drain(.25)
assert window.hold
held.emit('released',1,4.,4.)
assert not window.hold
drain(.1)
held.emit('pressed',1,4.,4.)
held.emit('released',1,4.,4.)
held.emit('pressed',1,4.,4.)
drain(.1)
assert window.hold
held.emit('released',1,4.,4.)
button.emit('clicked')
assert not window.hold
drain(.1)
assert hashlib.sha256(Path(window.facts.path).read_bytes()).hexdigest()==before
window.destroy()
drain()
assert not app.get_windows()
app.quit()
print('HOLD CANCEL / ORIGINAL PRESERVED / CLOSED',width,'PASS',flush=True)
