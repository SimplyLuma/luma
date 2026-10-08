"""Apps with mobile Places controls never open a sidebar at phone widths."""
import unittest
from gi.repository import Gtk
from luma_appkit import SidebarToggle, install_appkit, install_lumaui
from test_luma_appkit_island_resize import settle

class PhoneDisabled(unittest.TestCase):
    def test_open_compact_drawer_closes_on_phone_and_cannot_reopen(self):
        Gtk.init();install_appkit();install_lumaui()
        side=Gtk.Box();side.append(Gtk.Label(label='Places'))
        body=Gtk.Box();body.append(side);body.append(Gtk.Label(label='Forecast',hexpand=True))
        toggle=SidebarToggle(side,drawer_below=701,phone_enabled=False)
        body.append(toggle)
        window=Gtk.Window(child=body,default_width=680,default_height=700)
        window.present();settle();toggle.toggle();settle()
        self.assertTrue(toggle.shown);self.assertIsNotNone(toggle._drawer)
        window.set_default_size(402,700);settle()
        self.assertIsNone(toggle._drawer);self.assertFalse(side.get_mapped())
        toggle.toggle();settle();self.assertIsNone(toggle._drawer)
        toggle.set_active(False);toggle.set_active(True);settle()
        self.assertIsNone(toggle._drawer);self.assertFalse(side.get_mapped())
        window.set_default_size(1180,700);settle();self.assertTrue(side.get_mapped())
        window.close()

if __name__=='__main__':unittest.main()
