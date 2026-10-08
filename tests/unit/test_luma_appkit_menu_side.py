"""An explicitly rising menu keeps its side when it reaches the window edge."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from luma_appkit import install_appkit, install_lumaui
from luma_appkit.action_bubble import FloatingMenu, MenuItem
from luma_appkit.structure_layers import LayerHost


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class MenuSide(unittest.TestCase):
    def test_explicit_above_clamps_instead_of_flipping(self):
        Gtk.init(); install_appkit(); install_lumaui()
        anchor = Gtk.Button(label='Speed', halign=Gtk.Align.END, valign=Gtk.Align.START)
        anchor.set_margin_top(140)
        host = LayerHost(anchor, name='window')
        window = Gtk.Window(child=host, default_width=720, default_height=600)
        window.add_css_class('luma-app-window')
        window.present(); settle()
        rows = ['Speed'] + [MenuItem(str(rate)) for rate in (.75, 1, 1.25, 1.5, 2)]
        rows += [None, MenuItem('Skip silences'), MenuItem('Enhance voice')]
        menu = FloatingMenu(rows).popup(anchor, side='above'); settle()
        self.assertFalse(menu.has_css_class('below'))
        _, bounds = menu.compute_bounds(host)
        self.assertEqual(round(bounds.get_y()), 8)
        self.assertLessEqual(bounds.get_y() + bounds.get_height(), host.get_height() - 8)
        menu.close()
        menu = FloatingMenu(rows).popup(anchor); settle()
        self.assertTrue(menu.has_css_class('below'))
        menu.close(); window.close()


if __name__ == '__main__':
    unittest.main()
