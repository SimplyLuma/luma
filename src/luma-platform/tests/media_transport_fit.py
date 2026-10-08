# SPDX-License-Identifier: Apache-2.0
"""Attached transport fits a sidebar-constrained viewport at fixed root width."""
import time
import gi
gi.require_version('Gtk', '4.0');gi.require_version('Adw', '1')
from gi.repository import Adw,Gio,GLib,Gtk
from luma_appkit import MediaTransport,install_appkit,install_lumaui

def settle():
    until=time.monotonic()+.4
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)

app=Adw.Application(application_id='org.projectluma.TransportFit',flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit();install_lumaui()
w=Gtk.ApplicationWindow(application=app,default_width=720,default_height=500)
w.add_css_class('luma-app-window')
row=Gtk.Box();sidebar=Gtk.Box(width_request=264,visible=False);row.append(sidebar)
viewport=Gtk.Overlay(child=Gtk.Box(hexpand=True,vexpand=True),hexpand=True)
viewport.set_overflow(Gtk.Overflow.HIDDEN);row.append(viewport)
calls=[]
transport=MediaTransport('attached',duration=120,loop=False,on_fullscreen=lambda:calls.append('fullscreen'))
transport.set_valign(Gtk.Align.END)
viewport.add_overlay(transport);viewport.set_measure_overlay(transport,False)
w.set_child(row);w.present();settle()
for visible in (False,True,False,True,False):
    sidebar.set_visible(visible);settle()
    assert w.get_width()==720
    assert transport.has_css_class('phone') == visible,(visible,viewport.get_width(),transport._full_width)
    for key in transport._keys.values():
        if key.get_mapped():
            ok,b=key.compute_bounds(viewport)
            assert ok and b.get_x()>=0 and b.get_x()+b.get_width()<=viewport.get_width(),(key.get_tooltip_text(),b.get_x(),b.get_width(),viewport.get_width())
    assert transport._keys['fullscreen'].get_mapped() != visible
transport._keys['fullscreen'].emit('clicked');assert calls==['fullscreen']
for width in (360,402,1180,720):
    w.set_default_size(width,500);settle()
    assert transport.has_css_class('phone') == (width<transport._full_width)
w.close();print('MediaTransport fixed-root sidebar fit and bidirectional resize PASS')
