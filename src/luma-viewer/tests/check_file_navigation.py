# SPDX-License-Identifier: Apache-2.0
"""Real window arrows navigate files without stealing native text caret keys."""
import hashlib
import os
import time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
from gi.repository import Gdk, GLib, Gtk
from luma_viewer.annotations import Mark
from luma_viewer.application import ViewerApplication


def drain(seconds=.1):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def wait(condition):
    deadline=time.monotonic()+20
    while not condition() and time.monotonic()<deadline:drain()
    assert condition(),'native file navigation timed out'


app=ViewerApplication()
assert app.register(None) and not app.get_is_remote()
app.activate()
window=app.get_windows()[0]
width=int(os.environ['VIEWER_CHECK_WIDTH'])
window.set_default_size(width,740)
wait(lambda:window.loaded)
drain(.3)
assert window.fixture
assets=[Path(sample['path']) for sample in window.recents.samples.values()
    if sample.get('asset')]
originals={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
controllers=window.observe_controllers()
keys=next(controllers.get_item(i) for i in range(controllers.get_n_items())
    if controllers.get_item(i).get_name()=='vw-file-navigation')
assert keys.get_propagation_phase()==Gtk.PropagationPhase.CAPTURE


def key(value,uid):
    window.marks_area.grab_focus()
    assert keys.emit('key-pressed',value,0,Gdk.ModifierType(0))
    expected=window.recents.samples[uid]['path']
    wait(lambda:window.loaded and window.facts.path==expected)
    drain(.1)


key(Gdk.KEY_Down,'receipt')
key(Gdk.KEY_KP_Right,'lease')
assert window.document is not None and len(window.form_entries)==3
field=window.form_entries['tenant']
field.set_text('Caret stays in this PDF field')
field.grab_focus()
drain()
assert isinstance(window.get_focus(),Gtk.Editable)
path=window.facts.path
token=window.load_token
for value in (Gdk.KEY_Up,Gdk.KEY_Down,Gdk.KEY_Left,Gdk.KEY_Right):
    assert not keys.emit('key-pressed',value,0,Gdk.ModifierType(0))
    assert window.facts.path==path and window.load_token==token
assert field.get_text()=='Caret stays in this PDF field'
key(Gdk.KEY_KP_Left,'receipt')
window._set_mode('markup')
window.marks_area.grab_focus()
assert not keys.emit('key-pressed',Gdk.KEY_Right,0,Gdk.ModifierType(0))
assert not keys.emit('key-pressed',Gdk.KEY_Left,0,Gdk.ModifierType(0))
window.tool='select'
window.history.add(Mark('arrow',window.ink,(100,100),(150,150),page=window.page))
window.selected=0
window.marks_area.grab_focus()
drain()
token=window.load_token
assert not keys.emit('key-pressed',Gdk.KEY_Down,0,Gdk.ModifierType(0))
canvas_keys=next(window.marks_area.observe_controllers().get_item(i)
    for i in range(window.marks_area.observe_controllers().get_n_items())
    if isinstance(window.marks_area.observe_controllers().get_item(i),Gtk.EventControllerKey))
assert canvas_keys.emit('key-pressed',Gdk.KEY_Down,0,Gdk.ModifierType(0))
assert window.marks[0].start==(100,101) and window.load_token==token,\
    'Selected mark arrow must move the mark without changing documents'
window.selected=None
key(Gdk.KEY_Up,'offsite')
token=window.load_token
key(Gdk.KEY_Up,'offsite')
assert window.load_token==token,'first endpoint must not reload'
window._set_mode('view')
window._show_recents(search=True)
drain()
assert isinstance(window.get_focus(),Gtk.Editable)
assert not keys.emit('key-pressed',Gdk.KEY_Down,0,Gdk.ModifierType(0))
assert window.load_token==token
window.marks_area.grab_focus()
window.saving=True
assert not keys.emit('key-pressed',Gdk.KEY_Down,0,Gdk.ModifierType(0))
window.saving=False
assert all(hashlib.sha256(path.read_bytes()).hexdigest()==digest
    for path,digest in originals.items())
print('ACTUAL WINDOW FILE ARROWS / PDF AND SEARCH CARETS / MODE AND SAVE GUARDS / ORIGINALS',width,'PASS',flush=True)
window.destroy()
drain()
assert not app.get_windows()
app.quit()
print('CLOSED FILE NAVIGATION',width,'PASS',flush=True)
