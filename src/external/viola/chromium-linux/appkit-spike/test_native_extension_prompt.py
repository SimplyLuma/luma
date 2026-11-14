# SPDX-License-Identifier: GPL-3.0-only
import unittest
from types import SimpleNamespace
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk
from native_extension_prompt import ExtensionPromptPanel, is_extension_prompt
from native_footer import BAR_PROMPTS
from responsive_chrome import ResponsiveChrome

INSTALL = {'id': 'e1', 'type': 'extension', 'title': 'Add "uBlock Origin Lite"?',
           'body': 'It can:', 'details': ['Read and change all your data on all websites'],
           'accept': 'Add extension', 'cancel': 'Cancel', 'name': 'uBlock Origin Lite'}
INSTALLED = {'id': 'e2', 'type': 'extension-installed', 'title': 'uBlock Origin Lite was added to Viola',
             'body': '', 'accept': '', 'cancel': 'Done'}


class ExtensionPromptPanelTest(unittest.TestCase):
    def test_accept_answers_once(self):
        answers = []
        panel = ExtensionPromptPanel(INSTALL, lambda *answer: answers.append(answer))
        self.assertEqual(panel.accept_button.get_label(), 'Add extension')
        self.assertEqual(panel.cancel_button.get_label(), 'Cancel')
        self.assertEqual(panel.details, ['Read and change all your data on all websites'])
        panel.accept()
        panel.accept()
        panel.cancel()
        self.assertEqual(answers, [('e1', True)])

    def test_cancel_and_escape_decline(self):
        answers = []
        panel = ExtensionPromptPanel(INSTALL, lambda *answer: answers.append(answer))
        self.assertTrue(panel._key(None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(answers, [('e1', False)])

    def test_added_notice_has_one_button_that_declines(self):
        answers = []
        panel = ExtensionPromptPanel(INSTALLED, lambda *answer: answers.append(answer))
        self.assertIsNone(panel.accept_button)
        self.assertEqual(panel.cancel_button.get_label(), 'Done')
        panel.accept()
        panel.cancel()
        self.assertEqual(answers, [('e2', False)])

    def test_details_are_bounded(self):
        panel = ExtensionPromptPanel(dict(INSTALL, details=['line'] * 40), lambda *_: None)
        self.assertEqual(len(panel.details), 24)

    def test_only_extension_notices_are_extension_prompts(self):
        self.assertTrue(is_extension_prompt(INSTALL))
        self.assertTrue(is_extension_prompt(INSTALLED))
        self.assertFalse(is_extension_prompt({'id': 'b', 'type': 'permission'}))
        self.assertFalse(is_extension_prompt({'id': 'b', 'type': 'login'}))
        self.assertFalse(is_extension_prompt({'type': 'extension'}))
        self.assertFalse(is_extension_prompt(None))

    def test_the_bar_grows_them_instead_of_a_card(self):
        self.assertIn('extension', BAR_PROMPTS)
        self.assertIn('extension-installed', BAR_PROMPTS)


class ExtensionAnswerTest(unittest.TestCase):
    def chrome(self):
        sent = []
        center = SimpleNamespace(grown='signin', fold=lambda: setattr(center, 'grown', None))
        chrome = SimpleNamespace(host=SimpleNamespace(send=lambda channel, payload: sent.append((channel, payload))),
                                 center=center, signin=object(), signin_answered=None)
        return chrome, sent, center

    def test_accept_allows_and_folds(self):
        chrome, sent, center = self.chrome()
        ResponsiveChrome.answer_extension(chrome, 'e1', True)
        self.assertEqual(sent, [('permission:respond', {'id': 'e1', 'decision': 'allow'})])
        self.assertEqual(chrome.signin_answered, 'e1')
        self.assertIsNone(chrome.signin)
        self.assertIsNone(center.grown)

    def test_cancel_dismisses(self):
        chrome, sent, _center = self.chrome()
        ResponsiveChrome.answer_extension(chrome, 'e1', False)
        self.assertEqual(sent, [('permission:respond', {'id': 'e1', 'decision': 'dismiss'})])

    def test_escape_on_the_window_cancels_an_open_prompt(self):
        answers = []
        panel = ExtensionPromptPanel(INSTALL, lambda *answer: answers.append(answer))
        chrome = SimpleNamespace(signin=panel, center=SimpleNamespace(grown='signin'))
        self.assertTrue(ResponsiveChrome.signin_key(chrome, None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(answers, [('e1', False)])


if __name__ == '__main__':
    unittest.main()
