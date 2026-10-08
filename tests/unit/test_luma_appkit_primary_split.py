"""A primary split action keeps both its main and alternative actions reachable."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import ActionCenter,BarAction,SplitAction,ToastHost,install_appkit,install_lumaui

class PrimarySplit(unittest.TestCase):
    def test_save_stays_visible_when_secondary_actions_overflow(self):
        Gtk.init();install_appkit();install_lumaui()
        for width in (360,402,500):
            events=[]
            host=ToastHost(Gtk.Box());win=Gtk.Window(child=host,default_width=width,default_height=700)
            save=SplitAction('Save',lambda:events.append('save'),[],primary=True)
            bar=ActionCenter().attach(host)
            bar.show_bar([BarAction('x',tooltip='Close')]+[BarAction('crop',tooltip=f'Tool {n}') for n in range(5)]+[save],fill=True)
            win.present();end=time.monotonic()+.4
            while time.monotonic()<end:
                while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            for button in (save.main,save.more):
                self.assertTrue(button.get_mapped())
                ok,b=button.compute_bounds(bar.bar)
                self.assertTrue(ok)
                self.assertGreaterEqual(b.get_x(),0)
                self.assertLessEqual(b.get_x()+b.get_width(),bar.bar.get_width())
            save.main.emit('clicked');self.assertEqual(events,['save'])
            win.close()

if __name__=='__main__':unittest.main()
