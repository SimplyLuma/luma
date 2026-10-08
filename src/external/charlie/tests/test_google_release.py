# SPDX-License-Identifier: Apache-2.0
"""First-release policy at real native mailbox/transport boundaries."""
from dataclasses import replace
import threading
from unittest.mock import patch
from test_host_mail_owner import MailOwnerTests
from charlie_luma.engine import ImapSmtpTransport
from charlie_luma.oauth import GoogleOAuthUnavailable, GOOGLE_UNAVAILABLE


import unittest


class GoogleReleaseTests(unittest.TestCase):
    setUpBase = MailOwnerTests.setUp
    def setUp(self):
        self.setUpBase()
        self.account = replace(self.account, provider='gmail')
        self.owner.store.upsert_account(self.account)
        self.owner.store.upsert_server_config(self.account.id, self.config)
        self.saved = '{"access_token":"retained-access","refresh_token":"retained-refresh","expires_at":9999999999}'
        self.owner.secrets.store(self.account.id, 'oauth-token', self.saved)

    def test_host_google_enrollment_cannot_touch_retained_profile(self):
        before = (self.owner.store.accounts(), self.owner.store.server_config(self.account.id), dict(self.owner.secrets.values))
        with patch('charlie_luma.oauth.HTTPServer') as socket, patch('charlie_luma.oauth._post_form') as network:
            with self.assertRaises(GoogleOAuthUnavailable):
                self.owner.perform('GoogleSignIn', {}, threading.Event())
            socket.assert_not_called(); network.assert_not_called()
        self.assertEqual((self.owner.store.accounts(), self.owner.store.server_config(self.account.id), self.owner.secrets.values), before)

    def test_native_sync_is_paused_before_imap_connection_and_keeps_token(self):
        self.owner.transport = ImapSmtpTransport(self.owner.secrets.lookup, self.owner._token)
        with patch('charlie_luma.engine.imaplib.IMAP4_SSL') as imap:
            with self.assertRaisesRegex(OSError, 'Google sign-in is unavailable'):
                self.owner.perform('Sync', {'account_id': self.account.id}, self.live)
            imap.assert_not_called()
        self.assertEqual(self.owner.secrets.lookup(self.account.id, 'oauth-token'), self.saved)
        self.assertEqual(self.owner.store.accounts(), (self.account,))
        self.assertEqual(self.owner.store.server_config(self.account.id), self.config)

    def test_forced_refresh_retains_token_and_manual_password_path(self):
        with self.assertRaisesRegex(OSError, 'Google sign-in is unavailable'):
            self.owner._token(self.account, force_refresh=True)
        self.assertEqual(self.owner.secrets.lookup(self.account.id, 'oauth-token'), self.saved)
        self.owner.secrets.clear(self.account.id, 'oauth-token')
        self.owner.secrets.store(self.account.id, 'password', 'owned-manual-password')
        self.assertIsNone(self.owner._token(self.account))
        self.assertEqual(self.owner.secrets.lookup(self.account.id, 'password'), 'owned-manual-password')
