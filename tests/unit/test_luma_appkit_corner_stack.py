# SPDX-License-Identifier: Apache-2.0
"""The vertical corner releases no geometry to individual apps."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
try:
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gdk, GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
    if HAVE_DISPLAY:
        from luma_appkit import CornerPill, install_appkit
except (ImportError, ValueError):
    HAVE_DISPLAY = False

@unittest.skipUnless(HAVE_DISPLAY, 'needs a GTK display')
class CornerStack(unittest.TestCase):
    def test_two_targets_and_separator_match_phone_stack(self):
        install_appkit()
        calls = []
        pill = CornerPill(actions=(('map', 'Map style', lambda: calls.append('map')),
                                   ('navigation', 'Where am I', lambda: calls.append('locate'))),
                          orientation='vertical')
        window = Gtk.Window(child=pill, default_width=402, default_height=300)
        window.present()
        end = time.monotonic()+.4
        while time.monotonic() < end:
            while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        ok, bounds = pill.compute_bounds(window)
        self.assertTrue(ok)
        self.assertEqual((bounds.get_width(), bounds.get_height()), (52, 101))
        for key in ('actions.0', 'actions.1'):
            button = pill.controls[key]
            ok, rect = button.compute_bounds(window)
            self.assertTrue(ok)
            self.assertEqual((rect.get_width(), rect.get_height()), (44, 44))
            button.emit('clicked')
        self.assertEqual(calls, ['map', 'locate'])
        window.destroy()

    def test_navigation_label_stays_visible_and_fits_phone(self):
        calls=[]
        kept=CornerPill(actions=(('chevron-left','Launch kit',lambda:calls.append('back')),),
                        labelled=True,keep_labels=True)
        normal=CornerPill(actions=(('share','Share',lambda:None),),labelled=True)
        box=Gtk.Box();box.append(kept);box.append(normal)
        window=Gtk.Window(child=box,default_width=360,default_height=200)
        window.present()
        end=time.monotonic()+.4
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertTrue(kept.controls['actions.0'].label_widget.get_visible())
        self.assertFalse(normal.controls['actions.0'].label_widget.get_visible())
        self.assertLess(kept.get_width(),180)
        kept.controls['actions.0'].emit('clicked')
        self.assertEqual(calls,['back'])
        window.destroy()

    def test_default_stays_horizontal(self):
        pill = CornerPill(actions=(('map', 'Map style', lambda: None),))
        self.assertEqual(pill.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertFalse(pill.has_css_class('vertical-stack'))
        with self.assertRaises(ValueError):
            CornerPill(actions=(('map', 'Map', lambda: None),), orientation='diagonal')

if __name__ == '__main__': unittest.main()
