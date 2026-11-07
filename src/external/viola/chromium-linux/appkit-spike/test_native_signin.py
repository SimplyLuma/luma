# SPDX-License-Identifier: GPL-3.0-only
import unittest
from types import SimpleNamespace
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk
from native_signin import SignInPanel, is_signin
from responsive_chrome import ResponsiveChrome

PROMPT = {'id': 'a1', 'type': 'login', 'title': 'Sign in to example.test',
          'body': 'Your connection to this site is not private', 'host': 'example.test', 'proxy': False}


class SignInPanelTest(unittest.TestCase):
    def test_sign_in_sends_both_fields_once_and_forgets_the_password(self):
        answers = []
        panel = SignInPanel(PROMPT, lambda *answer: answers.append(answer))
        panel.username.set_text('luma')
        panel.password.set_text('test')
        self.assertFalse(panel.password.entry.get_visibility())
        panel.submit()
        panel.submit()
        panel.cancel()
        self.assertEqual(answers, [('a1', True, 'luma', 'test')])
        self.assertEqual(panel.password.text, '')

    def test_cancel_and_escape_answer_not_supplied(self):
        answers = []
        panel = SignInPanel(PROMPT, lambda *answer: answers.append(answer))
        panel.password.set_text('secret')
        self.assertTrue(panel._key(None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(answers, [('a1', False, '', '')])
        self.assertEqual(panel.password.text, '')

    def test_only_login_notices_are_sign_ins(self):
        self.assertTrue(is_signin(PROMPT))
        self.assertFalse(is_signin({'id': 'b', 'type': 'permission'}))
        self.assertFalse(is_signin({'type': 'login'}))
        self.assertFalse(is_signin(None))


class SignInAnswerTest(unittest.TestCase):
    def chrome(self):
        sent = []
        center = SimpleNamespace(grown='signin', fold=lambda: setattr(center, 'grown', None))
        chrome = SimpleNamespace(host=SimpleNamespace(send=lambda channel, payload: sent.append((channel, payload))),
                                 center=center, signin=object(), signin_answered=None)
        return chrome, sent, center

    def test_sign_in_answers_the_engine_and_folds(self):
        chrome, sent, center = self.chrome()
        ResponsiveChrome.answer_signin(chrome, 'a1', True, 'luma', 'test')
        self.assertEqual(sent, [('permission:respond', {'id': 'a1', 'decision': 'signin',
                                                        'username': 'luma', 'password': 'test'})])
        self.assertEqual(chrome.signin_answered, 'a1')
        self.assertIsNone(center.grown)

    def test_escape_on_the_window_cancels_an_open_sign_in(self):
        answers = []
        panel = SignInPanel(PROMPT, lambda *answer: answers.append(answer))
        chrome = SimpleNamespace(signin=panel, center=SimpleNamespace(grown='signin'))
        self.assertTrue(ResponsiveChrome.signin_key(chrome, None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(answers, [('a1', False, '', '')])
        chrome.center.grown = None
        self.assertFalse(ResponsiveChrome.signin_key(chrome, None, Gdk.KEY_Escape, 0, 0))

    def test_cancel_dismisses(self):
        chrome, sent, _center = self.chrome()
        ResponsiveChrome.answer_signin(chrome, 'a1', False, '', '')
        self.assertEqual(sent, [('permission:respond', {'id': 'a1', 'decision': 'dismiss'})])


if __name__ == '__main__':
    unittest.main()
