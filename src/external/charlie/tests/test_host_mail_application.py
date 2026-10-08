# SPDX-License-Identifier: Apache-2.0
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time
import unittest
from gi.repository import GLib
from charlie_luma.application import CharlieApplication
from charlie_luma.model import Account, ServerConfig

class Client:
    def __init__(self, account): self.account = account; self.calls = []; self.closed = threading.Event()
    def request(self, operation, data):
        self.calls.append((operation, data))
        return {'account': asdict(self.account)} if operation.endswith('SignIn') else {}

class HostApplicationTests(unittest.TestCase):
    def setUp(self):
        self.account = Account('mail-test', 'Alice', 'alice@example.org', 'custom')
        self.config = ServerConfig('imap.example.org', smtp_host='smtp.example.org')
        self.reloads = []; self.background = []; self.client = Client(self.account)
        self.app = SimpleNamespace(host_client=self.client, store=None, secrets=None, call_mail_agent=self.reloads.append, ensure_mail_agent=lambda: self.background.append(True))
    def test_google_entry_refuses_without_host_call_and_completes_on_glib(self):
        from charlie_luma.oauth import GoogleOAuthUnavailable
        complete = []
        future = CharlieApplication.begin_google_sign_in(self.app,
            lambda account, error: complete.append((account, error)))
        with self.assertRaises(GoogleOAuthUnavailable): future.result()
        end = time.monotonic() + 2
        while not complete and time.monotonic() < end:
            GLib.MainContext.default().iteration(False); time.sleep(.005)
        self.assertEqual(len(complete), 1)
        self.assertIsNone(complete[0][0]); self.assertIsInstance(complete[0][1], GoogleOAuthUnavailable)
        self.assertEqual(self.client.calls, []); self.assertEqual(self.reloads, [])

    def test_host_save_uses_request_and_reloads_native_background_once(self):
        CharlieApplication.save_account(self.app, self.account, self.config, 'input-password')
        self.assertEqual(self.client.calls, [('SaveAccount', {'account': asdict(self.account), 'config': asdict(self.config), 'password': 'input-password'})])
        self.assertEqual(self.reloads, ['Reload']); self.assertEqual(self.background, [True]); self.assertIsNone(self.app.secrets)
    def test_host_remove_never_reads_keyring_and_reloads_background_once(self):
        CharlieApplication.remove_account(self.app, self.account.id)
        self.assertEqual(self.client.calls, [('RemoveAccount', {'account_id': self.account.id})]); self.assertEqual(self.reloads, ['Reload'])
    def test_host_oauth_receives_account_only_and_dispatches_completion_on_glib(self):
        self.app.oauth_pool = ThreadPoolExecutor(max_workers=1); self.addCleanup(self.app.oauth_pool.shutdown)
        complete = []; future = CharlieApplication._begin_host_sign_in(self.app, 'MicrosoftSignIn', {'client_id':'public-fixture'}, lambda account, error: complete.append((account, error)))
        future.result(3); end = time.monotonic() + 3
        while not complete and time.monotonic() < end: GLib.MainContext.default().iteration(False); time.sleep(.01)
        self.assertEqual(complete, [(self.account, None)]); self.assertEqual(self.reloads, ['Reload']); self.assertEqual(self.background, [True]); self.assertIsNone(self.app.secrets)
    def test_closed_host_oauth_client_suppresses_late_ui_completion(self):
        self.app.oauth_pool = ThreadPoolExecutor(max_workers=1); self.addCleanup(self.app.oauth_pool.shutdown); self.client.closed.set()
        complete = []; future = CharlieApplication._begin_host_sign_in(self.app, 'MicrosoftSignIn', {'client_id':'public-fixture'}, lambda *args: complete.append(args)); future.result(3)
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        self.assertEqual(complete, []); self.assertEqual(self.reloads, [])

class AsyncAccountTests(unittest.TestCase):
    def test_glib_heartbeat_and_failure_completion_while_native_owner_is_blocked(self):
        from charlie_luma.host_mail_client import Engine
        for operation in ('SaveAccount', 'RemoveAccount'):
            with self.subTest(operation=operation):
                entered = threading.Event(); release = threading.Event(); closed = threading.Event()
                class BlockedClient:
                    def __init__(self): self.closed = closed
                    def request(self, _operation, _arguments):
                        entered.set(); release.wait(3); raise OSError('controlled unavailable owner')
                    def close(self): closed.set()
                client = BlockedClient(); engine = Engine(client)
                reloads = []; complete = []; heartbeat = []
                app = SimpleNamespace(host_client=client, engine=engine,
                    call_mail_agent=reloads.append, ensure_mail_agent=lambda: None)
                try:
                    future = CharlieApplication._account_operation_async(app, operation, {},
                        lambda: self.fail('native fallback used'), complete.append)
                    self.assertTrue(entered.wait(1))
                    GLib.idle_add(lambda: (heartbeat.append(True), GLib.SOURCE_REMOVE)[1])
                    deadline = time.monotonic()+1
                    while not heartbeat and time.monotonic()<deadline:
                        GLib.MainContext.default().iteration(False); time.sleep(.005)
                    self.assertEqual(heartbeat, [True]); self.assertFalse(future.done()); self.assertEqual(complete, [])
                    release.set()
                    try: future.result(2)
                    except OSError: pass
                    deadline = time.monotonic()+1
                    while not complete and time.monotonic()<deadline:
                        GLib.MainContext.default().iteration(False); time.sleep(.005)
                    self.assertEqual(len(complete), 1); self.assertIsInstance(complete[0], OSError)
                    self.assertEqual(reloads, [])
                finally: release.set(); engine.close()
