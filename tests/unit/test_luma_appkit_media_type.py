"""Rendered media roles match v71 rather than inheriting generic heading sizes."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango
from luma_appkit import apply_type, install_appkit, install_lumaui


class MediaType(unittest.TestCase):
    def test_rendered_media_fonts(self):
        Gtk.init(); install_appkit(); install_lumaui()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        roles = [('feature-title',28,700),('now-title',22,700),('now-display',32,700),
                 ('album-hero-title',58,700),('album-hero-title-compact',36,700),
                 ('album-artist',16,500),('media-credit',16,400)]
        labels = []
        for role, size, weight in roles:
            label = apply_type(Gtk.Label(label='Pet Sounds'), role)
            column.append(label); labels.append((label,size,weight))
        window = Gtk.Window(child=column)
        window.add_css_class('luma-app-window')
        window.present()
        end = time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        for label,size,weight in labels:
            font=label.get_pango_context().get_font_description()
            self.assertEqual(font.get_size()/Pango.SCALE,size)
            self.assertEqual(int(font.get_weight()),weight)
        window.close()


if __name__ == '__main__':
    unittest.main()
