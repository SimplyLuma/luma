"""A filling status readout leaves adjacent navigation keys at their own size."""
import unittest
from gi.repository import Gtk
from luma_appkit import ActionCenter, BarAction, BarReadout, ToastHost, install_appkit, install_lumaui
from test_luma_appkit_panel_close import settle


class ReadoutWidth(unittest.TestCase):
    def test_navigation_keys_and_readout_across_phone_widths(self):
        Gtk.init(); install_appkit(); install_lumaui()
        host = ToastHost(Gtk.Box())
        center = ActionCenter().attach(host)
        window = Gtk.Window(child=host, default_width=402, default_height=874)
        window.add_css_class('luma-app-window')
        readout = BarReadout('8 min', '2.0 mi · arrive 3:08 PM', fill=True)
        center.show_bar([readout, BarAction('volume-2', tooltip='Voice'),
                         BarAction('', 'End')], fill=True)
        window.present(); settle()
        voice = readout.widget.get_next_sibling()
        end = voice.get_next_sibling()
        for width in (360, 402, 500, 720, 402):
            window.set_default_size(width, 874); settle()
            if width < 560:
                self.assertEqual(voice.get_width(), 48)
                self.assertLess(end.get_width(), 65)
                self.assertGreater(readout.widget.get_width(), 160)
                ok, bar = center.bar.compute_bounds(host)
                self.assertTrue(ok)
                self.assertEqual(round(bar.get_x()), 16)
                self.assertEqual(round(bar.get_width()), width - 32)
        window.close()
