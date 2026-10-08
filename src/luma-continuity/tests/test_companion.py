"""Synthetic tests for companion pairing, listener and adapters. Loopback only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import tempfile
import unittest

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity import transport
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import (SCHEMA, PAIRING_ALPN, CompanionListener, DesktopAdapters, PairingSession,
                                       Registry, call, sas)
from luma_continuity.policy import Denied, Journal
from luma_continuity.session import Receiver


def phone_request(session, phone, token=None, **changes):
    request = {'schema': SCHEMA, 'kind': 'request', 'token': token or session.token,
               'certificate': (phone / 'device.pem').read_text(),
               'pin': transport.fingerprint((phone / 'device.pem').read_text()),
               'name': 'Pixel 9', 'model': 'Google Pixel 9', 'platform': 'android', 'listen_port': 47811,
               'phone_to_desktop': ['device.status', 'clipboard.write'], 'desktop_to_phone': ['device.ring']}
    request.update(changes)
    return request


def pair_over_tls(session, request):
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_REQUIRED
    tls.load_verify_locations(cafile=str(session.directory / 'device.pem'))
    tls.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.set_alpn_protocols([PAIRING_ALPN])
    with socket.create_connection(('127.0.0.1', session.port), timeout=5) as raw:
        with tls.wrap_socket(raw) as stream:
            actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
            assert actual == session.pin and stream.selected_alpn_protocol() == PAIRING_ALPN
            transport.send(stream, request)
            return transport.receive(stream)


class CompanionPairingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.desktop, self.phone = root / 'desktop', root / 'phone'
        create_identity(self.desktop)
        create_identity(self.phone)

    def tearDown(self):
        self.temp.cleanup()

    def session(self, **options):
        values = dict(name='Desk', addresses=['127.0.0.1'], phone_to_desktop=['device.status', 'clipboard.write', 'files.write'],
                      desktop_to_phone=['device.ring', 'clipboard.write'])
        values.update(options)
        session = PairingSession(self.desktop, **values)
        session.start()
        self.addCleanup(session.cancel)
        return session

    def test_qr_pairing_narrows_grants_and_records_device(self):
        session = self.session()
        uri = session.uri()
        self.assertIn(f'pin={session.pin}', uri)
        self.assertIn(f'token={session.token}', uri)
        response = pair_over_tls(session, phone_request(session, self.phone))
        self.assertEqual(response['kind'], 'accepted')
        # The desktop never widens what the phone asked for.
        self.assertEqual(response['phone_to_desktop'], ['clipboard.write', 'device.status'])
        self.assertEqual(response['desktop_to_phone'], ['device.ring'])
        phone_pin = transport.fingerprint((self.phone / 'device.pem').read_text())
        self.assertEqual(response['sas'], sas(session.pin, phone_pin, response['epoch'], session.token))
        session.join(2)
        self.assertEqual(session.state, 'paired')
        device = Registry(self.desktop).active()[phone_pin]
        self.assertEqual(device['incoming'], ['clipboard.write', 'device.status'])
        self.assertEqual(device['outgoing'], ['device.ring'])
        self.assertEqual((device['host'], device['port']), ('127.0.0.1', 47811))
        self.assertTrue((self.desktop / 'peers' / (phone_pin + '.pem')).exists())

    def test_wrong_secret_is_rejected_and_session_closes_after_five(self):
        session = self.session()
        for _ in range(5):
            response = session.handle(phone_request(session, self.phone, token=secrets.token_hex(16)), '127.0.0.1')
            self.assertEqual(response['kind'], 'rejected')
        self.assertEqual(session.state, 'failed')
        self.assertEqual(session.handle(phone_request(session, self.phone), '127.0.0.1')['kind'], 'rejected')
        self.assertEqual(Registry(self.desktop).devices(), [])

    def test_invitation_is_single_use(self):
        session = self.session()
        self.assertEqual(session.handle(phone_request(session, self.phone), '127.0.0.1')['kind'], 'accepted')
        other = Path(self.temp.name) / 'other'
        create_identity(other)
        self.assertEqual(session.handle(phone_request(session, other), '127.0.0.1')['kind'], 'rejected')

    def test_expired_invitation_is_rejected(self):
        clock = [1_000_000.0]
        session = PairingSession(self.desktop, name='Desk', addresses=['127.0.0.1'], phone_to_desktop=['device.status'],
                                 desktop_to_phone=[], now=lambda: clock[0])
        clock[0] += 601
        self.assertEqual(session.handle(phone_request(session, self.phone), '127.0.0.1')['kind'], 'rejected')

    def test_mismatched_pin_or_self_pairing_fails(self):
        session = self.session()
        bad = phone_request(session, self.phone, pin='0' * 64)
        self.assertEqual(session.handle(bad, '127.0.0.1')['kind'], 'rejected')
        session = self.session()
        selfish = phone_request(session, self.desktop)
        self.assertEqual(session.handle(selfish, '127.0.0.1')['kind'], 'rejected')

    def test_unknown_fields_and_platforms_are_rejected(self):
        session = self.session()
        request = phone_request(session, self.phone)
        request['extra'] = True
        self.assertEqual(session.handle(request, '127.0.0.1')['kind'], 'rejected')
        session = self.session()
        self.assertEqual(session.handle(phone_request(session, self.phone, platform='ios'), '127.0.0.1')['kind'], 'rejected')


class CompanionExchangeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.desktop, self.phone = self.root / 'desktop', self.root / 'phone'
        create_identity(self.desktop)
        create_identity(self.phone)
        self.events = []
        self.adapters = DesktopAdapters(self.desktop, downloads=self.root / 'Downloads',
                                        notify=lambda kind, peer, value: self.events.append((kind, value)),
                                        clipboard_set=lambda text, sensitive: self.events.append(('clipboard', text)))
        self.listener = CompanionListener(self.desktop, addresses=['127.0.0.1'], adapter_factory=self.adapters.for_peer, port=0,
                                          after=self.adapters.after_request)
        port = self.listener.start()
        self.addCleanup(self.listener.stop)
        session = PairingSession(self.desktop, name='Desk', addresses=['127.0.0.1'], listen_port=port,
                                 phone_to_desktop=['clipboard.write', 'files.write', 'links.open', 'notifications.mirror'],
                                 desktop_to_phone=['device.ring'])
        response = session.handle(phone_request(session, self.phone, phone_to_desktop=['clipboard.write', 'files.write', 'links.open', 'notifications.mirror']), '127.0.0.1')
        self.epoch, self.desktop_port = response['epoch'], port
        self.phone_pin = transport.fingerprint((self.phone / 'device.pem').read_text())
        # Mirror the approval the phone would store.
        (self.phone / 'peers').mkdir(mode=0o700, exist_ok=True)
        (self.phone / 'peers' / (session.pin + '.pem')).write_text((self.desktop / 'device.pem').read_text())
        journal = Journal(self.phone / 'continuity.db')
        journal.approve(session.pin, self.epoch, {'device.ring'}, outgoing_grants=set(response['phone_to_desktop']))
        journal.close()
        self.desktop_pin = session.pin

    def tearDown(self):
        self.temp.cleanup()

    def phone_call(self, capability, payload, request_id=None):
        from luma_continuity.local import PairedExchange
        request = {'version': 1, 'epoch': self.epoch, 'id': request_id or secrets.token_hex(16), 'account': None,
                   'capability': capability, 'expires': int(__import__('time').time()) + 60, 'payload': payload}
        return PairedExchange(self.phone, self.desktop_pin, self.epoch, '127.0.0.1', self.desktop_port)(request)

    def test_clipboard_and_link_reach_desktop_effects(self):
        self.assertEqual(self.phone_call('clipboard.write', {'text': 'hello', 'sensitive': False})['state'], 'complete')
        self.assertEqual(self.phone_call('links.open', {'url': 'https://example.org/a'})['state'], 'complete')
        self.assertEqual(self.events[0], ('clipboard', 'hello'))
        self.assertEqual(self.events[1][1]['url'], 'https://example.org/a')
        receipt = self.phone_call('links.open', {'url': 'file:///etc/passwd'})
        self.assertEqual(receipt['result'], {'error': 'invalid-request'})

    def test_file_transfer_is_verified_and_atomic(self):
        data = secrets.token_bytes(700_000)
        transfer = secrets.token_hex(16)
        first, second = data[:500_000], data[500_000:]
        common = dict(transfer=transfer, name='photo.jpg', size=len(data))
        receipt = self.phone_call('files.write', {**common, 'offset': 0, 'data': base64.b64encode(first).decode(), 'final': False, 'sha256': None})
        self.assertEqual(receipt['result'], {'received': 500_000})
        self.assertFalse((self.root / 'Downloads' / 'photo.jpg').exists())
        receipt = self.phone_call('files.write', {**common, 'offset': 500_000, 'data': base64.b64encode(second).decode(),
                                                  'final': True, 'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual(receipt['result']['name'], 'photo.jpg')
        self.assertEqual((self.root / 'Downloads' / 'photo.jpg').read_bytes(), data)

    def test_file_with_wrong_digest_is_discarded(self):
        data = b'abc'
        receipt = self.phone_call('files.write', dict(transfer=secrets.token_hex(16), name='x.txt', size=3, offset=0,
                                                      data=base64.b64encode(data).decode(), final=True, sha256='0' * 64))
        self.assertEqual(receipt['result'], {'error': 'invalid-request'})
        self.assertFalse((self.root / 'Downloads' / 'x.txt').exists())

    def test_path_traversal_names_are_rejected(self):
        receipt = self.phone_call('files.write', dict(transfer=secrets.token_hex(16), name='../evil', size=1, offset=0,
                                                      data='YQ==', final=True, sha256=hashlib.sha256(b'a').hexdigest()))
        self.assertEqual(receipt['result'], {'error': 'invalid-request'})

    def test_replayed_request_returns_cached_receipt_without_second_effect(self):
        request_id = secrets.token_hex(16)
        self.phone_call('clipboard.write', {'text': 'once'}, request_id)
        self.phone_call('clipboard.write', {'text': 'once'}, request_id)
        self.assertEqual([event for event in self.events if event[0] == 'clipboard'], [('clipboard', 'once')])

    def test_status_is_persisted_after_the_receipt(self):
        Registry(self.desktop).set_incoming(self.phone_pin, ['clipboard.write', 'device.status'])
        journal = Journal(self.phone / 'continuity.db')
        try: journal.set_grants(self.desktop_pin, {'device.ring'}, outgoing_grants={'clipboard.write', 'device.status'})
        finally: journal.close()
        receipt = self.phone_call('device.status', {'battery': 64, 'charging': False, 'network': 'wifi', 'listen_port': 47999})
        self.assertEqual(receipt['result'], {'accepted': True})
        for _ in range(50):
            device = Registry(self.desktop).active()[self.phone_pin]
            if device['status']: break
            __import__('time').sleep(.05)
        self.assertEqual(device['status'], {'battery': 64, 'charging': False, 'network': 'wifi'})
        self.assertEqual(device['port'], 47999)

    def test_ungranted_capability_and_revocation_are_denied(self):
        with self.assertRaises(Denied):
            self.phone_call('device.status', {'battery': 50})
        Registry(self.desktop).revoke(self.phone_pin)
        with self.assertRaises((OSError, ssl.SSLError, ValueError, EOFError)):
            self.phone_call('clipboard.write', {'text': 'after revoke'})
        self.assertNotIn(('clipboard', 'after revoke'), self.events)

    def test_notification_payloads_are_strict(self):
        post = {'op': 'post', 'key': secrets.token_hex(16), 'app': 'Messages', 'package': 'com.google.android.apps.messaging',
                'title': 'Sam', 'text': 'Running late', 'when': 1, 'silent': False, 'conversation': None,
                'actions': [{'id': secrets.token_hex(16), 'label': 'Reply', 'reply': True}]}
        self.assertEqual(self.phone_call('notifications.mirror', post)['result'], {'accepted': True})
        self.assertEqual(self.phone_call('notifications.mirror', {**post, 'actions': [{'id': 'x', 'label': 'Bad', 'reply': 1}]})['result'],
                         {'error': 'invalid-request'})


class DesktopCallTest(unittest.TestCase):
    def test_call_requires_a_seen_route(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'desktop'
            create_identity(directory)
            with self.assertRaises(Denied):
                call(directory, '0' * 64, 'device.ring', {})


class JournalPruneTest(unittest.TestCase):
    def test_expired_tombstones_are_pruned_when_quota_is_reached(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Journal(Path(temp) / 'state' / 'continuity.db')
            self.addCleanup(journal.close)
            peer, epoch = 'a' * 64, 'b' * 32
            journal.approve(peer, epoch, {'device.status'})
            journal.db.executemany("INSERT INTO requests(peer,epoch,id,digest,state,response,expires) VALUES(?,?,?,?,?,NULL,?)",
                                   [(peer, epoch, '%032x' % index, 'c' * 64, 'complete', 10) for index in range(10000)])
            request = {'version': 1, 'epoch': epoch, 'id': 'd' * 32, 'account': None, 'capability': 'device.status',
                       'expires': 2_000_000_060, 'payload': {}}
            result = journal.dispatch(peer, request, lambda capability, payload: {'ok': True}, now=2_000_000_000)
            self.assertEqual(result['state'], 'complete')
            try: self.assertEqual(journal.db.execute('SELECT count(*) FROM requests').fetchone()[0], 1)
            finally: journal.close()


if __name__ == '__main__':
    unittest.main()
