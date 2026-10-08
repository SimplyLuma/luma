"""Paragraph-style choices retain radio activation and mapped preview geometry."""
import unittest
from test_luma_appkit_list_status import settle
from gi.repository import Gtk
from luma_appkit import PanelChoices,install_appkit,install_lumaui
class DocumentChoices(unittest.TestCase):
    def test_previews_and_activation(self):
        Gtk.init();install_appkit();install_lumaui()
        calls=[]
        choices=PanelChoices([('heading','Heading'),('text','Text'),('quote','Quote')],selected='text',document_style=True,on_choose=calls.append)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);box.append(choices)
        window=Gtk.Window(child=box,default_width=360,default_height=200)
        window.add_css_class('luma-app-window');window.present();settle()
        widths=[]
        for button in choices.buttons.values():
            self.assertEqual(button.get_allocated_height(),44)
            widths.append(button.get_allocated_width())
        self.assertLessEqual(max(widths)-min(widths),1)
        heading=choices.buttons['heading'].get_child().get_last_child()
        self.assertEqual(int(heading.get_pango_context().get_font_description().get_weight()),750)
        choices.buttons['quote'].emit('clicked')
        self.assertEqual(calls,['quote']);self.assertEqual(choices.selected,'quote')
        self.assertTrue(choices.buttons['quote'].has_css_class('on'))
        window.close()
if __name__=='__main__':unittest.main()
