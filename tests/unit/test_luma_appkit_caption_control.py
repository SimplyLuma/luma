"""Caption controls keep their words and real actions at every width."""
import unittest
from test_luma_appkit_island_resize import settle
from gi.repository import Gtk
from luma_appkit import BarAction, install_appkit, install_lumaui
from luma_appkit.action_center import make_control
class CaptionControl(unittest.TestCase):
    def test_layout_and_actions(self):
        Gtk.init();install_appkit();install_lumaui()
        for width in (360,402,720,1180):
            row=Gtk.Box(spacing=2,homogeneous=True,valign=Gtk.Align.END,margin_start=16,margin_end=16)
            window=Gtk.Window(child=row,default_width=width,default_height=300)
            calls=[]
            controls=[make_control(BarAction('copy','Duplicate',lambda:calls.append(True)),size='caption') for _ in range(4)]
            for key in controls:row.append(key)
            window.present();settle()
            for key in controls:
                self.assertEqual(key.get_height(),52)
                self.assertEqual(key.get_child().get_orientation(),Gtk.Orientation.VERTICAL)
                self.assertTrue(key.bar_words.get_mapped())
                self.assertLessEqual(key.bar_words.get_height(),52)
                key.emit('clicked')
            self.assertEqual(len(calls),4)
            window.close()
if __name__=='__main__':unittest.main()
