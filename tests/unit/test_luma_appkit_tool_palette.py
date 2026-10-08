"""Palette tools remain reachable independently of the action bar."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import ToolPalette,BarAction,install_appkit,install_lumaui


def settle():
    until=time.monotonic()+.25
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class Palette(unittest.TestCase):
    def test_responsive_scroll_and_selection(self):
        Gtk.init();install_appkit();install_lumaui()
        events=[]
        overlay=Gtk.Overlay(child=Gtk.Box())
        palette=ToolPalette([BarAction('pencil',tooltip=str(i),on_activate=lambda i=i:events.append(i)) for i in range(12)]).attach(overlay)
        palette.set_shown(True)
        window=Gtk.Window(child=overlay);window.set_default_size(720,500);window.present();settle()
        for width in (720,402,360):
            window.set_default_size(width,500);settle()
            ok,b=palette.compute_bounds(overlay)
            self.assertTrue(ok)
            self.assertGreaterEqual(b.get_x(),12)
            self.assertLessEqual(b.get_x()+b.get_width(),width-12)
            self.assertAlmostEqual(overlay.get_height()-b.get_y()-b.get_height(),104 if width<560 else 76,delta=1)
            adjustment=palette.scroller.get_hadjustment()
            if width<560:self.assertGreater(adjustment.get_upper(),adjustment.get_page_size())
            palette.set_current(palette.buttons[-1]);self.assertTrue(palette.buttons[-1].has_css_class('primary'))
            palette.buttons[-1].grab_focus();settle()
            ok,last=palette.buttons[-1].compute_bounds(palette.scroller)
            self.assertTrue(ok)
            self.assertGreaterEqual(last.get_x(),-1)
            self.assertLessEqual(last.get_x()+last.get_width(),palette.scroller.get_width()+1)
            palette.buttons[-1].emit('clicked')
        self.assertEqual(events,[11,11,11])
        window.close()


if __name__=='__main__':unittest.main()
