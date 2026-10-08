# SPDX-License-Identifier: Apache-2.0
"""Actual shared search placement: header omits rule; footer retains its tokens."""
import time
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk, Adw, Gio, GLib
from luma_appkit import SidebarFoot, install_appkit, install_lumaui
app=Adw.Application(application_id='org.projectluma.SidebarHeaderTest',flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit();install_lumaui()
window=Gtk.ApplicationWindow(application=app,default_width=360,default_height=240)
window.add_css_class('luma-app-window')
box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);window.set_child(box)
changed=[];added=[]
header=SidebarFoot(search='Search mail',placement='header',on_search=changed.append,
                   add=('New email','square-pen',lambda:added.append(True)))
footer=SidebarFoot(search='Search mail')
box.append(header);box.append(footer);window.present()
end=time.monotonic()+.35
while time.monotonic()<end:
    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
    time.sleep(.005)
assert header.get_style_context().get_border().top==0
assert header.get_style_context().get_padding().top==0
assert footer.get_style_context().get_border().top==1
assert footer.get_style_context().get_padding().top>0
assert header.field.get_height()==footer.field.get_height()>0
header.entry.set_text('private mail');assert changed==['private mail']
header.add_button.emit('clicked');assert added==[True]
try:SidebarFoot(search='Mail',placement='middle')
except ValueError:pass
else:raise AssertionError('unknown placement must fail')
window.destroy()
print('PASS native header/footer rule, shared search geometry and actual actions')
