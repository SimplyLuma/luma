"""Grown islands retain phone gutters when resizing inside the phone tier."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import TitleIsland,LayerHost,install_appkit,install_lumaui

def settle():
    until=time.monotonic()+.35
    while time.monotonic()<until:
        while time.monotonic()<until and GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)
class IslandResize(unittest.TestCase):
    def test_grown_bounds_follow_each_phone_width(self):
        Gtk.init();install_appkit();install_lumaui()
        host=LayerHost(Gtk.Box(),name='window')
        window=Gtk.Window(child=host,default_width=480,default_height=874)
        window.add_css_class('luma-app-window')
        island=TitleIsland(title='Launch',grows='details',grow=lambda:Gtk.Label(label='Details'))
        island.float_over(host);window.present();settle();island.grow_into();settle()
        for width in (480,402,360,500,402):
            window.set_default_size(width,874);settle()
            ok,rect=island.compute_bounds(host)
            self.assertTrue(ok)
            self.assertFalse(island.has_css_class('flat'))
            self.assertEqual(island.lead_button.get_width(),48)
            self.assertEqual(round(rect.get_x()),12)
            self.assertEqual(round(rect.get_width()),width-24)
            self.assertEqual(round(rect.get_x()+rect.get_width()),width-12)
        island.fold();window.close()
    def test_menu_and_desktop_details_have_their_rendered_caps(self):
        for width, kind, expected in ((402, 'menu', 320), (1100, 'details', 360)):
            host=LayerHost(Gtk.Box(),name='window')
            window=Gtk.Window(child=host,default_width=width,default_height=874)
            window.add_css_class('luma-app-window')
            island=TitleIsland(title='Home',grows=kind,phone_only=False,
                               grow=lambda:Gtk.Label(label='Content'))
            island.float_over(host);window.present();settle();island.grow_into();settle()
            ok,rect=island.compute_bounds(host)
            self.assertTrue(ok)
            self.assertEqual(round(rect.get_x()),12)
            self.assertEqual(round(rect.get_width()),expected)
            island.fold();window.close()
if __name__=='__main__':unittest.main()
