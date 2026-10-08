"""A physical phone list reserves status space exactly once."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import ListFirst, Island, install_appkit, install_lumaui

def settle():
    end=time.monotonic()+.35
    while time.monotonic()<end:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)
class ListStatus(unittest.TestCase):
    def test_outer_and_nested_list_titles_share_the_status_inset(self):
        Gtk.init();install_appkit();install_lumaui()
        for nested,bleed in ((False,False),(False,True),(True,False),(True,True)):
            page=ListFirst(Gtk.Box(),Gtk.Box(),title='Recents')
            if nested:
                body=Island();body.append(page)
            else:body=page
            window=Gtk.Window(child=body,default_width=402,default_height=874)
            for css in ('luma-app-window','lumaui-phone-device'):window.add_css_class(css)
            if bleed:window.add_css_class('lumaui-bleed')
            window.set_decorated(False);window.present();settle();page.show_list();settle()
            ok,bounds=page.title_label.compute_bounds(window)
            self.assertTrue(ok)
            self.assertEqual(round(bounds.get_y()),60,(nested,bleed))
            window.close()
if __name__=='__main__':unittest.main()
