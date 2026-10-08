# SPDX-License-Identifier: Apache-2.0
from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
import unittest
from charlie_luma.host_mail_owner import MailOwner
from charlie_luma.host_mail_wire import validate
from charlie_luma.model import Account, ServerConfig, Draft
from charlie_luma.store import MailStore
from charlie_luma.engine import AuthenticationFailure

class Secrets:
    def __init__(self, refuse=False): self.values = {}; self.refuse = refuse
    def store(self, account, kind, value):
        if not self.refuse: self.values[account, kind] = value
    def lookup(self, account, kind): return self.values.get((account, kind))
    def clear(self, account, kind): return self.values.pop((account, kind), None) is not None

class Transport:
    def __init__(self): self.sent = []; self.synced = []
    def send(self, account, config, draft):
        self.sent.append((account, config, [Path(p).read_bytes() for p in draft.attachments], tuple(draft.attachments)))
    def fetch_mail(self, account, config, cancel): self.synced.append((account, config)); return ()

class MailOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.owner = MailOwner.__new__(MailOwner); self.owner.store = MailStore(Path(self.tmp.name) / 'mail.db'); self.addCleanup(self.owner.close)
        self.owner.secrets = Secrets(); self.owner.transport = Transport(); self.live = threading.Event()
        self.account = Account('mail-test', 'Alice', 'alice@example.org', 'custom')
        self.config = ServerConfig('imap.example.org', smtp_host='smtp.example.org')
    def save(self):
        return self.owner.perform('SaveAccount', validate('SaveAccount', {'account': asdict(self.account), 'config': asdict(self.config), 'password': 'host-only-secret'}), self.live)
    def test_password_never_enters_mailbox_or_response(self):
        value = self.save()
        self.assertEqual(value, {'account': asdict(self.account)})
        self.assertNotIn('host-only-secret', str(value))
        with self.owner.store.transaction() as db:
            for table in ('accounts', 'server_configs'):
                self.assertNotIn('host-only-secret', str(db.execute('SELECT * FROM ' + table).fetchall()))
        self.assertEqual(self.owner.secrets.lookup(self.account.id, 'password'), 'host-only-secret')
    def test_refused_keyring_write_does_not_create_account(self):
        self.owner.secrets = Secrets(refuse=True)
        with self.assertRaises(AuthenticationFailure): self.save()
        self.assertEqual(self.owner.store.accounts(), ())
    def test_unknown_account_cannot_dispatch_transport(self):
        with self.assertRaises(ValueError): self.owner.perform('Sync', {'account_id': 'missing'}, self.live)
        self.assertEqual(self.owner.transport.synced, [])
    def test_cancelled_request_never_mutates_account_or_secrets(self):
        self.live.set()
        with self.assertRaises(RuntimeError): self.save()
        self.assertEqual(self.owner.store.accounts(), ())
        self.assertEqual(self.owner.secrets.values, {})
    def test_send_uses_host_account_config_and_private_temporary_bytes(self):
        self.save(); draft = Draft(self.account.id, to=['bob@example.org'], body='Hello')
        self.owner.perform('Send', {'draft': draft, 'attachments': [('song.flac', b'audio-content')]}, self.live)
        account, config, contents, paths = self.owner.transport.sent[0]
        self.assertEqual((account, config), (self.account, self.config)); self.assertEqual(contents, [b'audio-content'])
        self.assertTrue(all(not Path(p).exists() for p in paths))
    def test_account_remove_clears_both_kinds_without_exposing_them(self):
        self.save(); self.owner.secrets.store(self.account.id, 'oauth-token', 'refresh-token')
        self.assertEqual(self.owner.perform('RemoveAccount', {'account_id': self.account.id}, self.live), {})
        self.assertEqual(self.owner.store.accounts(), ()); self.assertEqual(self.owner.secrets.values, {})
    def test_unknown_account_remove_does_not_touch_unrelated_secrets(self):
        self.owner.secrets.store('missing', 'password', 'retained')
        with self.assertRaises(ValueError): self.owner.perform('RemoveAccount', {'account_id': 'missing'}, self.live)
        self.assertEqual(self.owner.secrets.lookup('missing', 'password'), 'retained')
