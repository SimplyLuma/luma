# SPDX-License-Identifier: Apache-2.0
"""Fixed numerical ModeSwitch stops retain their geometry and selection semantics."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gdk, GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
    if HAVE_DISPLAY:
        from luma_appkit import ModeSwitch, install_appkit
except (ImportError, ValueError):
    HAVE_DISPLAY = False


def settle():
    until = time.monotonic() + .45
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


@unittest.skipUnless(HAVE_DISPLAY, "needs a GTK display")
class FixedStops(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        install_appkit()

    def test_fixed_geometry_and_selection_at_all_widths(self):
        for width in (360, 500, 720, 1024, 1180):
            for count in (3, 4):
                with self.subTest(width=width, count=count):
                    changes = []
                    modes = [(str(i), 'A long translated zoom stop' if i == 0 else f'{i}×') for i in range(count)]
                    switch = ModeSwitch(modes, stop_width=34, fill=True, on_change=changes.append)
                    switch.set_halign(Gtk.Align.START)
                    window = Gtk.Window(default_width=width, default_height=200)
                    window.add_css_class('luma-app-window')
                    window.set_child(switch)
                    window.present()
                    settle()
                    expected = 6 + 34 * count + 2 * (count - 1)
                    ok, outer = switch.compute_bounds(window)
                    self.assertTrue(ok)
                    self.assertEqual(outer.get_width(), expected)
                    self.assertEqual(outer.get_height(), 36)
                    for index, button in enumerate(switch.buttons.values()):
                        self.assertEqual(button.get_width(), 34)
                        ok, bounds = button.compute_bounds(switch.row)
                        self.assertTrue(ok)
                        self.assertEqual(bounds.get_x(), index * 36)
                    self.assertEqual(switch.buttons['0'].get_tooltip_text(), modes[0][1])
                    switch.buttons[str(count - 1)].set_active(True)
                    settle()
                    self.assertEqual(changes, [str(count - 1)])
                    self.assertEqual(switch.current, str(count - 1))
                    self.assertEqual(switch.indicator.get_width(), 34)
                    ok, bounds = switch.indicator.compute_bounds(switch.row)
                    self.assertTrue(ok)
                    self.assertEqual(bounds.get_x(), (count - 1) * 36)
                    switch.set_current('0')
                    self.assertEqual(changes, [str(count - 1)])
                    window.destroy()
                    settle()

    def test_small_property_row_fits_phone_and_preserves_selection(self):
        from gi.repository import Pango
        changes=[]
        switch=ModeSwitch([('saver','Battery saver'),('balanced','Balanced'),('performance','Performance')],
                          size='small',fill=True,on_change=changes.append)
        box=Gtk.Box(margin_start=42,margin_end=42,valign=Gtk.Align.START)
        box.append(switch)
        window=Gtk.Window(default_width=360,default_height=100)
        window.add_css_class('luma-app-window');window.set_child(box);window.present();settle()
        self.assertEqual(window.get_width(),360)
        ok,bounds=switch.compute_bounds(window)
        self.assertTrue(ok)
        self.assertEqual(bounds.get_width(),276)
        for button in switch.buttons.values():
            self.assertEqual(button.get_height(),30)
            name=button.get_child().get_first_child()
            self.assertEqual(name.get_ellipsize(),Pango.EllipsizeMode.END)
            self.assertEqual(name.get_pango_context().get_font_description().get_size()/Pango.SCALE,12)
        switch.buttons['performance'].set_active(True);settle()
        self.assertEqual(changes,['performance'])
        self.assertEqual(switch.current,'performance')
        window.destroy()

    def test_invalid_fixed_width_is_rejected(self):
        for width in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                ModeSwitch([('one', 'One'), ('two', 'Two')], stop_width=width)


if __name__ == '__main__':
    unittest.main()
