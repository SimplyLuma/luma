"""The shared favorites well owns its heading without duplicate card spacing."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import Favourite,FavouritesStrip,install_appkit,install_lumaui
class HeadedFavourites(unittest.TestCase):
    def test_heading_and_actions_survive_item_replacement(self):
        Gtk.init();install_appkit();install_lumaui()
        opened=[]
        strip=FavouritesStrip([],heading='Favorites',on_open=lambda item:opened.append(item.key))
        window=Gtk.Window(child=strip,default_width=370)
        window.add_css_class('luma-app-window')
        items=[Favourite(name,person=name,key=name) for name in ('Priya','Nora','Dad','Sam')]
        strip.set_items(items);window.present()
        end=time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertEqual(len(strip.buttons),4)
        self.assertLess(strip.get_height(),125)
        self.assertEqual(strip.get_child_at(0,0).get_text(),'Favorites')
        strip.buttons[0].emit('clicked');self.assertEqual(opened,['Priya'])
        strip.set_items(items[:2]);self.assertEqual(strip.get_child_at(0,0).get_text(),'Favorites')
        strip.buttons[1].emit('clicked');self.assertEqual(opened,['Priya','Nora'])
        window.close()
if __name__=='__main__':unittest.main()
