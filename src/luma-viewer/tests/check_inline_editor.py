# SPDX-License-Identifier: Apache-2.0
"""Actual native annotation textbox, commit/cancel, phone bounds and originals.

Run privately with the fixture inline-editor state and VIEWER_CHECK_WIDTH.
"""
import hashlib
import os
import time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
from gi.repository import Gdk, GLib, Gtk
from luma_viewer.application import ViewerApplication

def drain(seconds=.1):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def descendants(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from descendants(child)
        child=child.get_next_sibling()

def editor(window):
    width,height=window._page_size()
    window._text_entry((width*.2,height*.2))
    drain()
    field=next(w for w in descendants(window) if w.get_name()=='vw-text-entry')
    assert field.get_mapped() and field.entry.get_mapped()
    return field

app=ViewerApplication()
assert app.register(None) and not app.get_is_remote()
assert app.get_application_id()=='org.projectluma.Viewer.LumaUIPreview'
app.activate()
window=app.get_windows()[0]
width=int(os.environ['VIEWER_CHECK_WIDTH'])
window.set_default_size(width,740)
deadline=time.monotonic()+30
while not window.loaded and time.monotonic()<deadline:
    drain()
assert window.loaded and window.fixture
drain(.5)
if os.environ.get('ADW_DEBUG_HIGH_CONTRAST')=='1':
    from gi.repository import Adw
    assert Adw.StyleManager.get_default().get_high_contrast()
    print('ACTUAL PRIVATE HIGH CONTRAST True',flush=True)
assert window.get_width()==width,(window.get_width(),width)
assert window.mode=='markup' and window.tool=='text'
initial=next(w for w in descendants(window) if w.get_name()=='vw-text-entry-input')
assert initial.get_mapped() and initial.get_accessible_role()==Gtk.AccessibleRole.TEXT_BOX
assert initial.get_placeholder_text()=='Type here' and initial.get_text()==''
ok,bounds=initial.compute_bounds(window)
assert ok and bounds.get_x()>=0 and bounds.get_x()+bounds.get_width()<=width+2
print('CAPTURED INLINE INPUT',width,initial.get_accessible_role(),bounds.get_x(),bounds.get_width(),flush=True)
window._set_mode('markup')
before=hashlib.sha256(Path(window.facts.path).read_bytes()).hexdigest()
for value,key,expected in [('Enter places text',Gdk.KEY_Return,1),('Escape drops text',Gdk.KEY_Escape,1)]:
    field=editor(window)
    field.entry.set_text(value)
    controllers=field.observe_controllers()
    keyboard=next(controllers.get_item(i) for i in range(controllers.get_n_items()) if isinstance(controllers.get_item(i),Gtk.EventControllerKey))
    assert keyboard.emit('key-pressed',key,0,Gdk.ModifierType(0))
    drain()
    assert len(window.history.marks)==expected
    assert not any(w.get_name()=='vw-text-entry' for w in descendants(window))
    assert window.history.marks[0].text=='Enter places text'
    print('INLINE KEY',width,value,'PASS',flush=True)
field=editor(window)
field.entry.set_text('Focus leaving places text')
controllers=field.entry.observe_controllers()
focus=next(controllers.get_item(i) for i in range(controllers.get_n_items()) if isinstance(controllers.get_item(i),Gtk.EventControllerFocus))
focus.emit('leave')
drain()
assert len(window.history.marks)==2
assert window.history.marks[-1].text=='Focus leaving places text'
assert hashlib.sha256(Path(window.facts.path).read_bytes()).hexdigest()==before
print('INLINE FOCUS COMMIT / ORIGINAL PRESERVED',width,'PASS',flush=True)
window.destroy()
drain()
assert not app.get_windows()
app.quit()
print('CLOSED: zero Viewer windows',flush=True)
