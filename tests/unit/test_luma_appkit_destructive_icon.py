"""A running Clock key retains its phone target and white ink."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import ActionCenter,BarAction,TabBar,ToastHost,install_appkit,install_lumaui
class DestructiveIcon(unittest.TestCase):
    def test_red_key_beside_tabs_stays_56_wide(self):
        Gtk.init();install_appkit();install_lumaui()
        host=ToastHost(Gtk.Box());window=Gtk.Window(child=host,default_width=402,default_height=874)
        window.add_css_class('luma-app-window')
        bar=ActionCenter().attach(host)
        tabs=TabBar([('world','World','globe'),('alarm','Alarms','alarm-clock'),('sw','Stopwatch','timer'),('tm','Timer','hourglass')],current='sw',compact=True)
        events=[]
        bar.show_bar([tabs,BarAction('flag',tooltip='Lap'),BarAction('pause',tooltip='Stop',primary=True,danger=True,on_activate=lambda:events.append('stop'))])
        window.present();end=time.monotonic()+.4
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        button=bar.bar_row.get_last_child()
        self.assertEqual(button.get_width(),56)
        self.assertEqual(button.get_height(),48)
        color=button.get_style_context().get_color()
        self.assertGreater(color.red,.95);self.assertGreater(color.green,.95);self.assertGreater(color.blue,.95)
        button.emit('clicked');self.assertEqual(events,['stop']);window.close()
if __name__=='__main__':unittest.main()
