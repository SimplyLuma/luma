"""Composite controls retain identity and callbacks through rebuilds and panels."""
import unittest
from test_luma_appkit_island_resize import settle
from gi.repository import Gtk
from luma_appkit import ActionCenter, BarWidget, BarChip, ToastHost, install_appkit, install_lumaui
class BarWidgetTest(unittest.TestCase):
    def test_hosted_live_chip_keeps_its_responsive_metadata(self):
        from luma_appkit.action_center import make_control
        Gtk.init();install_appkit();install_lumaui()
        host=ToastHost(Gtk.Box());center=ActionCenter().attach(host)
        window=Gtk.Window(child=host,default_width=1180,default_height=740)
        definition=BarChip('Recording',meta='0:00',live=True)
        chip=make_control(definition)
        item=BarWidget(chip)
        center.show_bar([item]);window.present();settle()
        self.assertIs(chip.bar_item,definition)
        for width in (360,1180):
            window.set_default_size(width,740);settle()
            chip.time_label.set_label('0:01')
            self.assertEqual(chip.time_label.get_label(),'0:01')
            self.assertEqual(chip.has_css_class('well'),width==360)
        window.close()

    def test_retained_control(self):
        Gtk.init();install_appkit();install_lumaui()
        for width in (360,402,720,1180):
            host=ToastHost(Gtk.Box());center=ActionCenter().attach(host)
            window=Gtk.Window(child=host,default_width=width,default_height=740)
            button=Gtk.Button(label='Play');calls=[]
            button.connect('clicked',lambda *_:calls.append(True));item=BarWidget(button)
            for _ in range(3):
                center.show_bar([item]);window.present();settle()
                self.assertTrue(button.get_mapped());button.emit('clicked')
                center.grow('settings',Gtk.Label(label='Settings'));settle();center.fold()
            self.assertEqual(len(calls),3)
            window.close()
        with self.assertRaises(TypeError):BarWidget('not a control')
        parent=Gtk.Box();child=Gtk.Label(label='Already owned');parent.append(child)
        with self.assertRaises(ValueError):BarWidget(child)
if __name__=='__main__':unittest.main()
