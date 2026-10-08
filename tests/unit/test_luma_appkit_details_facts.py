"""Standalone facts retain selectable wrapping values when reused in narrow panels."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import DetailsFacts,install_appkit,install_lumaui

class DetailsFactsTest(unittest.TestCase):
    def test_narrow_values_wrap_and_remain_selectable(self):
        Gtk.init();install_appkit();install_lumaui()
        value='Pictures / A long folder name / A long photograph name.jpg'
        facts=DetailsFacts([('Where',value),('Size','1.4 MB')])
        win=Gtk.Window(child=facts,default_width=288,default_height=200)
        win.present()
        end=time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        row=facts.get_first_child();key=row.get_first_child();shown=key.get_next_sibling()
        self.assertTrue(shown.get_selectable())
        self.assertEqual(shown.get_text(),value)
        self.assertGreater(shown.get_layout().get_line_count(),1)
        ok,b=shown.compute_bounds(facts)
        self.assertTrue(ok)
        self.assertGreaterEqual(b.get_x(),0)
        self.assertLessEqual(b.get_x()+b.get_width(),facts.get_width())
        win.close()

if __name__=='__main__':unittest.main()
