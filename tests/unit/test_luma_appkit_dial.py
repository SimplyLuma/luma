"""Reusable dialing keys preserve source geometry and native activation."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib,Pango
from luma_appkit import DialKey,install_appkit,install_lumaui

class DialKeyTests(unittest.TestCase):
    def test_geometry_type_and_activation(self):
        Gtk.init();install_appkit();install_lumaui()
        for phone in (False,True):
            fired=[]
            box=Gtk.Box(halign=Gtk.Align.START,valign=Gtk.Align.START)
            key=DialKey('2',legend='ABC',phone=phone,on_activate=lambda:fired.append('2'))
            tone=DialKey('2',variant='tone',phone=phone)
            call=DialKey(icon='phone',variant='call',phone=phone,label='Call')
            for widget in (key,tone,call):box.append(widget)
            window=Gtk.Window(child=box,default_width=402,default_height=150)
            window.add_css_class('luma-app-window');window.present()
            until=time.monotonic()+.3
            while time.monotonic()<until:
                while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            self.assertEqual((key.get_width(),key.get_height()),(80,80) if phone else (76,76))
            self.assertEqual((call.get_width(),call.get_height()),(76,76))
            self.assertEqual((tone.get_width(),tone.get_height()),(76,76) if phone else (56,56))
            digit=key.get_child().get_first_child();legend=digit.get_next_sibling()
            self.assertEqual(digit.get_pango_context().get_font_description().get_size()/Pango.SCALE,28)
            self.assertEqual(legend.get_pango_context().get_font_description().get_size()/Pango.SCALE,9.5)
            self.assertEqual(legend.get_pango_context().get_font_description().get_weight(),Pango.Weight.BOLD)
            found,muted=legend.get_style_context().lookup_color('luma_muted')
            self.assertTrue(found)
            self.assertEqual(legend.get_color().to_string(),muted.to_string())
            key.emit('clicked');self.assertEqual(fired,['2'])
            self.assertEqual(key.get_accessible_role(),Gtk.AccessibleRole.BUTTON)
            window.destroy()

if __name__=='__main__':unittest.main()
