"""The quote rule follows document hue and keeps its fractional width."""
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk
from luma_appkit import snapshot_document_quote, lumaui

class DocumentQuote(unittest.TestCase):
    def test_rule_geometry_and_hue(self):
        Gtk.init()
        owner = Gtk.TextView()
        snapshot = Gtk.Snapshot()
        snapshot_document_quote(snapshot, owner, 12, 24, 60, hue=145)
        node = snapshot.to_node()
        bounds = node.get_bounds()
        self.assertEqual((bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()),
                         (12, 24, 2.5, 60))
        expected = Gdk.RGBA(); expected.parse(lumaui.oklch_rgba(.72, .13, 145))
        self.assertTrue(node.get_color().equal(expected))

if __name__ == '__main__':
    unittest.main()
