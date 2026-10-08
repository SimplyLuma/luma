# SPDX-License-Identifier: Apache-2.0
"""The editor slider fits the photo editor while Viewer keeps its own geometry."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk
from luma_appkit import ValueSlider, install_appkit


class EditorSlider(unittest.TestCase):
    def test_geometry_and_changes(self):
        install_appkit()
        host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        host.add_css_class('lumaui-action-center')
        host.add_css_class('phone')
        changes = []
        editor = ValueSlider('Exposure', layout='editor', on_change=changes.append)
        viewer = ValueSlider('Exposure', layout='stacked')
        host.append(editor); host.append(viewer)
        window = Gtk.Window(child=host, default_width=360)
        window.present()
        end = time.monotonic() + .3
        while time.monotonic() < end:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertAlmostEqual(editor.measure(Gtk.Orientation.VERTICAL, 360)[1], 56, delta=1)
        self.assertLess(viewer.measure(Gtk.Orientation.VERTICAL, 360)[1], 49)
        editor.range.set_value(25, notify=True)
        self.assertEqual(changes, [25])
        self.assertEqual(editor.value_label.get_text(), '+25')
        window.close()


if __name__ == '__main__':
    unittest.main()
