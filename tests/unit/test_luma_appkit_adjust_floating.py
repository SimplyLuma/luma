"""Floating phone adjustments stay inside gutters and clear the action bar."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import AdjustmentPanel,ValueSlider,install_appkit,install_lumaui

class FloatingAdjustment(unittest.TestCase):
    def test_floating_and_default_sheet_geometry(self):
        Gtk.init();install_appkit();install_lumaui()
        for width in (360,402,500):
            for layout in ('floating','sheet'):
                overlay=Gtk.Overlay(child=Gtk.Box());win=Gtk.Window(child=overlay,default_width=width,default_height=874)
                panel=AdjustmentPanel([('Light',[ValueSlider(str(n)) for n in range(9)])],phone_layout=layout)
                panel.attach(overlay);panel.set_shown(True);win.present()
                end=time.monotonic()+.4
                while time.monotonic()<end:
                    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                    time.sleep(.005)
                ok,b=panel.compute_bounds(overlay);self.assertTrue(ok)
                inset=16 if layout=='floating' else 0;bottom=104 if layout=='floating' else 0
                self.assertEqual(b.get_x(),inset)
                self.assertEqual(b.get_width(),width-2*inset)
                self.assertAlmostEqual(b.get_y()+b.get_height(),overlay.get_height()-bottom,delta=1)
                self.assertLessEqual(b.get_height(),int(overlay.get_height()*(.44 if layout=='floating' else .5)))
                win.close()

if __name__=='__main__':unittest.main()
