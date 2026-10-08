# SPDX-License-Identifier: Apache-2.0
"""Statistic wells retain the shared surface and explicit content insets."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk
from luma_appkit import Card, install_appkit


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class StatCard(unittest.TestCase):
    def test_insets_and_shape_reset(self):
        install_appkit()
        child = Gtk.Label(label='Battery')
        card = Card(child, shape='stat', recessed=True)
        window = Gtk.Window(child=card, default_width=360)
        window.set_decorated(False)
        window.present(); settle()
        bounds = child.compute_bounds(window)[1]
        self.assertEqual((bounds.origin.x, bounds.origin.y), (12, 12))
        self.assertEqual(window.get_height()-bounds.origin.y-bounds.size.height, 11)
        self.assertEqual(window.get_width()-bounds.size.width, 24)
        card.set_shape('regular'); settle()
        bounds = child.compute_bounds(window)[1]
        self.assertEqual((bounds.origin.x, bounds.origin.y), (14, 14))
        self.assertTrue(card.has_css_class('recessed'))
        self.assertFalse(card.has_css_class('stat'))
        window.close()
        with self.assertRaises(ValueError):
            Card(shape='unknown')


if __name__ == '__main__':
    unittest.main()
