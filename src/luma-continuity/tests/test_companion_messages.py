"""Prairie Messages through a companion phone (ADR-021): account-free selection.

The "phone" is a loopback CompanionListener serving the same messages.read and
messages.send payloads the Android app implements, backed by a synthetic store.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'prairie-core'))
from luma_continuity import transport
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import CompanionListener, PairingSession, Registry
from luma_continuity.message_devices import MessageDevices
from luma_continuity.message_provider import MessageProvider, SelectedPhone, companion_exchange, companion_observation
from luma_continuity.policy import Journal
from prairie_apps.messages_backend import MessageStore


class FakeAndroidMessages:
    """Mirrors the Android adapter: threads, per-thread messages, send by operation."""

    def __init__(self):
        self.messages = [
            {'uid': 'android-sms:1', 'address': '+15550100', 'body': 'Running late', 'timestamp': 1_757_000_000,
             'direction': 'incoming', 'state': 'received', 'attachments': [], 'quote': None},
        ]
        self.sent = []

    def __call__(self, capability, payload):
        if capability == 'messages.read' and set(payload) == {'query', 'limit'}:
            latest = self.messages[-1]
            return {'threads': [{'address': '+15550100', 'display_name': 'Sam', 'preview': latest['body'],
                                 'updated': latest['timestamp'], 'unread': 1}], 'truncated': False}
        if capability == 'messages.read' and set(payload) == {'address', 'limit'}:
            return {'messages': [m for m in self.messages if m['address'] == payload['address']][-payload['limit']:]}
        if capability == 'messages.send':
            uid = 'android-send:' + payload['operation']
            self.sent.append(payload)
            self.messages.append({'uid': uid, 'address': payload['address'], 'body': payload['body'],
                                  'timestamp': 1_757_000_100, 'direction': 'outgoing', 'state': 'sent',
                                  'attachments': [], 'quote': None})
            return {'uid': uid, 'state': 'queued'}
        raise ValueError('unsupported')


class PairedPhoneCase(unittest.TestCase):
    """A desktop and a loopback phone paired for messages."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.desktop, self.phone = root / 'desktop', root / 'phone'
        create_identity(self.desktop)
        create_identity(self.phone)
        self.android = FakeAndroidMessages()
        # The phone side: a listener that trusts the desktop and serves messages.
        self.phone_listener = CompanionListener(self.phone, addresses=['127.0.0.1'], port=0,
                                                adapter_factory=lambda peer: {'messages.read': self.android,
                                                                              'messages.send': self.android})
        phone_port = self.phone_listener.start()
        self.addCleanup(self.phone_listener.stop)
        session = PairingSession(self.desktop, name='Desk', addresses=['127.0.0.1'], listen_port=47810,
                                 phone_to_desktop=['device.status'], desktop_to_phone=['messages.read', 'messages.send'])
        phone_pem = (self.phone / 'device.pem').read_text()
        self.phone_pin = transport.fingerprint(phone_pem)
        response = session.handle({'schema': 'org.projectluma.companion-pairing/v1', 'kind': 'request', 'token': session.token,
                                   'certificate': phone_pem, 'pin': self.phone_pin, 'name': 'Pixel 9', 'model': 'Google Pixel 9',
                                   'platform': 'android', 'listen_port': phone_port,
                                   'phone_to_desktop': ['device.status'], 'desktop_to_phone': ['messages.read', 'messages.send']},
                                  '127.0.0.1')
        self.epoch = response['epoch']
        # The phone approves the desktop the way the app does after pairing.
        (self.phone / 'peers').mkdir(mode=0o700, exist_ok=True)
        (self.phone / 'peers' / (session.pin + '.pem')).write_text((self.desktop / 'device.pem').read_text())
        journal = Journal(self.phone / 'continuity.db')
        journal.approve(session.pin, self.epoch, {'messages.read', 'messages.send'}, outgoing_grants={'device.status'})
        journal.close()
        # The phone registry on the "phone" side needs the desktop recorded for its listener.
        registry = Registry(self.phone)
        j = registry._open()
        try:
            registry.record(j, session.pin, name='Desk', model='Luma', platform='android', host='127.0.0.1', port=47810, now=time.time())
            j.db.commit()
        finally:
            j.close()

    def provider(self):
        devices = MessageDevices(self.desktop, Path(self.temp.name) / 'config' / 'messages-phone.json', account=lambda: None)
        devices.select_companion(self.phone_pin)
        document = json.loads((Path(self.temp.name) / 'config' / 'messages-phone.json').read_text())
        selection = SelectedPhone(**document['phone'])
        self.assertEqual(selection.carrier, 'companion')
        self.assertIsNone(selection.account)
        return MessageProvider(self.desktop, selection, account_observation=companion_observation(self.desktop, self.phone_pin),
                               store_factory=MessageStore, dispatch=lambda fn: fn(), exchange_factory=companion_exchange)


class CompanionMessagesTest(PairedPhoneCase):
    def test_sync_and_send_through_companion_without_account(self):
        provider = self.provider()
        self.addCleanup(provider.close)
        self.assertTrue(provider.account_current())
        # Authorization is checked on the provider's worker, never the UI thread.
        state = provider.refresh().result(timeout=30)
        self.assertEqual(state['state'], 'ready', state)
        self.assertEqual(state['imported'], 1)
        store = MessageStore(provider.store_path)
        try:
            thread = store.thread('+15550100')
            self.assertEqual([m.body for m in thread], ['Running late'])
            draft_uid = store.add('+15550100', 'On my way', direction='outgoing', state='queued', uid=secrets.token_hex(16))
        finally:
            store.close()
        import threading
        result = {}
        worker = threading.Thread(target=lambda: result.update(provider.send_message(draft_uid if isinstance(draft_uid, str) else draft_uid.uid)))
        worker.start(); worker.join()
        self.assertEqual(result['state'], 'queued')
        state = provider.refresh().result(timeout=30)
        self.assertEqual(self.android.sent[0]['body'], 'On my way')
        self.assertEqual(state['state'], 'ready', state)
        store = MessageStore(provider.store_path)
        try:
            bodies = [m.body for m in store.thread('+15550100')]
        finally:
            store.close()
        # The sent message is reconciled, not duplicated.
        self.assertEqual(bodies.count('On my way'), 1, bodies)

    def test_revoked_companion_stops_messages(self):
        provider = self.provider()
        self.addCleanup(provider.close)
        Registry(self.desktop).revoke(self.phone_pin)
        self.assertFalse(provider.account_current())
        self.assertIn(provider.refresh().result(timeout=30)['state'], {'unavailable', 'offline'})

    def test_account_bound_selection_cannot_be_forged_as_companion(self):
        with self.assertRaises(ValueError):
            SelectedPhone(self.phone_pin, self.epoch, 'account-1', 'Pixel', '127.0.0.1', 47811, 'companion')
        with self.assertRaises(ValueError):
            SelectedPhone(self.phone_pin, self.epoch, None, 'Pixel', '127.0.0.1', 47811, 'lan')


class MessageServicesTest(PairedPhoneCase):
    """ADR-022: Messages lists every phone that allowed messages, beside this device's own."""

    def services(self, config=None):
        from unittest.mock import patch
        from luma_continuity import message_provider
        problems = []
        config = config or Path(self.temp.name) / 'config' / 'messages-phone.json'
        # _watch subscribes to the session bus; this test has none.
        with patch.object(message_provider, '_watch', lambda provider, *_args: provider):
            providers = message_provider.message_services(
                store_factory=MessageStore, dispatch=lambda fn: fn(), config_path=config,
                identity_directory=self.desktop, problem=problems.append)
        for provider in providers:
            self.addCleanup(provider.close)
        return providers, problems

    def test_a_paired_phone_that_allowed_messages_is_a_service_without_choosing_it(self):
        providers, problems = self.services()
        self.assertEqual(problems, [])
        self.assertEqual([(p.peer, p.label, p.selection.carrier, p.selection.account) for p in providers],
                         [(self.phone_pin, 'Pixel 9', 'companion', None)])
        state = providers[0].refresh().result(timeout=30)
        self.assertEqual(state['state'], 'ready', state)

    def test_a_saved_selection_of_the_same_phone_is_not_listed_twice(self):
        self.provider().close()
        providers, _problems = self.services()
        self.assertEqual([p.peer for p in providers], [self.phone_pin])

    def test_a_removed_phone_is_not_a_service(self):
        journal = Journal(self.desktop / 'continuity.db')
        try:
            journal.revoke(self.phone_pin)
        finally:
            journal.close()
        providers, _problems = self.services()
        self.assertEqual(providers, [])

    def test_a_damaged_saved_selection_is_reported_and_the_phones_still_listed(self):
        config = Path(self.temp.name) / 'private' / 'messages-phone.json'
        config.parent.mkdir(mode=0o700)
        config.write_text('{"version": 9}')
        config.chmod(0o600)
        providers, problems = self.services(config)
        self.assertEqual(len(problems), 1)
        self.assertEqual([p.peer for p in providers], [self.phone_pin])

    def test_no_connect_state_means_no_phone_services(self):
        from unittest.mock import patch
        from luma_continuity import message_provider
        with patch.object(message_provider, '_watch', lambda provider, *_args: provider):
            providers = message_provider.message_services(
                store_factory=MessageStore, dispatch=lambda fn: fn(),
                config_path=Path(self.temp.name) / 'absent.json', identity_directory=Path(self.temp.name) / 'nothing')
        self.assertEqual(providers, [])
        self.assertFalse((Path(self.temp.name) / 'nothing').exists())


if __name__ == '__main__':
    unittest.main()
