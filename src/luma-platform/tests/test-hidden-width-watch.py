"""Hidden phone controls must still adapt when their root window changes size."""
import time
import unittest
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from luma_appkit.structure_adapt import WidthWatch
from luma_appkit.structure_listfirst import ListFirst


def settle():
    until = time.monotonic() + 0.6
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


class HiddenWidthWatch(unittest.TestCase):
    def test_list_first_initial_measure(self):
        listing, detail = Gtk.Box(), Gtk.Box()
        listing.set_size_request(200, 100)
        detail.set_size_request(200, 100)
        stack = ListFirst(listing, detail, "Settings")
        self.assertLessEqual(stack.measure(Gtk.Orientation.HORIZONTAL, -1)[0], 360)
        window = Gtk.Window(default_width=1000, default_height=600)
        window.set_child(stack)
        window.present()
        settle()
        self.assertFalse(stack.phone)
        window.destroy()
        settle()

    def test_hidden_control_tracks_root_and_reparent(self):
        child = Gtk.Box(visible=False)
        watch = WidthWatch(child)
        window = Gtk.Window(default_width=360, default_height=600)
        window.set_child(child)
        window.present()
        settle()
        self.assertFalse(child.get_realized())
        self.assertEqual(watch._last, window.get_width())
        self.assertEqual(watch.tier, "phone")
        window.set_default_size(700, 600)
        settle()
        self.assertEqual(window.get_width(), 700)
        self.assertEqual(watch.tier, "compact")
        window.set_child(None)
        other = Gtk.Window(default_width=1000, default_height=600)
        other.set_child(child)
        other.present()
        settle()
        self.assertEqual(watch._last, other.get_width())
        self.assertEqual(watch.tier, "regular")
        other.destroy()
        window.destroy()
        settle()


if __name__ == "__main__":
    unittest.main()
