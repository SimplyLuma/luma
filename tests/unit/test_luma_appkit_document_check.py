# SPDX-License-Identifier: Apache-2.0
"""Document markers and compact marks render without editor-owned painting."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
try:
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gdk, GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
    if HAVE_DISPLAY:
        from luma_appkit import DocumentCheck, snapshot_document_check, Mark, MarkValue, install_appkit
except (ImportError, ValueError):
    HAVE_DISPLAY = False

@unittest.skipUnless(HAVE_DISPLAY, 'needs a GTK display')
class DocumentMarkers(unittest.TestCase):
    def test_rendered_sizes_and_editor_snapshot(self):
        install_appkit()
        box = Gtk.Box(spacing=10, halign=Gtk.Align.START, valign=Gtk.Align.START)
        controls = [DocumentCheck(), DocumentCheck(checked=True, hue=285),
                    DocumentCheck(appearance='outline'), Mark(density='card'), Mark(density='filter')]
        for control in controls: box.append(control)
        window = Gtk.Window(child=box, default_width=402, default_height=200)
        window.present()
        end = time.monotonic()+.4
        while time.monotonic() < end:
            while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        for control, size in zip(controls, (17,17,16,7,8)):
            ok, bounds = control.compute_bounds(window)
            self.assertTrue(ok)
            self.assertEqual((bounds.get_width(),bounds.get_height()), (size,size))
        for checked in (False,True):
            snapshot = Gtk.Snapshot()
            snapshot_document_check(snapshot, controls[0], 2, 5, checked=checked, hue=285)
            paintable = snapshot.to_paintable(None)
            self.assertGreater(paintable.get_intrinsic_width(), 0)
        controls[0].set_checked(True)
        self.assertTrue(controls[0].checked)
        with self.assertRaises(ValueError): controls[3].set_value(MarkValue('icon',icon='book'))
        window.destroy()

if __name__ == '__main__': unittest.main()
