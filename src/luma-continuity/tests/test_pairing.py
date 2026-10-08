import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from luma_continuity.bootstrap import create_identity
from luma_continuity.pairing import create_offer, accept_offer, finish_offer
from luma_continuity.policy import Journal, Denied
from luma_continuity.transport import encode


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.a, self.b = (Path(self.temp.name) / part for part in ('desktop', 'phone'))
        self.a_pin, self.b_pin = create_identity(self.a), create_identity(self.b)
        self.now = int(time.time())
        self.offer = create_offer(self.a, a_to_b=['messages.read', 'messages.send'], b_to_a=[], now=self.now)

    def accept(self, **kwargs):
        return accept_offer(self.b, self.offer, verified_a_pin=self.a_pin,
                            a_to_b=['messages.read'], b_to_a=[], now=self.now, **kwargs)

    def finish(self, response, **kwargs):
        return finish_offer(self.a, response, verified_b_pin=self.b_pin, now=self.now, **kwargs)

    def test_asymmetric_permissions_and_single_consumption(self):
        response = self.accept(); result = self.finish(response)
        self.assertEqual(result['incoming'], []); self.assertEqual(result['outgoing'], ['messages.read'])
        for directory, pin, incoming, outgoing in ((self.a, self.b_pin, [], ['messages.read']),
                                                   (self.b, self.a_pin, ['messages.read'], [])):
            journal = Journal(directory / 'continuity.db')
            try:
                row = journal.db.execute('SELECT grants,outgoing_grants FROM peers WHERE fingerprint=?', (pin,)).fetchone()
                self.assertEqual(json.loads(row[0]), incoming); self.assertEqual(json.loads(row[1]), outgoing)
            finally: journal.close()
        with self.assertRaises(PermissionError): self.finish(response)
        with self.assertRaises(sqlite3.IntegrityError): self.accept()

    def test_incoming_edit_preserves_outgoing_permissions(self):
        self.finish(self.accept())
        journal = Journal(self.b / 'continuity.db')
        try:
            journal.set_grants(self.a_pin, ['messages.read', 'messages.send'])
            row = journal.db.execute('SELECT outgoing_grants FROM peers WHERE fingerprint=?', (self.a_pin,)).fetchone()
            self.assertEqual(json.loads(row[0]), [])
        finally: journal.close()

    def test_expiry_during_approval_rolls_back_both_sides(self):
        from luma_continuity import pairing
        real = pairing._persist_certificate
        response = self.accept()
        for finish in (False, True):
            current = [self.now]
            def persist(*args):
                real(*args)
                current[0] += 601
            directory = self.a if finish else Path(self.temp.name) / 'third'
            if not finish: create_identity(directory)
            with patch('luma_continuity.pairing.time.time', side_effect=lambda: current[0]), patch(
                    'luma_continuity.pairing._persist_certificate', side_effect=persist):
                with self.assertRaises(ValueError):
                    if finish:
                        finish_offer(directory, response, verified_b_pin=self.b_pin)
                    else:
                        accept_offer(directory, self.offer, verified_a_pin=self.a_pin,
                                     a_to_b=['messages.read'], b_to_a=[])
            journal = Journal(directory / 'continuity.db')
            try: self.assertEqual(journal.db.execute('SELECT count(*) FROM peers').fetchone()[0], 0)
            finally: journal.close()
        self.finish(response)

    def test_reverse_request_denied(self):
        result = self.finish(self.accept())
        journal = Journal(self.a / 'continuity.db')
        try:
            request = dict(version=1, epoch=result['epoch'], id='a' * 32, account=None,
                           capability='messages.read', expires=self.now + 10, payload={})
            with self.assertRaises(Denied): journal.dispatch(self.b_pin, request, lambda *_: self.fail('reverse access'))
        finally: journal.close()

    def test_wrong_independently_confirmed_pin_grants_nothing(self):
        with self.assertRaises(PermissionError):
            accept_offer(self.b, self.offer, verified_a_pin='a' * 64, a_to_b=['messages.read'], b_to_a=[], now=self.now)
        self.assertFalse((self.b / 'peers').exists())
        response = self.accept()
        with self.assertRaises(PermissionError):
            finish_offer(self.a, response, verified_b_pin='b' * 64, now=self.now)

    def test_offer_expiry_and_response_binding(self):
        response = self.accept()
        with self.assertRaises(ValueError): finish_offer(self.a, response, verified_b_pin=self.b_pin, now=self.now + 601)
        for field, value in [('epoch', 'c' * 32), ('offer_digest', 'd' * 64), ('account', 'different-account')]:
            document = json.loads(response); document[field] = value
            with self.assertRaises(PermissionError): self.finish(encode(document))
        self.finish(response)  # rejected attempts did not consume the offer

    def test_scope_expansion_and_unimplemented_capabilities(self):
        response = json.loads(self.accept()); response['b_to_a'] = ['messages.read']
        with self.assertRaises(PermissionError): self.finish(encode(response))
        with self.assertRaises(ValueError):
            create_offer(self.a, a_to_b=['screen.control'], b_to_a=[], now=self.now)
        with self.assertRaises(ValueError):
            create_offer(self.a, a_to_b=['messages.read', 'messages.read'], b_to_a=[], now=self.now)

    def test_cross_account_offer_rejected(self):
        offer = create_offer(self.a, a_to_b=['messages.read'], b_to_a=[], account='issuer|account-one', now=self.now)
        with self.assertRaises(PermissionError):
            accept_offer(self.b, offer, verified_a_pin=self.a_pin, a_to_b=['messages.read'], b_to_a=[],
                         account='issuer|account-two', now=self.now)

    def test_certificate_write_failure_leaves_offer_unconsumed(self):
        response = self.accept()
        with patch('luma_continuity.pairing._persist_certificate', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError): self.finish(response)
        journal = Journal(self.a / 'continuity.db')
        try: self.assertEqual(journal.db.execute('SELECT count(*) FROM peers').fetchone()[0], 0)
        finally: journal.close()
        self.finish(response)

    def test_revocation_cannot_be_undone_by_reimport(self):
        response = self.accept(); self.finish(response)
        journal = Journal(self.a / 'continuity.db'); journal.revoke(self.b_pin); journal.close()
        with self.assertRaises(PermissionError): self.finish(response, replace=True)

    def test_invalid_document_and_certificate_rejected(self):
        original = json.loads(self.offer)
        for mutate in (lambda d: d.update(created_at=True), lambda d: d.update(created_at=self.now + 60, expires_at=self.now + 100),
                       lambda d: d.update(a_certificate=d['a_certificate'] * 2), lambda d: d.update(extra='unknown')):
            doc = original.copy(); mutate(doc)
            with self.assertRaises(ValueError):
                accept_offer(self.b, encode(doc), verified_a_pin=self.a_pin, a_to_b=['messages.read'], b_to_a=[], now=self.now)
        duplicate = self.offer[:-1] + b',"kind":"offer"}'
        with self.assertRaises(ValueError):
            accept_offer(self.b, duplicate, verified_a_pin=self.a_pin, a_to_b=['messages.read'], b_to_a=[], now=self.now)

    def test_old_journal_migration_preserves_existing_grants(self):
        old = Path(self.temp.name) / 'old.db'
        db = sqlite3.connect(old)
        db.execute('CREATE TABLE peers(fingerprint TEXT PRIMARY KEY,epoch TEXT NOT NULL,account TEXT,grants TEXT NOT NULL,revoked INTEGER NOT NULL)')
        db.execute('INSERT INTO peers VALUES(?,?,?,?,0)', (self.a_pin, 'a' * 32, None, '["messages.read"]'))
        db.commit(); db.close(); old.chmod(0o600)
        journal = Journal(old)
        try:
            self.assertEqual(json.loads(journal.db.execute('SELECT outgoing_grants FROM peers').fetchone()[0]), ['messages.read'])
        finally: journal.close()
