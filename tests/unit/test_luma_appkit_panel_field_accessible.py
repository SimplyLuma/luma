"""A panel field exposes the editable textbox, not an inaccessible Gtk.Text."""
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
from luma_appkit import PanelField

class PanelFieldAccessibility(unittest.TestCase):
    def test_textbox_edits_and_submits(self):
        Gtk.init()
        changed=[];submitted=[]
        field=PanelField('tag','Label',text='Alarm',on_change=changed.append,on_submit=submitted.append)
        self.assertEqual(field.entry.get_accessible_role(),Gtk.AccessibleRole.TEXT_BOX)
        field.entry.set_text('Wake up')
        field.entry.emit('activate')
        self.assertEqual(field.text,'Wake up')
        self.assertEqual(changed[-1],'Wake up')
        self.assertEqual(submitted,['Wake up'])

if __name__=='__main__':unittest.main()
