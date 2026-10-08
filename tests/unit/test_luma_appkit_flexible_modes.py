"""Explicit ellipsizing modes shrink before reachable actions are hidden in overflow."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import ActionCenter,BarAction,ModeSwitch,ToastHost,install_appkit,install_lumaui


class FlexibleModes(unittest.TestCase):
    def test_crop_choices_and_commit_fit(self):
        Gtk.init();install_appkit();install_lumaui()
        for width in (360,402,500):
            events=[]
            host=ToastHost(Gtk.Box())
            win=Gtk.Window(child=host,default_width=width,default_height=700)
            modes=ModeSwitch([(k,k+' aspect ratio') for k in ('Free','Original','Square')],
                labels_only=True,ellipsize=True,on_change=events.append)
            center=ActionCenter().attach(host)
            center.show_bar([BarAction('x',tooltip='Close'),modes,BarAction('','Crop',primary=True)],fill=True)
            win.present()
            until=time.monotonic()+.5
            while time.monotonic()<until:
                while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            self.assertFalse(center._overflow)
            for button in modes.buttons.values():
                self.assertTrue(button.get_mapped())
                ok,b=button.compute_bounds(center.bar)
                self.assertTrue(ok)
                self.assertGreaterEqual(b.get_x(),0)
                self.assertLessEqual(b.get_x()+b.get_width(),center.bar.get_width())
            child=center.bar_row.get_first_child()
            while child:
                ok,b=child.compute_bounds(center.bar)
                self.assertTrue(ok)
                self.assertGreaterEqual(b.get_x(),0)
                self.assertLessEqual(b.get_x()+b.get_width(),center.bar.get_width())
                child=child.get_next_sibling()
            modes.buttons['Square'].emit('clicked');self.assertEqual(events,['Square'])
            win.close()


if __name__=='__main__':unittest.main()
