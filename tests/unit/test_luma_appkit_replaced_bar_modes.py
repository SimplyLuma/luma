"""Rebuilding a phone bar keeps its places available before child mapping."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import ActionCenter,BarSearch,ModeSwitch,ToastHost,install_appkit,install_lumaui

def settle():
    until=time.monotonic()+.3
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)

class ReplacementPlaces(unittest.TestCase):
    def test_replacing_places_does_not_trap_them_in_overflow(self):
        Gtk.init();install_appkit();install_lumaui()
        host=ToastHost(Gtk.Box());center=ActionCenter().attach(host)
        window=Gtk.Window(child=host,default_width=402,default_height=874)
        window.add_css_class('luma-app-window');window.present();settle()
        search=BarSearch('Search',keep=True)
        places=[('pad','Keypad','grid-3x3'),('recents','Recents','history'),('contacts','Contacts','users'),('vm','Voicemail','voicemail',2)]
        changes=[]
        for width in (402,360,500,720,402):
            window.set_default_size(width,874);settle()
            for current in ('pad','recents','contacts','vm'):
                center.set_visible(False)
                modes=ModeSwitch(places,current=current,on_change=changes.append)
                center.show_bar([search,modes],fill=True)
                # Layout may request fitting before the new child maps.
                self.assertEqual(center._phone, width < 560, (width, center._host_size()))
                center._fit()
                center.set_visible(True)
                settle()
                self.assertTrue(modes.get_visible(),(width,current))
                self.assertFalse(center._overflow,(width,current))
                for button in modes.buttons.values():self.assertTrue(button.get_mapped())
                modes.buttons['pad'].set_active(True)
        self.assertTrue(changes)
        window.close()

if __name__=='__main__':unittest.main()
