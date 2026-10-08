"""Person rows retain compact bounds, native action and visible live presence."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import PanelRow,PersonAvatar,BarTiles,BarTile,install_appkit,install_lumaui
class PanelPerson(unittest.TestCase):
    def test_person_geometry_presence_and_action(self):
        Gtk.init();install_appkit();install_lumaui();calls=[]
        row=PanelRow('Priya Raman',subtitle='Here now',lead=PersonAvatar('Priya',34),
                     appearance='person',live=True,closes=False,on_activate=lambda:calls.append(1))
        add=PanelRow('Add people',subtitle='By name, @username or email',icon='user-plus',appearance='person')
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);box.append(row);box.append(add)
        regular=BarTiles([BarTile('pin','Pinned')],chip=True);box.append(regular)
        compact=BarTiles([BarTile('pin','Pinned')],chip=True,size='compact');box.append(compact)
        window=Gtk.Window(child=box,default_width=354,default_height=400);window.add_css_class('luma-app-window');window.present()
        end=time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertEqual(round(row.compute_bounds(window)[1].get_height()),48)
        self.assertEqual(round(add.compute_bounds(window)[1].get_height()),48)
        self.assertEqual(add.get_child().get_first_child().get_width(),34)
        self.assertEqual(regular.buttons[0].get_height(),72)
        self.assertEqual(compact.buttons[0].get_height(),64)
        text=row.get_child().get_last_child();status=text.get_last_child();dot=status.get_first_child()
        self.assertEqual((dot.get_width(),dot.get_height()),(7,7))
        title=text.get_first_child();self.assertEqual(title.get_pango_context().get_font_description().get_weight(),600)
        row.emit('clicked');self.assertEqual(calls,[1]);window.close()
if __name__=='__main__':unittest.main()
