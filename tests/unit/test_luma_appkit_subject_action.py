"""Long two-line subjects keep surrounding reader actions visible on phones."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import SubjectAction,ActionCenter,BarAction,ToastHost,install_appkit,install_lumaui
class SubjectTest(unittest.TestCase):
 def test_subject_and_actions_fit(self):
  Gtk.init();install_appkit();install_lumaui()
  for width in (360,402,720,1180):
   events=[];host=ToastHost(Gtk.Box());win=Gtk.Window(child=host,default_width=width,default_height=700)
   subject=SubjectAction('A very long chapter title that must ellipsize','12 min left',on_activate=lambda:events.append('open'))
   bar=ActionCenter().attach(host)
   bar.show_bar([BarAction('chevron-left',tooltip='Library'),subject,BarAction('bookmark',tooltip='Bookmark'),BarAction('type',tooltip='Type'),BarAction('headphones',tooltip='Listen')],fill=True)
   win.present();end=time.monotonic()+.4
   while time.monotonic()<end:
    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
    time.sleep(.005)
   self.assertFalse(bar._overflow)
   self.assertTrue(subject.get_mapped())
   ok,b=subject.compute_bounds(bar.bar);self.assertTrue(ok)
   self.assertGreaterEqual(b.get_width(),70)
   self.assertGreaterEqual(b.get_x(),0);self.assertLessEqual(b.get_x()+b.get_width(),bar.bar.get_width())
   subject.emit('clicked');self.assertEqual(events,['open'])
   subject.set_title('Next chapter','9 min left');self.assertEqual(subject.subtitle_label.get_text(),'9 min left')
   win.close()
if __name__=='__main__':unittest.main()
