"""Exercise the real dialing buttons with an isolated fixture transport."""
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gio,GLib,Gtk
os.environ['LUMA_PHONE_FIXTURE']=str(Path(__file__).resolve().parents[3]/'tests/fixtures/phone-v71.json')
from prairie_apps.phone import PhoneApplication,PhoneWindow

def settle():
    until=time.monotonic()+.3
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def find(widget,name):
    if widget.get_name()==name:return widget
    child=widget.get_first_child()
    while child:
        result=find(child,name)
        if result:return result
        child=child.get_next_sibling()

class DialWorkflow(unittest.TestCase):
    def test_dial_and_end_across_widths(self):
        app=PhoneApplication();app.set_application_id('org.projectluma.Phone.DialTest')
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE);app.register(None)
        for width in (360,402,500,720,1024,1180):
            with patch.object(PhoneWindow,'_start_live',side_effect=AssertionError('live service requested')):
                window=PhoneWindow(app)
            window.set_default_size(width,874);window.present();settle()
            find(window,'pn-key-2').emit('clicked');settle()
            self.assertEqual(window.number,'2')
            find(window,'pn-call-voice').emit('clicked');settle()
            self.assertIsNotNone(window.call)
            self.assertEqual(window.call.kind,'voice')
            window._end_call();settle()
            self.assertIsNone(window.call)
            window.close();settle()
        app.run_dispose()

if __name__=='__main__':unittest.main()
