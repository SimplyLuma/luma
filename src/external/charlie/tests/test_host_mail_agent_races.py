# SPDX-License-Identifier: Apache-2.0
"""Independent native/background lifecycle and real process-lock regressions.

Actual MailOwner/MailAgentCore/SQLite/advisory locks are exercised. Only the
external OAuth/keyring providers are synthetic controlled seams.
"""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from charlie_luma.account_lifecycle import account_lock
from charlie_luma.host_mail_owner import MailOwner
from charlie_luma.mail_agent import MailAgentCore
from charlie_luma.model import Account, ServerConfig


class SharedSecrets:
    def __init__(self, values):
        self.values = values

    def lookup(self, account, kind):
        return self.values.get((account, kind))

    def store(self, account, kind, value):
        self.values[account, kind] = value

    def clear(self, account, kind):
        return self.values.pop((account, kind), None) is not None


class PausedOAuth:
    def __init__(self, entered, release):
        self.entered = entered
        self.release = release

    def access_token(self, saved, force_refresh=False):
        self.entered.set()
        if not self.release.wait(4):
            raise TimeoutError('Controlled background refresh was not released')
        return 'synthetic-access', 'synthetic-refreshed'


def refresh_in_process(store_path, state_path, account, values, entered, release, results):
    agent = MailAgentCore(store_path=Path(store_path), state_path=Path(state_path),
                         secrets=SharedSecrets(values), notifier=SimpleNamespace(),
                         publish=lambda *_: None, dispatch=lambda callback: callback(),
                         oauth=PausedOAuth(entered, release))
    try:
        results.put(('PASS', agent.credentials(account, True)))
    except Exception as error:
        results.put(('FAIL', type(error).__name__))
    finally:
        agent.stop()


def acquire_in_process(path, identifier, requested, acquired):
    requested.set()
    with account_lock(Path(path), identifier):
        acquired.set()


class MailAgentRaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.context = multiprocessing.get_context('fork')
        self.manager = self.context.Manager()
        self.addCleanup(self.manager.shutdown)
        self.values = self.manager.dict()
        self.secrets = SharedSecrets(self.values)
        self.store_path = Path(self.temporary.name) / 'mail.db'
        with patch('charlie_luma.host_mail_owner.default_store_path', return_value=self.store_path), \
             patch('charlie_luma.host_mail_owner.SecretStore', return_value=self.secrets):
            self.owner = MailOwner()
        self.addCleanup(self.owner.close)
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown, wait=True)
        self.account = Account('owned-background-account', 'Owned fixture',
                               'owned@example.invalid', 'microsoft')
        self.config = ServerConfig('imap.example.invalid', smtp_host='smtp.example.invalid')
        self.owner.store.upsert_account(self.account)
        self.owner.store.upsert_server_config(self.account.id, self.config)
        self.secrets.store(self.account.id, 'oauth-token', 'synthetic-old')

    def remove_in_thread(self, requested, done):
        requested.set()
        try:
            return self.owner.perform('RemoveAccount', {'account_id': self.account.id}, threading.Event())
        finally:
            done.set()

    def assert_removed(self):
        self.assertEqual(self.owner.store.accounts(), ())
        self.assertIsNone(self.owner.store.server_config(self.account.id))
        self.assertEqual(dict(self.values), {}, 'Background token must not survive account removal')

    def test_background_agent_and_owner_share_lifecycle_guard(self):
        entered, release = threading.Event(), threading.Event()
        agent = MailAgentCore(store_path=self.store_path,
                             state_path=Path(self.temporary.name) / 'agent.json',
                             secrets=self.secrets, notifier=SimpleNamespace(),
                             publish=lambda *_: None, dispatch=lambda callback: callback(),
                             oauth=PausedOAuth(entered, release))
        self.addCleanup(agent.stop)
        refreshing = self.pool.submit(agent.credentials, self.account, True)
        requested, done = threading.Event(), threading.Event()
        try:
            self.assertTrue(entered.wait(2))
            removing = self.pool.submit(self.remove_in_thread, requested, done)
            self.assertTrue(requested.wait(2))
            self.assertFalse(done.wait(.2), 'Separate native components must share the account guard')
        finally:
            release.set()
        self.assertEqual(refreshing.result(2), ('synthetic-access', None))
        self.assertEqual(removing.result(2), {})
        self.assert_removed()

    def test_real_process_background_refresh_then_remove_leaves_no_credentials(self):
        entered, release = self.context.Event(), self.context.Event()
        results = self.context.Queue()
        process = self.context.Process(target=refresh_in_process,
            args=(str(self.store_path), str(Path(self.temporary.name) / 'agent-process.json'),
                  self.account, self.values, entered, release, results))
        process.start()
        requested, done = threading.Event(), threading.Event()
        try:
            self.assertTrue(entered.wait(2), 'Actual background process reached refresh')
            removing = self.pool.submit(self.remove_in_thread, requested, done)
            self.assertTrue(requested.wait(2))
            self.assertFalse(done.wait(.2), 'Process-local RLock cannot satisfy this ownership boundary')
        finally:
            release.set()
            process.join(3)
            if process.is_alive():
                process.terminate(); process.join(2)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(results.get(timeout=2), ('PASS', ('synthetic-access', None)))
        self.assertEqual(removing.result(2), {})
        self.assert_removed()
        results.close(); results.join_thread()

    def test_reentrant_account_lock_retains_guard_until_outermost_exit(self):
        requested, acquired = self.context.Event(), self.context.Event()
        process = self.context.Process(target=acquire_in_process,
                                      args=(str(self.store_path), self.account.id, requested, acquired))
        try:
            with account_lock(self.store_path, self.account.id):
                with account_lock(self.store_path, self.account.id):
                    process.start()
                    self.assertTrue(requested.wait(2))
                    self.assertFalse(acquired.wait(.2), 'Actual second process must remain excluded')
                self.assertFalse(acquired.wait(.2), 'Inner exit must not release the outer credential guard')
            self.assertTrue(acquired.wait(2))
            process.join(2)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.pid and process.is_alive():
                process.terminate(); process.join(2)


if __name__ == '__main__':
    unittest.main()
