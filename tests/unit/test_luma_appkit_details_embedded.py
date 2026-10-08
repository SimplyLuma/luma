"""Details hosted in a panel must stay beside the panel's controls."""
import unittest
from gi.repository import Gtk
from luma_appkit import DetailsPane, install_appkit, install_lumaui
from test_luma_appkit_list_status import settle

class EmbeddedDetails(unittest.TestCase):
    def test_stays_in_parent_at_every_width(self):
        Gtk.init(); install_appkit(); install_lumaui()
        for width in (360, 402, 720, 1180):
            pane = DetailsPane('Details', main=True, embedded=True)
            pane.set_header_visible(False)
            lead = Gtk.Image.new_from_icon_name('image-x-generic-symbolic')
            lead.set_pixel_size(44)
            subject = pane.add_subject('A very long selected photo filename.webp', 'Image · 412 KB', lead=lead)
            pane.add_facts([('Kind', 'Document'), ('Size', '2.3 kB')])
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(pane)
            close = Gtk.Button(label='Done')
            box.append(close)
            window = Gtk.Window(child=box, default_width=width, default_height=740)
            window.add_css_class("luma-app-window")
            window.add_css_class("lumaui-phone-device")
            window.present(); settle()
            self.assertFalse(pane.sheet.has_css_class("luma-island"))
            self.assertFalse(pane.is_drawer)
            self.assertFalse(pane.header.get_visible())
            self.assertEqual(lead.get_height(), 44)
            ok, summary = subject.compute_bounds(pane)
            self.assertTrue(ok)
            self.assertLessEqual(summary.get_x() + summary.get_width(), pane.get_width())
            self.assertIs(pane.sheet.get_parent(), pane)
            self.assertTrue(close.get_mapped())
            self.assertGreater(pane.get_height(), 48)
            self.assertLess(pane.get_height(), 400)
            window.destroy(); settle()
