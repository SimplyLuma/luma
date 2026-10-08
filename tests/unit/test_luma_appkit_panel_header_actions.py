"""Native panel header actions survive header rebuilds and can be replaced."""
import unittest
import gi
gi.require_version('Gtk','4.0')
gi.require_version('LumaUI','1')
from gi.repository import Gtk
from luma_appkit import FloatingPanel
class HeaderActions(unittest.TestCase):
 def test_rebuild_replace_and_remove(self):
  Gtk.init();events=[]
  actions=Gtk.Box();button=Gtk.Button(label='Auto');actions.append(button)
  button.connect('clicked',lambda *_:events.append('auto'))
  panel=FloatingPanel('Adjust','sliders-horizontal',header_actions=actions)
  parent=actions.get_parent();self.assertIsNotNone(parent)
  panel.set_title('Crop');panel.set_icon('crop');panel.set_folded(True);panel.set_folded(False)
  self.assertIs(actions.get_parent(),parent)
  button.emit('clicked');self.assertEqual(events,['auto'])
  replacement=Gtk.Button(label='Reset');panel.set_header_actions(replacement)
  self.assertIsNone(actions.get_parent());self.assertIs(replacement.get_parent(),parent)
  panel.set_header_actions(None);self.assertIsNone(replacement.get_parent())
if __name__=='__main__':unittest.main()
