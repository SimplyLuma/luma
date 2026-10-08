# SPDX-License-Identifier: Apache-2.0
"""Actual crop mode buttons and Escape preserve the source and saved edits."""
import hashlib
import os
import time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
from gi.repository import Gdk, GLib, Gtk
from luma_viewer.application import ViewerApplication
from luma_appkit import ModeSwitch

def drain(seconds=.1):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def descendants(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from descendants(child)
        child=child.get_next_sibling()

app=ViewerApplication()
assert app.register(None) and not app.get_is_remote()
assert app.get_application_id()=='org.projectluma.Viewer.LumaUIPreview'
app.activate()
window=app.get_windows()[0]
width=int(os.environ['VIEWER_CHECK_WIDTH'])
window.set_default_size(width,740)
deadline=time.monotonic()+30
while not window.loaded and time.monotonic()<deadline:drain()
assert window.loaded and window.fixture
drain(.3)
assert window.get_width()==width
before=hashlib.sha256(Path(window.facts.path).read_bytes()).hexdigest()
window._set_mode('adjust')
window._start_crop()
for aspect in ('square','4:5','orig','16:9','square'):
    # Activate the actual shared mode button with its real app callback.
    switch=next(widget for widget in descendants(window.action_center) if isinstance(widget,ModeSwitch))
    button=switch.buttons[aspect]
    assert button.get_mapped(),'aspect choice must remain visible'
    button.emit('clicked')
    drain()
    assert window.crop_aspect==aspect and window.cropping
    x,y,w,h=window.crop_box
    source_w,source_h=window._page_size()
    assert abs(x+w/2-source_w/2)<.001 and abs(y+h/2-source_h/2)<.001
    assert abs(max(w/source_w,h/source_h)-.9)<.001
    print('ACTUAL CROP MODE',width,aspect,window.crop_box,'PASS',flush=True)
controllers=window.observe_controllers()
keys=next(controllers.get_item(i) for i in range(controllers.get_n_items())
    if controllers.get_item(i).get_name()=='vw-mode-dismiss')
assert keys.get_propagation_phase()==Gtk.PropagationPhase.CAPTURE
assert not keys.emit('key-pressed',Gdk.KEY_a,0,Gdk.ModifierType(0))
assert window.cropping and window.crop is None
window.saving=True
assert not keys.emit('key-pressed',Gdk.KEY_Escape,0,Gdk.ModifierType(0))
assert window.cropping
window.saving=False
assert keys.emit('key-pressed',Gdk.KEY_Escape,0,Gdk.ModifierType(0))
drain()
assert not window.cropping and window.crop is None
print('ACTUAL ESCAPE CANCEL',width,'PASS',flush=True)
window._start_crop()
window._crop_aspect('square')
expected=window.crop_box
crop=next(widget for widget in descendants(window.action_center)
    if isinstance(widget,Gtk.Button) and widget.get_name()=='vw-action-crop')
assert crop.get_mapped(),'crop commit must remain visible'
crop.emit('clicked')
drain()
assert window.crop==expected and not window.cropping
window._start_crop()
assert keys.emit('key-pressed',Gdk.KEY_Escape,0,Gdk.ModifierType(0))
drain()
assert window.crop==expected
assert hashlib.sha256(Path(window.facts.path).read_bytes()).hexdigest()==before
assert not keys.emit('key-pressed',Gdk.KEY_Escape,0,Gdk.ModifierType(0))
window._begin_compare([str(window.recents.samples[key]['path']) for key in ('v6','v7')])
deadline=time.monotonic()+30
while len(window.compare_pixbufs)!=2 and time.monotonic()<deadline:drain()
assert window.compare and len(window.compare_pixbufs)==2
assert keys.emit('key-pressed',Gdk.KEY_Escape,0,Gdk.ModifierType(0))
drain()
assert window.compare is None and not window.comparison_selection
assert window.crop==expected
assert hashlib.sha256(Path(window.facts.path).read_bytes()).hexdigest()==before
print('ACTUAL ESCAPE COMPARISON / SAVING GUARD',width,'PASS',flush=True)
window.destroy()
drain()
assert not app.get_windows()
app.quit()
print('CROP COMMIT/CANCEL / ORIGINAL PRESERVED / CLOSED',width,'PASS',flush=True)
