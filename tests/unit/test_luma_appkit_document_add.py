"""Document add actions retain native activation and the inline source geometry."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import AddRow,install_appkit,install_lumaui
class DocumentAdd(unittest.TestCase):
    def test_inline_geometry_and_activation(self):
        Gtk.init();install_appkit();install_lumaui()
        calls=[]
        row=AddRow('New note inside',icon='plus',appearance='document',on_activate=lambda:calls.append(1))
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);box.set_margin_start(16);box.append(row)
        ordinary=AddRow('Add person');box.append(ordinary)
        window=Gtk.Window(child=box,default_width=402,default_height=200);window.add_css_class('luma-app-window');window.present()
        end=time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        _,bounds=row.compute_bounds(window)
        mark=row.get_child().get_first_child()
        _,glyph=mark.compute_bounds(window)
        self.assertEqual(round(bounds.get_height()),32)
        self.assertEqual((round(glyph.get_x()),round(glyph.get_width())),(16,15))
        self.assertLess(bounds.get_width(),200)
        self.assertGreater(bounds.get_width(),120)
        self.assertFalse(row.get_child().get_last_child().get_layout().is_ellipsized())
        self.assertGreater(ordinary.get_width(),row.get_width())
        row.emit('clicked');self.assertEqual(calls,[1]);window.close()
if __name__=='__main__':unittest.main()
