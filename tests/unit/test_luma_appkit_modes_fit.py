"""Constrained text-only corners retain every action and mode."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango
from luma_appkit import BarSearch, CornerPill, ModeSwitch, install_appkit, install_lumaui


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class FittingModes(unittest.TestCase):
    def test_corner_fits_and_modes_remain_operable(self):
        Gtk.init(); install_appkit(); install_lumaui()
        changes = []
        modes = ModeSwitch([('albums', 'Albums'), ('artists', 'Artists'), ('songs', 'Songs')],
                           ellipsize=True, on_change=changes.append)
        corner = CornerPill(modes=modes, actions=[('search', 'Search', lambda: None),
                                                ('library', 'Sources', lambda: None)])
        overlay = Gtk.Overlay(child=Gtk.Box())
        overlay.add_overlay(corner)
        window = Gtk.Window(child=overlay)
        for width in (420, 248, 360):
            window.set_default_size(width, 160); window.present(); settle()
            self.assertEqual(window.get_width(), width)
            for control in [*modes.buttons.values(), corner.controls['actions.0'], corner.controls['actions.1']]:
                ok, bounds = control.compute_bounds(overlay)
                self.assertTrue(ok)
                self.assertGreaterEqual(bounds.get_x(), 0)
                self.assertLessEqual(bounds.get_x() + bounds.get_width(), width)
                self.assertGreaterEqual(bounds.get_width(), 28)
            modes.buttons['artists'].set_active(True)
            self.assertEqual(modes.current, 'artists')
        self.assertEqual(changes, ['artists'])
        self.assertEqual(modes.buttons['albums'].get_tooltip_text(), 'Albums')
        ordinary = ModeSwitch([('a', 'Albums'), ('b', 'Artists')])
        self.assertEqual(ordinary.buttons['a'].label_widget.get_ellipsize(), Pango.EllipsizeMode.NONE)
        window.close()

    def test_search_wraps_and_preserves_value_focus_and_close(self):
        Gtk.init(); install_appkit(); install_lumaui()
        closed = []
        search = BarSearch(label='Search music', on_close=lambda: closed.append(True))
        modes = ModeSwitch([('albums', 'Albums'), ('artists', 'Artists'), ('songs', 'Songs')], ellipsize=True)
        corner = CornerPill(modes=modes, search=search, actions=[('library', 'Sources', lambda: None)])
        overlay = Gtk.Overlay(child=Gtk.Box())
        overlay.add_overlay(corner)
        window = Gtk.Window(child=overlay)
        search.set_text('Miles')
        for width in (600, 248, 600):
            window.set_default_size(width, 180); window.present(); settle()
            self.assertEqual(corner._search_wrapped, width == 248)
            for widget in [search.widget, *modes.buttons.values(), corner.controls['actions.0']]:
                ok, bounds = widget.compute_bounds(overlay)
                self.assertTrue(ok)
                self.assertGreaterEqual(bounds.get_x(), 0)
                self.assertLessEqual(bounds.get_x() + bounds.get_width(), width)
            self.assertEqual(search.text, 'Miles')
            search.focus()
            self.assertIs(window.get_focus(), search.entry)
        window.set_default_size(248, 180); settle()
        corner.edit(); settle()
        self.assertFalse(search.widget.get_mapped())
        self.assertTrue(corner.done_button.get_mapped())
        corner.stop_editing(); settle()
        self.assertTrue(search.widget.get_mapped())
        search.clear_button.emit('clicked')
        self.assertEqual(closed, [True])
        self.assertEqual(search.text, '')
        window.close()


if __name__ == '__main__':
    unittest.main()
