"""The current-file head keeps both labels and a working disclosure at narrow widths."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib,Gdk
from luma_appkit import FileSummaryRow, SubjectAction, install_appkit, install_lumaui


def settle():
    until=time.monotonic()+.25
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class FileSummary(unittest.TestCase):
    def test_subject_secondary_ink_uses_defined_bar_color(self):
        Gtk.init();install_appkit();install_lumaui()
        for cls in (FileSummaryRow, SubjectAction):
            row=cls('Chapter II', '7 min left')
            win=Gtk.Window(child=row);win.set_default_size(320,60);win.present();settle()
            for child in (row.subtitle_label,row.line.get_last_child()):
                found,expected=child.get_style_context().lookup_color('luma_bar_muted')
                self.assertTrue(found)
                self.assertEqual(child.get_color().to_string(),expected.to_string())
            win.close()

    def test_long_name_fits_and_activates(self):
        Gtk.init();install_appkit();install_lumaui()
        events=[]
        row=FileSummaryRow('A very long document name which must remain accessible.pdf', '2.4 MB',kind='document',on_open=lambda:events.append('open'))
        win=Gtk.Window(child=row);win.set_default_size(240,48);win.present();settle()
        self.assertEqual(row.get_allocated_width(),240)
        self.assertEqual(row.get_height(),48)
        self.assertEqual(row.thumbnail.get_width(),34)
        self.assertLess(row.title_label.get_width(),240)
        self.assertTrue(row.subtitle_label.get_mapped())
        self.assertIn('accessible.pdf',row.get_tooltip_text())
        row.emit('clicked');self.assertEqual(events,['open'])
        row.set_expanded(True);self.assertTrue(row.has_css_class('on'))
        row.set_title('Photo.jpg','');self.assertFalse(row.subtitle_label.get_visible())
        win.close()
        pixels=GLib.Bytes.new(bytes([255,0,0,255])*800*450)
        picture=Gdk.MemoryTexture.new(800,450,Gdk.MemoryFormat.R8G8B8A8,pixels,800*4)
        image_row=FileSummaryRow('Photo.jpg','2.1 MB',picture=picture)
        win=Gtk.Window(child=image_row);win.set_default_size(320,48);win.present();settle()
        self.assertEqual(image_row.thumbnail.get_width(),34)
        self.assertEqual(image_row.thumbnail.get_height(),34)
        win.close()


if __name__=='__main__':unittest.main()
