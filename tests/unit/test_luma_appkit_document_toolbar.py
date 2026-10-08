# SPDX-License-Identifier: Apache-2.0
"""Writing toolbar controls keep their targets and primary chip inside the bar."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
try:
    import gi
    gi.require_version('Gtk','4.0')
    from gi.repository import Gdk, GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_DISPLAY = False

@unittest.skipUnless(HAVE_DISPLAY, 'needs GTK display')
class DocumentToolbar(unittest.TestCase):
    def test_phone_widths_and_action(self):
        from luma_appkit import ActionCenter, BarAction, RULE, ToastHost, install_appkit
        install_appkit()
        for width in (360,402,500):
            with self.subTest(width=width):
                calls=[]
                host=ToastHost(Gtk.Box())
                window=Gtk.Window(child=host, default_width=width, default_height=874)
                window.add_css_class('luma-app-window')
                center=ActionCenter().attach(host)
                center.show_bar([BarAction('', 'Aa', text_glyph=True, tooltip='Formatting'),
                    BarAction('list-checks', tooltip='Checklist'), BarAction('image',tooltip='Photo'),
                    RULE, BarAction('square-pen',tooltip='New note',primary=True,on_activate=lambda:calls.append('new'))],toolbar=True)
                window.present()
                end=time.monotonic()+.5
                while time.monotonic()<end:
                    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                    time.sleep(.005)
                child=center.bar_row.get_first_child()
                while child:
                    ok,bounds=child.compute_bounds(center)
                    self.assertTrue(ok)
                    if isinstance(child,Gtk.Button):
                        self.assertEqual((bounds.get_width(),bounds.get_height()),(48,48))
                        self.assertGreaterEqual(bounds.get_x(),6)
                        self.assertLessEqual(bounds.get_x()+bounds.get_width(),center.get_width()-6)
                        if child.has_css_class('primary'):child.emit('clicked')
                    else:
                        self.assertTrue(child.has_css_class('explicit-rule'))
                        self.assertEqual(bounds.get_height(),28)
                    child=child.get_next_sibling()
                self.assertEqual(calls,['new'])
                self.assertFalse(center._overflow)
                center.grow('format', Gtk.Label(label='Formatting'))
                end=time.monotonic()+.4
                while time.monotonic()<end:
                    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                    time.sleep(.005)
                child=center.bar_row.get_first_child()
                while child:
                    if isinstance(child,Gtk.Button):
                        self.assertEqual(child.get_allocated_width(),48)
                    child=child.get_next_sibling()
                self.assertEqual(center.bar.get_allocated_width(),width-24)
                center.fold()
                window.destroy()

if __name__=='__main__':unittest.main()
