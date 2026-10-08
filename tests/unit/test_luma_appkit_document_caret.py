"""A peer label stays inside its editor without moving the insertion position."""
import time,unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import snapshot_document_caret

class CaretBounds(unittest.TestCase):
    def test_label_bounds_preserve_caret_position(self):
        Gtk.init()
        view=Gtk.TextView()
        window=Gtk.Window(child=view,default_width=360,default_height=160)
        window.present()
        end=time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        for width in (360,402,720,1180):
            right=width-32
            for name in ('Priya','A very long collaborator name that cannot fit in this text area' * 5):
                snapshot=Gtk.Snapshot()
                snapshot_document_caret(snapshot,view,right-5,40,name=name,hue=320,label_bounds=(0,right))
                node=snapshot.to_node()
                self.assertLessEqual(node.get_bounds().get_x()+node.get_bounds().get_width(),right)
                caret=node.get_child(0).get_bounds()
                self.assertAlmostEqual(caret.get_x(),right-5)
        snapshot=Gtk.Snapshot()
        snapshot_document_caret(snapshot,view,355,40,name='Priya',hue=320)
        bounds=snapshot.to_node().get_bounds()
        self.assertGreater(bounds.get_x()+bounds.get_width(),360)
        window.destroy()
if __name__=='__main__':unittest.main()
