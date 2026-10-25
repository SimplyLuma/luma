# SPDX-License-Identifier: Apache-2.0
"""Native details-pane quiescence and allocation lifecycle on a private display."""
import time
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('LumaUI', '1')
from gi.repository import GObject, GLib, Gtk, LumaUI

def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for _ in range(100):
            if not GLib.MainContext.default().pending():
                break
            GLib.MainContext.default().iteration(False)
        time.sleep(.003)

def quiet(window, label):
    pump(.8)
    clock = window.get_frame_clock()
    start = clock.get_frame_counter();pump(1.1)
    frames = clock.get_frame_counter() - start
    assert frames <= 3, (label, frames)
    print('PASS native drawer idle', label, frames, flush=True)

Gtk.init();LumaUI.init()
window = Gtk.Window(default_width=390, default_height=700)
pane = LumaUI.DetailsPane.new('Details', False)
for i in range(40):
    pane.add(Gtk.Label(label='Detail '+str(i)))
box = Gtk.Box();box.append(Gtk.Box(hexpand=True));box.append(pane)
window.set_child(box);window.present();pump(.6)
pane.set_subject(GObject.Object());assert pane.show(True);pump(.6)
assert pane.get_is_drawer()
quiet(window, 'open')
# The modal owns the sheet while shown; its scroller must follow host height.
host = LumaUI.LayerHost.window_host(pane)
sheet = host.get_modal().get_card()
children = []
child = sheet.get_first_child()
while child:
    children.append(child);child = child.get_next_sibling()
scroller = next(child for child in children if isinstance(child, Gtk.ScrolledWindow))
maximum = scroller.get_max_content_height()
window.set_default_size(390, 500);pump(.8)
assert scroller.get_max_content_height() < maximum
quiet(window, 'resized')
window.set_visible(False);pump(.2);window.present();pump(.8)
assert pane.get_is_drawer();quiet(window, 'remapped')
window.set_default_size(1000, 700);pump(.8)
assert not pane.get_is_drawer();quiet(window, 'side')
window.set_default_size(390, 700);pump(.8)
assert pane.get_is_drawer();pane.close();pump(.8)
assert not pane.get_is_drawer();quiet(window, 'closed')
window.destroy();pump(.2)
