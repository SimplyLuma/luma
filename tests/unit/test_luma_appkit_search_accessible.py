"""A collapsed search is a named activation button; its editor remains a search."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib
from luma_appkit import ActionCenter, BarSearch, install_appkit, install_lumaui
from luma_appkit.action_center import make_control


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class SearchAccessibility(unittest.TestCase):
    def test_collapsed_keyboard_activation_opens_real_search(self):
        Gtk.init(); install_appkit(); install_lumaui()
        for opens in (False, True):
            item = BarSearch('Find music', label='Search', collapsed=not opens, opens=opens)
            center = ActionCenter()
            center.show_bar([item])
            window = Gtk.Window(child=center)
            window.set_default_size(360, 180); window.present(); settle()
            self.assertEqual(item.widget.get_accessible_role(), Gtk.AccessibleRole.BUTTON)
            controllers = item.widget.observe_controllers()
            handled = False
            for index in range(controllers.get_n_items()):
                control = controllers.get_item(index)
                if isinstance(control, Gtk.EventControllerKey):
                    handled = control.emit('key-pressed', Gdk.KEY_Return, 0, Gdk.ModifierType(0)) or handled
            self.assertTrue(handled)
            self.assertTrue(center.searching)
            self.assertEqual(center._search[1].widget.get_accessible_role(), Gtk.AccessibleRole.SEARCH)
            center.close_search()
            self.assertFalse(center.searching)
            window.close(); settle()
        self.assertEqual(make_control(BarSearch()).get_accessible_role(), Gtk.AccessibleRole.SEARCH)


if __name__ == '__main__':
    unittest.main()
