# SPDX-License-Identifier: Apache-2.0
import threading
import time
import unittest
from charlie_luma.oauth import GmailOAuth, MicrosoftOAuth, OAuthError

class OAuthCancellationTests(unittest.TestCase):
    def test_real_microsoft_loopback_wait_is_promptly_cancelled(self):
        for oauth in (MicrosoftOAuth('public-client-id'),):
            with self.subTest(provider=type(oauth).__name__):
                cancelled = threading.Event(); opened = threading.Event(); errors = []
                def run():
                    try: oauth.sign_in(lambda _uri: opened.set() or True, cancel=cancelled)
                    except Exception as error: errors.append(error)
                worker = threading.Thread(target=run, daemon=True); worker.start()
                self.assertTrue(opened.wait(2)); started = time.monotonic(); cancelled.set()
                worker.join(2)
                self.assertFalse(worker.is_alive()); self.assertLess(time.monotonic()-started, 1)
                self.assertEqual(len(errors), 1); self.assertIsInstance(errors[0], OAuthError)
                self.assertIn('cancelled', str(errors[0]).lower())
