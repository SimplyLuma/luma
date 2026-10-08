# SPDX-License-Identifier: Apache-2.0
"""Raised media-bar actions retain dark ink, including when hovered."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gdk, GLib, Gtk
from luma_appkit import BarAction, lumaui
from luma_appkit.action_center import make_control

@unittest.skipUnless(Gtk.init_check() and Gdk.Display.get_default(), 'requires mapped GTK display')
class MediaPrimary(unittest.TestCase):
    def test_primary_ink_over_media(self):
        lumaui.install(Gdk.Display.get_default())
        box=Gtk.Box(css_classes=['lumaui-media'])
        button=make_control(BarAction('', 'Open in Photos', primary=True))
        box.append(button)
        window=Gtk.Window(child=box,default_width=360,default_height=120)
        window.add_css_class('luma-app-window')
        window.present()
        try:
            for flag in (Gtk.StateFlags.NORMAL,Gtk.StateFlags.PRELIGHT):
                button.set_state_flags(flag,False)
                until=time.monotonic()+.25
                while time.monotonic()<until:
                    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                    time.sleep(.005)
                self.assertTrue(button.get_mapped())
                context=button.get_style_context()
                found,expected=context.lookup_color('luma_media_key_ink')
                self.assertTrue(found)
                for widget in (button, button.get_child()):
                    actual=widget.get_style_context().get_color()
                    for channel in ('red','green','blue'):
                        self.assertAlmostEqual(getattr(actual,channel),getattr(expected,channel),places=3)
                    self.assertLess(actual.red,.2,'white text disappears on the light primary chip')
        finally:window.destroy()

if __name__=='__main__':unittest.main()
