"""Folding a deck's range preserves its live value and user callback."""
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from luma_appkit import MediaTransport, install_appkit, install_lumaui


class CompactVolume(unittest.TestCase):
    def test_range_folds_without_replacing_adjustment(self):
        Gtk.init(); install_appkit(); install_lumaui()
        changed = []
        transport = MediaTransport('deck', volume=.6, on_volume=changed.append)
        control = transport.volume_control
        scale = transport._volume_range
        adjustment = scale.get_adjustment()
        expanded = control.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        transport.set_volume_compact(True)
        self.assertFalse(scale.get_visible())
        self.assertTrue(control.get_first_child().get_visible())
        self.assertLess(control.measure(Gtk.Orientation.HORIZONTAL, -1)[1], expanded)
        transport.set_volume(.25)
        transport.set_volume_compact(False)
        self.assertTrue(scale.get_visible())
        self.assertIs(scale.get_adjustment(), adjustment)
        self.assertAlmostEqual(scale.get_value(), .25)
        scale.emit('change-value', Gtk.ScrollType.JUMP, .8)
        self.assertEqual(changed, [.8])
        MediaTransport('deck').set_volume_compact(True)


if __name__ == '__main__':
    unittest.main()
