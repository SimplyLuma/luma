"""A live readout keeps value/caption bounds and callbacks across updates."""
import unittest
from gi.repository import Gtk
from luma_appkit import MediaReadout, MediaTransport, install_appkit, install_lumaui
from test_luma_appkit_island_resize import settle
class ReadoutTest(unittest.TestCase):
    def test_live_value_and_transport(self):
        Gtk.init();install_appkit();install_lumaui()
        for width in (360,402,720,1180):
            calls=[]
            readout=MediaReadout('95','BPM',chip=True,on_activate=lambda:calls.append(True))
            row=Gtk.Box();row.append(readout)
            readout.set_valign(Gtk.Align.CENTER)
            window=Gtk.Window(child=row,default_width=width,default_height=180)
            window.present();settle()
            self.assertEqual(readout.get_height(),48)
            for value in ('120','400','20'):
                readout.set_value(value);settle()
                box=readout.get_child();label=box.get_first_child()
                self.assertEqual(label.get_text(),value)
                self.assertEqual(box.get_last_child().get_text(),'BPM')
                self.assertLessEqual(label.get_width(),readout.get_width())
                readout.emit('clicked')
            self.assertEqual(len(calls),3)
            window.close()
        transport=MediaTransport('lcd',readouts=[('95','BPM',lambda:calls.append(True))])
        child=transport.get_first_child()
        while child and not isinstance(child,MediaReadout):child=child.get_next_sibling()
        self.assertIsInstance(child,MediaReadout)
        self.assertFalse(child.has_css_class('chip'))
if __name__=='__main__':unittest.main()
