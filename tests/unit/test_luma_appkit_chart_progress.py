# SPDX-License-Identifier: Apache-2.0
"""Chart meters paint their series ink and follow live accent changes."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GLib, Graphene, Gtk
from PIL import Image
from luma_appkit import ProgressLine, install_appkit


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class ChartProgress(unittest.TestCase):
    def test_series_ink_and_default(self):
        install_appkit()
        chart = ProgressLine(.5, tone='chart')
        default = ProgressLine(.5)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(chart); box.append(default)
        window = Gtk.Window(child=box, default_width=300)
        window.present(); settle()
        def pixel(widget):
            snapshot = Gtk.Snapshot()
            Gtk.WidgetPaintable.new(widget).snapshot(snapshot, widget.get_width(), widget.get_height())
            texture = window.get_renderer().render_texture(snapshot.to_node(),
                Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height()))
            with tempfile.NamedTemporaryFile(suffix='.png') as image:
                texture.save_to_png(image.name)
                with Image.open(image.name) as pixels:
                    return pixels.convert("RGB").getpixel((20, 2))
        for widget, role in ((chart, 'luma_monitor_chart_primary'), (default, 'luma_progress_fill')):
            found, color = widget.get_style_context().lookup_color(role)
            self.assertTrue(found)
            for actual, expected in zip(pixel(widget), (color.red, color.green, color.blue)):
                self.assertAlmostEqual(actual, round(expected * 255), delta=2)
        original = pixel(chart)
        provider = Gtk.CssProvider()
        provider.load_from_string('@define-color luma_accent oklch(0.72 0.13 30);')
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, provider, 1000)
        try:
            settle()
            changed = pixel(chart)
            self.assertGreater(original[2], original[0])
            self.assertGreater(changed[0], changed[2])
        finally:
            Gtk.StyleContext.remove_provider_for_display(display, provider)
            window.close()


if __name__ == '__main__':
    unittest.main()
