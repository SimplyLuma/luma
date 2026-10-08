# SPDX-License-Identifier: Apache-2.0
"""Controlled account-removal races against the real native mailbox owner.

OAuth/keyring/network are explicit seams; SQLite and owner lifecycle are real.
No credentials, public accounts, or remote mail servers are used.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from charlie_luma.host_mail_owner import MailOwner
from charlie_luma.accounts import account_id_for_address
from charlie_luma.account_lifecycle import stripe
from charlie_luma.model import Account, ServerConfig


class Secrets:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()

    def lookup(self, account, kind):
        with self.lock:
            return self.values.get((account, kind))

    def store(self, account, kind, value):
        with self.lock:
            self.values[account, kind] = value

    def clear(self, account, kind):
        with self.lock:
            return self.values.pop((account, kind), None) is not None


class ControlledOAuth:
    def __init__(self, *, block_refresh=False, block_avatar=False):
        self.block_refresh = block_refresh
        self.block_avatar = block_avatar
        self.started = threading.Event()
        self.release = threading.Event()
        self.refreshes = 0

    def _hold(self):
        self.started.set()
        if not self.release.wait(3):
            raise TimeoutError('Controlled OAuth seam was not released')

    def access_token(self, saved, force_refresh=False):
        self.refreshes += 1
        if self.block_refresh:
            self._hold()
        return 'synthetic-access-token', 'synthetic-refreshed-token'

    def profile_picture_for_access_token(self, token):
        if self.block_avatar:
            self._hold()
        return 'https://example.invalid/owned-avatar.png'


class ControlledSecrets(Secrets):
    """Pause after an external credential write, not before its side effect."""
    def __init__(self, values, operation):
        super().__init__()
        self.values.update(values)
        self.operation = operation
        self.started = threading.Event()
        self.release = threading.Event()

    def _hold(self, operation):
        if operation == self.operation and not self.started.is_set():
            self.started.set()
            if not self.release.wait(3):
                raise TimeoutError('Controlled credential commit was not released')

    def store(self, account, kind, value):
        super().store(account, kind, value)
        self._hold('store')

    def clear(self, account, kind):
        result = super().clear(account, kind)
        self._hold('clear')
        return result


class RefreshingTransport:
    def __init__(self, owner):
        self.owner = owner

    def fetch_mail(self, account, config, cancel):
        self.owner._token(account, force_refresh=True)
        return ()


class MailOwnerRaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.secrets = Secrets()
        with patch('charlie_luma.host_mail_owner.default_store_path',
                   return_value=Path(self.temporary.name) / 'mail.db'), \
             patch('charlie_luma.host_mail_owner.SecretStore', return_value=self.secrets):
            self.owner = MailOwner()
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.owner.close)
        self.addCleanup(self.pool.shutdown, wait=True)
        self.account = Account('owned-race-account', 'Owned fixture',
                               'owned@example.invalid', 'microsoft')
        self.config = ServerConfig('imap.example.invalid', smtp_host='smtp.example.invalid')
        self.owner.store.upsert_account(self.account)
        self.owner.store.upsert_server_config(self.account.id, self.config)
        self.secrets.store(self.account.id, 'oauth-token', 'synthetic-old-token')
        self.live = threading.Event()

    def remove(self, identifier=None):
        return self.owner.perform('RemoveAccount',
                                  {'account_id': identifier or self.account.id}, self.live)

    def run_removal_interleaving(self, operation, oauth):
        self.microsoft_patch = patch('charlie_luma.host_mail_owner.MicrosoftOAuth.from_token_json', return_value=oauth)
        self.microsoft_patch.start(); self.addCleanup(self.microsoft_patch.stop)
        running = self.pool.submit(self.owner.perform, operation,
                                   {'account_id': self.account.id}, self.live)
        removal_started = threading.Event()
        removal_done = threading.Event()

        def removing():
            removal_started.set()
            try:
                return self.remove()
            finally:
                removal_done.set()

        try:
            self.assertTrue(oauth.started.wait(2), 'Actual operation reached blocked OAuth seam')
            removal = self.pool.submit(removing)
            self.assertTrue(removal_started.wait(2), 'Concurrent removal was scheduled')
            self.assertFalse(removal_done.wait(.2),
                             'Removal must wait for the same-account in-flight operation')
        finally:
            oauth.release.set()
        self.assertEqual(running.result(2), {})
        self.assertEqual(removal.result(2), {})
        self.assertEqual(self.owner.store.accounts(), ())
        self.assertIsNone(self.owner.store.server_config(self.account.id))
        self.assertEqual(self.secrets.values, {}, 'Neither password nor refreshed token may survive removal')

    def test_refresh_finishes_before_removal_clears_new_token(self):
        self.owner.transport = RefreshingTransport(self.owner)
        self.run_removal_interleaving('Sync', ControlledOAuth(block_refresh=True))


    def test_distinct_account_stripe_does_not_wait_for_blocked_refresh(self):
        # Production deliberately bounds locks with stripes. Select two actual
        # distinct stripes so randomized Python hashes cannot make this flaky.
        identifier = next('other-owned-account-' + str(index) for index in range(256)
                          if stripe('other-owned-account-' + str(index)) != stripe(self.account.id))
        other = Account(identifier, 'Other fixture',
                        'other@example.invalid', 'custom')
        self.owner.store.upsert_account(other)
        self.owner.store.upsert_server_config(other.id, self.config)
        self.secrets.store(other.id, 'password', 'synthetic-other-password')
        oauth = ControlledOAuth(block_refresh=True)
        self.microsoft_patch = patch('charlie_luma.host_mail_owner.MicrosoftOAuth.from_token_json', return_value=oauth)
        self.microsoft_patch.start(); self.addCleanup(self.microsoft_patch.stop)
        self.owner.transport = RefreshingTransport(self.owner)
        running = self.pool.submit(self.owner.perform, 'Sync',
                                   {'account_id': self.account.id}, self.live)
        try:
            self.assertTrue(oauth.started.wait(2))
            self.assertEqual(self.pool.submit(self.remove, other.id).result(1), {})
            self.assertEqual(tuple(a.id for a in self.owner.store.accounts()), (self.account.id,))
            self.assertIsNone(self.secrets.lookup(other.id, 'password'))
        finally:
            oauth.release.set()
        self.assertEqual(running.result(2), {})

    def test_cancelled_queued_remove_preserves_account_and_refreshed_token(self):
        oauth = ControlledOAuth(block_refresh=True)
        self.microsoft_patch = patch('charlie_luma.host_mail_owner.MicrosoftOAuth.from_token_json', return_value=oauth)
        self.microsoft_patch.start(); self.addCleanup(self.microsoft_patch.stop)
        self.owner.transport = RefreshingTransport(self.owner)
        running = self.pool.submit(self.owner.perform, 'Sync',
                                   {'account_id': self.account.id}, self.live)
        queued_cancel = threading.Event()
        removal_started = threading.Event()

        def removing():
            removal_started.set()
            return self.owner.perform('RemoveAccount',
                                      {'account_id': self.account.id}, queued_cancel)

        try:
            self.assertTrue(oauth.started.wait(2))
            removal = self.pool.submit(removing)
            self.assertTrue(removal_started.wait(2))
            self.assertFalse(removal.done(), 'Removal queued behind the live account operation')
            queued_cancel.set()
        finally:
            oauth.release.set()
        self.assertEqual(running.result(2), {})
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            removal.result(2)
        self.assertEqual(tuple(a.id for a in self.owner.store.accounts()), (self.account.id,))
        self.assertEqual(self.secrets.lookup(self.account.id, 'oauth-token'),
                         'synthetic-refreshed-token')

    def test_removed_account_token_lookup_cannot_refresh_retained_account_object(self):
        oauth = ControlledOAuth()
        self.microsoft_patch = patch('charlie_luma.host_mail_owner.MicrosoftOAuth.from_token_json', return_value=oauth)
        self.microsoft_patch.start(); self.addCleanup(self.microsoft_patch.stop)
        self.remove()
        with self.assertRaises(ValueError):
            self.owner._token(self.account, force_refresh=True)
        self.assertEqual(oauth.refreshes, 0)
        self.assertEqual(self.secrets.values, {})

    def complete_after_credential_commit(self, operation, payload, phase):
        self.secrets = ControlledSecrets(self.secrets.values, phase)
        self.owner.secrets = self.secrets
        cancelled = threading.Event()
        running = self.pool.submit(self.owner.perform, operation, payload, cancelled)
        try:
            self.assertTrue(self.secrets.started.wait(2),
                            'Real owner reached the externally committed credential seam')
            cancelled.set()
        finally:
            self.secrets.release.set()
        # Cancellation after the credential commit must not strand different
        # database metadata. The client suppresses late UI completion separately.
        return running.result(2)

    def test_cancel_during_password_commit_completes_matching_account_metadata(self):
        updated = replace(self.account, display_name='Updated owned fixture',
                          provider='custom')
        response = self.complete_after_credential_commit(
            'SaveAccount', {'account': updated, 'config': self.config,
                            'password': 'synthetic-updated-password'}, 'store')
        self.assertEqual(self.owner.store.accounts(), (updated,))
        self.assertEqual(self.owner.store.server_config(updated.id), self.config)
        self.assertEqual(self.secrets.lookup(updated.id, 'password'),
                         'synthetic-updated-password')
        self.assertIsNone(self.secrets.lookup(updated.id, 'oauth-token'))
        self.assertEqual(response['account']['display_name'], updated.display_name)

    def test_cancel_during_remove_credential_clear_finishes_database_removal(self):
        self.secrets.store(self.account.id, 'password', 'synthetic-old-password')
        self.assertEqual(self.complete_after_credential_commit(
            'RemoveAccount', {'account_id': self.account.id}, 'clear'), {})
        self.assertEqual(self.owner.store.accounts(), ())
        self.assertIsNone(self.owner.store.server_config(self.account.id))
        self.assertEqual(self.secrets.values, {})

    def test_cancel_during_oauth_commit_finishes_matching_account_metadata(self):
        identity = SimpleNamespace(email='new-owned@example.invalid',
                                   name='New owned fixture', picture='',
                                   token_json='synthetic-new-oauth-token')

        class SignedInOAuth:
            def sign_in(self, open_uri, *, cancel=None):
                if cancel.is_set():
                    raise AssertionError('Fixture starts before cancellation')
                return identity

        with patch('charlie_luma.host_mail_owner.MicrosoftOAuth', return_value=SignedInOAuth()):
            response = self.complete_after_credential_commit('MicrosoftSignIn', {'client_id':'public-fixture'}, 'store')
        identifier = account_id_for_address(identity.email)
        account = next(a for a in self.owner.store.accounts() if a.id == identifier)
        self.assertEqual((account.address, account.provider), (identity.email, 'microsoft'))
        self.assertIsNotNone(self.owner.store.server_config(identifier))
        self.assertEqual(self.secrets.lookup(identifier, 'oauth-token'), identity.token_json)
        self.assertIsNone(self.secrets.lookup(identifier, 'password'))
        self.assertEqual(response['account']['id'], identifier)
        self.assertNotIn(identity.token_json, str(response))



if __name__ == '__main__':
    unittest.main()
