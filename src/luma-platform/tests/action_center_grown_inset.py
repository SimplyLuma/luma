# SPDX-License-Identifier: Apache-2.0
"""Mapped explicit phone gutter, default/fill preservation, bidirectional resize."""
import time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import ActionCenter, BarAction, install_appkit, install_lumaui
from luma_appkit.lumaui_tokens import ACTION_CENTER


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


app = Adw.Application(application_id="org.projectluma.GrownInsetTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit(); install_lumaui()
w = Gtk.ApplicationWindow(application=app, default_width=402, default_height=700)
w.add_css_class("luma-app-window")
center = ActionCenter()
body = Gtk.Box()
w.set_child(body)
center.attach(body)
center.show_bar([BarAction("info", tooltip="Details")])
w.present(); settle()
resting = center.get_width()
assert center.get_phone_grown_inset() is None
center.grow("details", Gtk.Label(label="Details")); settle()
assert center.get_width() == 378, center.get_width()
center.set_phone_grown_inset(ACTION_CENTER["frame_side"]); settle()
assert center.get_width() == 370, center.get_width()
for width in (720, 1180, 360, 402):
    w.set_default_size(width, 700); settle()
    expected = width - 32 if width < 560 else 380
    assert center.get_width() == expected, (width, center.get_width())
center.fold(); settle()
assert center.get_width() == resting
center.set_phone_grown_inset(None)
center.show_bar([BarAction("x", "Cancel"), BarAction("check", "Save")], fill=True)
center.grow("form", Gtk.Label(label="Form")); settle()
assert center.get_width() == 370, center.get_width()
w.close()
print("ActionCenter grown inset/default/fill and four-width resize PASS")
