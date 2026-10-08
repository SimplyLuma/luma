# SPDX-License-Identifier: GPL-3.0-only
from types import SimpleNamespace
import unittest
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk
from native_dialogs import NativeDialogs


class NativeDialogTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.window = Gtk.Window()
        engine = SimpleNamespace(request=lambda *args, **kwargs: self.calls.append((args,kwargs)))
        self.window.page_input = SimpleNamespace(session='active',engine=engine)
        self.dialogs = NativeDialogs(self.window)

    def tearDown(self):
        self.dialogs.dismiss(answer=False)
        self.window.destroy()

    def event(self, session='active', kind='confirm'):
        return {'sessionId':session,'method':'Page.javascriptDialogOpening',
                'params':{'type':kind,'url':'chrome://viola/settings.html','message':'Clear history?','defaultPrompt':'Example'}}

    def test_confirmation_is_native_and_answers_the_original_session_once(self):
        self.dialogs.event(self.event())
        dialog = self.dialogs.dialog
        self.assertEqual(dialog.get_heading(),'Viola Settings')
        self.assertEqual(dialog.get_default_response(),'cancel')
        dialog.emit('response','accept')
        dialog.emit('response','accept')
        self.assertEqual(self.calls,[(('Page.handleJavaScriptDialog',{'accept':True}),{'session':'active'})])

    def test_other_windows_events_are_ignored_and_switching_cancels_old_dialog(self):
        self.dialogs.event(self.event('other'))
        self.assertIsNone(self.dialogs.dialog)
        self.dialogs.event(self.event())
        self.window.page_input.session = 'new'
        self.dialogs.sync()
        self.assertIsNone(self.dialogs.dialog)
        self.assertEqual(self.calls[-1],(('Page.handleJavaScriptDialog',{'accept':False}),{'session':'active'}))

    def test_prompt_uses_entry_value(self):
        self.dialogs.event(self.event(kind='prompt'))
        dialog = self.dialogs.dialog
        dialog.get_extra_child().set_text('Answer')
        dialog.emit('response','accept')
        self.assertEqual(self.calls[-1][0][1],{'accept':True,'promptText':'Answer'})


if __name__ == '__main__':
    unittest.main()
