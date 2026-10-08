# SPDX-License-Identifier: Apache-2.0
"""The compact recessed account is one well, not a nested contact row."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from luma_appkit import AccountCard, install_appkit

class CompactAccount(unittest.TestCase):
    def test_compact_well_geometry_and_activation(self):
        install_appkit()
        activated = []
        card = AccountCard('Nick', compact=True, recessed=True, hue=250,
                           on_activate=lambda: activated.append(True))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        box.append(card)
        window = Gtk.Window(child=box, default_width=382, default_height=160)
        window.present()
        end = time.monotonic() + .4
        while time.monotonic() < end:
            while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        valid, well = card._well.compute_bounds(box)
        self.assertTrue(valid)
        self.assertEqual((well.origin.x, well.origin.y, well.size.width, well.size.height), (2, 0, 378, 54))
        card.emit('clicked')
        self.assertEqual(activated, [True])
        window.destroy()
