"""Application visibility and responsive tier gating are independent."""
import unittest
from gi.repository import Gtk
from luma_appkit import TitleIsland, LayerHost, install_appkit, install_lumaui
from test_luma_appkit_island_resize import settle

class IslandVisibility(unittest.TestCase):
    def test_application_hidden_survives_tiers_and_parent_mapping(self):
        Gtk.init(); install_appkit(); install_lumaui()
        for floating in (False, True):
            with self.subTest(floating=floating):
                body = Gtk.Box()
                host = LayerHost(body, name='window')
                window = Gtk.Window(child=host, default_width=1180, default_height=740)
                island = TitleIsland(title='Viewer', lead='back')
                if floating:
                    island.float_over(host)
                else:
                    body.append(island)
                window.present(); settle()
                self.assertTrue(island.get_visible())
                self.assertFalse(island.get_mapped())
                island.set_visible(True); settle()
                self.assertFalse(island.get_mapped(), 'app show cannot bypass wide tier')
                window.set_default_size(402, 740); settle()
                self.assertTrue(island.get_mapped())
                island.set_visible(False)
                for width in (1180, 402, 360):
                    window.set_default_size(width, 740); settle()
                    host.set_visible(False); settle()
                    host.set_visible(True); settle()
                    self.assertFalse(island.get_visible())
                    self.assertFalse(island.get_mapped(), 'closed viewer reappeared')
                island.set_phone_only(False); settle()
                self.assertFalse(island.get_mapped(), 'mode change overrode app hide')
                island.set_phone_only(True)
                island.set_visible(True); settle()
                self.assertTrue(island.get_mapped())
                window.set_default_size(1180, 740); settle()
                self.assertFalse(island.get_mapped())
                island.set_phone_only(False); settle()
                self.assertTrue(island.get_mapped())
                window.close(); settle()

if __name__ == '__main__': unittest.main()
