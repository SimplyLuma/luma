# SPDX-License-Identifier: Apache-2.0
"""Session thumbnails fit their declared viewport without cropping a larger minimum."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from luma_appkit import BarThumbnail, install_appkit
from luma_appkit.action_center import make_control

class ThumbnailCompact(unittest.TestCase):
    def test_sizes_and_activation(self):
        install_appkit()
        for size, width in [('regular', 44), ('compact', 36)]:
            activated = []
            tile = make_control(BarThumbnail(None, label='Last photo', size=size,
                                            on_activate=lambda: activated.append(True)))
            self.assertEqual(tile.measure(Gtk.Orientation.HORIZONTAL, -1)[0], width)
            self.assertEqual(tile.measure(Gtk.Orientation.VERTICAL, width)[0], 36)
            tile.emit('clicked')
            self.assertEqual(activated, [True])
        with self.assertRaises(ValueError):
            BarThumbnail(None, size='invalid')

if __name__ == '__main__': unittest.main()
