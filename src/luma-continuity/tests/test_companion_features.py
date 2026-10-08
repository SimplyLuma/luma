"""Do Not Disturb, one-tap hotspot and approve-with-your-phone. Synthetic, no D-Bus, no network."""
import base64
import hashlib
import os
from pathlib import Path
import secrets
import stat
import tempfile
import unittest

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from luma_continuity import transport
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import DesktopAdapters
from luma_continuity.companion_approval import (KEYS_FILE, ApprovalBroker, ApprovalKeyChanged, ApprovalUnavailable,
                                                message, verify)
from luma_continuity.companion_desktop import HOTSPOTS_FILE, GnomeDnd, HotspotJoiner

PHONE = 'ab' * 32
OTHER_PHONE = 'cd' * 32


class FakeSettings:
    def __init__(self, banners=True):
        self.values, self.handlers, self.writes = {'show-banners': banners}, {}, []

    def get_boolean(self, key):
        return self.values[key]

    def set_boolean(self, key, value):
        self.writes.append(value)
        changed = self.values[key] != value
        self.values[key] = value
        if changed:
            for handler in list(self.handlers.values()): handler(self, key)

    def connect(self, signal, handler):
        assert signal == 'changed::show-banners'
        token = len(self.handlers) + 1
        self.handlers[token] = handler
        return token

    def disconnect(self, token):
        del self.handlers[token]


class DndTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings, self.sent = FakeSettings(), []
        self.owner = GnomeDnd(self.settings, send=self.sent.append)
        self.adapters = DesktopAdapters(self.temp.name, downloads=Path(self.temp.name) / 'Downloads', dnd=self.owner)

    def test_adapter_only_with_an_owner_and_strict_payload(self):
        self.assertNotIn('dnd.set', DesktopAdapters(self.temp.name, downloads=self.temp.name).for_peer(PHONE))
        adapter = self.adapters.for_peer(PHONE)['dnd.set']
        for payload in ({}, {'on': 1}, {'on': 'true'}, {'on': True, 'until': 5}, {'state': True}):
            with self.assertRaises(ValueError): adapter('dnd.set', payload)
        self.assertEqual(self.settings.writes, [])

    def test_phone_change_is_applied_without_echo(self):
        adapter = self.adapters.for_peer(PHONE)['dnd.set']
        self.assertEqual(adapter('dnd.set', {'on': True}), {'on': True})
        self.assertFalse(self.settings.values['show-banners'])
        self.assertEqual(self.sent, [])  # the changed signal fired but the phone's own value is not sent back
        self.assertEqual(adapter('dnd.set', {'on': True}), {'on': True})
        self.assertEqual(self.settings.writes, [False])  # already on: nothing written
        self.assertEqual(adapter('dnd.set', {'on': False}), {'on': False})
        self.assertEqual(self.sent, [])

    def test_local_change_is_sent_once(self):
        self.settings.set_boolean('show-banners', False)
        self.assertEqual(self.sent, [True])
        self.owner._changed()  # a duplicate signal for the same state
        self.assertEqual(self.sent, [True])
        self.settings.set_boolean('show-banners', True)
        self.assertEqual(self.sent, [True, False])
        # The phone then confirms the state it was sent: still no echo either way.
        self.adapters.for_peer(PHONE)['dnd.set']('dnd.set', {'on': False})
        self.assertEqual(self.sent, [True, False])
        self.owner.close()
        self.settings.set_boolean('show-banners', False)
        self.assertEqual(self.sent, [True, False])

    def test_broadcast_only_to_granted_phones_and_never_raises(self):
        calls = []
        def call(directory, fingerprint, capability, payload, lifetime):
            calls.append((fingerprint, capability, payload, lifetime))
            if fingerprint == OTHER_PHONE: raise OSError('unreachable')
        devices = [{'fingerprint': PHONE, 'outgoing': ['dnd.set']}, {'fingerprint': 'ef' * 32, 'outgoing': ['device.ring']},
                   {'fingerprint': OTHER_PHONE, 'outgoing': ['dnd.set']}]
        GnomeDnd.broadcast(call, self.temp.name, devices, True)
        self.assertEqual(calls, [(PHONE, 'dnd.set', {'on': True}, 30), (OTHER_PHONE, 'dnd.set', {'on': True}, 30)])


class FakeNM:
    def __init__(self, *, visible=(), saved=None, active=(), joins_after=None):
        self.calls, self.visible, self.saved = [], set(visible), dict(saved or {})
        self.active, self.joins_after, self.scans = set(active), joins_after, 0

    def wifi_devices(self):
        self.calls.append(('devices',)); return ['/dev/wifi0']

    def request_scan(self, device):
        self.calls.append(('scan', device)); self.scans += 1
        if self.joins_after and self.scans >= self.joins_after[0]: self.active.add(self.joins_after[1])

    def visible_ssids(self, device):
        self.calls.append(('visible', device)); return set(self.visible)

    def connection_ssid(self, uuid):
        self.calls.append(('ssid', uuid)); return self.saved.get(uuid)

    def activate(self, uuid, device):
        self.calls.append(('activate', uuid, device)); self.active.add(uuid)

    def active_wifi(self):
        self.calls.append(('active',)); return set(self.active)


class HotspotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.requests = []

    def joiner(self, nm, result=None):
        def call(directory, fingerprint, capability, payload, lifetime):
            self.requests.append((fingerprint, capability, payload))
            return {'state': 'complete', 'result': result or {'error': 'needs-user'}}
        return HotspotJoiner(nm, self.directory, call=call, sleep=lambda seconds: nm.calls.append(('sleep', seconds)))

    def test_known_hotspot_is_joined_after_a_rescan_in_order(self):
        nm = FakeNM(visible={b'Home', b'Pixel hotspot'}, saved={'uuid-hotspot': b'Pixel hotspot'})
        joiner = self.joiner(nm)
        joiner._remember(PHONE, 'uuid-hotspot')
        self.assertEqual(joiner.request(PHONE), 'joined')
        self.assertEqual(self.requests, [(PHONE, 'hotspot.request', {})])
        self.assertEqual(nm.calls, [('active',), ('devices',), ('scan', '/dev/wifi0'), ('sleep', 5), ('active',),
                                    ('ssid', 'uuid-hotspot'), ('visible', '/dev/wifi0'),
                                    ('activate', 'uuid-hotspot', '/dev/wifi0')])

    def test_waits_until_the_hotspot_appears_then_stops(self):
        nm = FakeNM(saved={'uuid-hotspot': b'Pixel hotspot'})
        joiner = self.joiner(nm)
        joiner._remember(PHONE, 'uuid-hotspot')
        original = nm.request_scan
        def scan(device):
            original(device)
            if nm.scans == 3: nm.visible.add(b'Pixel hotspot')
        nm.request_scan = scan
        self.assertEqual(joiner.join(PHONE), 'joined')
        self.assertEqual(nm.scans, 3)
        self.assertEqual([call for call in nm.calls if call[0] == 'activate'], [('activate', 'uuid-hotspot', '/dev/wifi0')])

    def test_first_time_learns_only_the_connection_the_person_chose(self):
        nm = FakeNM(active={'uuid-home'}, joins_after=(2, 'uuid-phone'))
        joiner = self.joiner(nm)
        self.assertEqual(joiner.request(PHONE), 'learned')
        self.assertEqual(joiner.remembered(PHONE), 'uuid-phone')
        self.assertFalse(any(call[0] == 'activate' for call in nm.calls))
        self.assertEqual(stat.S_IMODE((self.directory / HOTSPOTS_FILE).stat().st_mode), 0o600)
        self.assertNotIn('psk', (self.directory / HOTSPOTS_FILE).read_text())

    def test_refused_request_does_not_scan_and_deleted_network_is_forgotten(self):
        nm = FakeNM()
        self.assertEqual(self.joiner(nm, result={'error': 'revoked'}).request(PHONE), 'refused')
        self.assertEqual(nm.calls, [])
        joiner = self.joiner(nm)
        joiner._remember(PHONE, 'uuid-gone')
        joiner.ATTEMPTS = 2
        self.assertEqual(joiner.join(PHONE), 'not-found')
        self.assertIsNone(joiner.remembered(PHONE))
        self.assertEqual(nm.scans, 2)


class ApprovalVectorTest(unittest.TestCase):
    # Same constants as protocol/src/test/kotlin/.../ApprovalSignatureTest.kt.
    PIN = '18050ffd0924e288b03139d645375e5330287ae97fbba1303fdbbb8bacfc034b'
    REQUEST = '94af58a870ae07ed7108a9eff76ab174'
    CHALLENGE = '2LaWHh/UL48Ix7CppvcW4DDtOksYtyVkq7NaXcXz9/0='
    DIGEST = '74444e9c69f8fc64c99af955fd4736366a143cad7f9d763edcc12f7898ad2b23'
    KEY = ('MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEIFrO4EGO7an1NCMFW5nciXOKCyc0dZd5EuULyPT2cOKc6yNdZOoch9iQpZkDl5MIvEs08A'
           'RDIPr7lK+RtlBC3g==')
    SIGNATURE = 'MEQCIAeDod3k5gU2Q6SJ9OcyladZd1ixBsog9Ol5ZPDhjhgaAiA5CWJge6SEYng0UYhABvcVphNKHGZGi8GTYg15I2DwHA=='

    def test_message_and_signature_match_kotlin(self):
        data = message(self.PIN, self.REQUEST, self.CHALLENGE, True)
        self.assertEqual(data, f'luma-approval/1|{self.PIN}|{self.REQUEST}|{self.CHALLENGE}|true'.encode())
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.DIGEST)
        key, signature = base64.b64decode(self.KEY), base64.b64decode(self.SIGNATURE)
        self.assertTrue(verify(key, data, signature))
        self.assertFalse(verify(key, message(self.PIN, self.REQUEST, self.CHALLENGE, False), signature))
        for bad in (('EE' * 32, self.REQUEST, self.CHALLENGE), (self.PIN, 'A' * 32, self.CHALLENGE),
                    (self.PIN, self.REQUEST, self.CHALLENGE[:-1])):
            with self.assertRaises(ValueError): message(*bad, True)


class Phone:
    """A phone with its own approval key, signing like the Android app."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_key(self):
        return base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()

    def answer(self, request, desktop_pin, approved=True, *, challenge=None, signed=True):
        challenge = challenge or request['challenge']
        signature = None
        if signed:
            signature = base64.b64encode(self.key.sign(message(desktop_pin, request['request'], challenge, approved),
                                                       ec.ECDSA(hashes.SHA256()))).decode()
        return {'request': request['request'], 'approved': approved, 'signature': signature, 'public_key': self.public_key}


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'desktop'
        create_identity(self.directory)
        self.pin = transport.fingerprint((self.directory / 'device.pem').read_text())
        self.clock = [1_757_790_000.0]
        self.sent, self.receipt = [], {'state': 'complete', 'result': {'error': 'needs-user'}}
        def call(directory, fingerprint, capability, payload, lifetime):
            self.sent.append({'fingerprint': fingerprint, 'capability': capability, 'payload': payload, 'lifetime': lifetime})
            if isinstance(self.receipt, Exception): raise self.receipt
            return self.receipt
        self.broker = ApprovalBroker(self.directory, call=call, submit=lambda work: work(), now=lambda: self.clock[0],
                                     timer=False)
        self.adapters = DesktopAdapters(self.directory, downloads=Path(self.temp.name) / 'Downloads', approval=self.broker)
        self.phone = Phone()

    def respond(self, payload, peer=PHONE):
        # Maps adapter exceptions exactly like session.Receiver.handle.
        try: return self.adapters.for_peer(peer)['auth.response']('auth.response', payload)
        except (ValueError, KeyError): return {'error': 'invalid-request'}
        except PermissionError: return {'error': 'revoked'}

    def ask(self, **options):
        future = self.broker.request(PHONE, options.get('reason', 'Unlock Settings'), options.get('app', 'Settings'),
                                     timeout=options.get('timeout', 60))
        return future, self.sent[-1]['payload']

    def test_request_payload_is_bounded(self):
        future, payload = self.ask(timeout=45)
        self.assertEqual(self.sent[-1]['capability'], 'auth.request')
        self.assertEqual(set(payload), {'request', 'reason', 'app', 'challenge', 'expires'})
        self.assertEqual(len(base64.b64decode(payload['challenge'])), 32)
        self.assertEqual(payload['expires'], int(self.clock[0]) + 45)
        self.assertEqual(self.sent[-1]['lifetime'], 45)
        self.assertFalse(future.done())
        for reason, app, timeout in (('', 'Settings', 60), ('x' * 201, 'Settings', 60), ('ok', 'y' * 65, 60),
                                     ('Pay ‮yrtne', 'Settings', 60), ('two\nlines', 'Settings', 60),
                                     ('ok', 'Settings', 61), ('ok', 'Settings', 0)):
            with self.assertRaises(ValueError): self.broker.request(PHONE, reason, app, timeout=timeout)

    def test_valid_signature_approves_and_pins_the_key(self):
        future, payload = self.ask()
        self.assertEqual(self.respond(self.phone.answer(payload, self.pin)), {'accepted': True})
        self.assertIs(future.result(0), True)
        keys = self.directory / KEYS_FILE
        self.assertIn(self.phone.public_key, keys.read_text())
        self.assertEqual(stat.S_IMODE(keys.stat().st_mode), 0o600)
        # A signed denial resolves False.
        future, payload = self.ask()
        self.assertEqual(self.respond(self.phone.answer(payload, self.pin, approved=False)), {'accepted': True})
        self.assertIs(future.result(0), False)

    def test_unsigned_denial_and_unsigned_approval(self):
        future, payload = self.ask()
        self.assertEqual(self.respond(self.phone.answer(payload, self.pin, approved=False, signed=False)), {'accepted': True})
        self.assertIs(future.result(0), False)
        self.assertFalse((self.directory / KEYS_FILE).exists())  # a denial never pins a key
        future, payload = self.ask()
        self.assertEqual(self.respond(self.phone.answer(payload, self.pin, signed=False)), {'error': 'invalid-request'})
        self.assertFalse(future.done())  # malformed responses do not consume the request

    def test_wrong_challenge_is_rejected(self):
        future, payload = self.ask()
        other = base64.b64encode(secrets.token_bytes(32)).decode()
        self.assertEqual(self.respond(self.phone.answer(payload, self.pin, challenge=other)), {'error': 'invalid-request'})
        self.assertIs(future.result(0), False)
        self.assertFalse((self.directory / KEYS_FILE).exists())

    def test_wrong_desktop_pin_binding_is_rejected(self):
        future, payload = self.ask()
        self.assertEqual(self.respond(self.phone.answer(payload, 'ee' * 32)), {'error': 'invalid-request'})
        self.assertIs(future.result(0), False)

    def test_replay_and_other_phone_are_rejected(self):
        future, payload = self.ask()
        answer = self.phone.answer(payload, self.pin)
        self.assertEqual(self.respond(answer, peer=OTHER_PHONE), {'error': 'expired'})
        self.assertFalse(future.done())
        self.assertEqual(self.respond(answer), {'accepted': True})
        self.assertIs(future.result(0), True)
        self.assertEqual(self.respond(answer), {'error': 'expired'})
        # Replaying an old approval against a new request fails the new challenge.
        second, payload2 = self.ask()
        self.assertEqual(self.respond({**answer, 'request': payload2['request']}), {'error': 'invalid-request'})
        self.assertIs(second.result(0), False)

    def test_expiry(self):
        future, payload = self.ask(timeout=30)
        self.clock[0] += 30
        self.assertEqual(self.respond(self.phone.answer(payload, self.pin)), {'error': 'expired'})
        self.assertIs(future.result(0), False)
        future, payload = self.ask(timeout=10)
        self.clock[0] += 11
        self.broker.sweep()
        self.assertIs(future.result(0), False)
        self.assertEqual(self.broker.pending(), 0)
        self.assertEqual(self.respond(self.phone.answer(payload, self.pin)), {'error': 'expired'})

    def test_key_change_after_pinning(self):
        future, payload = self.ask()
        self.respond(self.phone.answer(payload, self.pin))
        self.assertTrue(future.result(0))
        replaced = Phone()  # e.g. Keystore invalidated the key after a new fingerprint was enrolled
        future, payload = self.ask()
        self.assertEqual(self.respond(replaced.answer(payload, self.pin)), {'error': 'key-changed'})
        with self.assertRaises(ApprovalKeyChanged): future.result(0)
        self.broker.forget(PHONE)  # the person trusts the new key
        future, payload = self.ask()
        self.assertEqual(self.respond(replaced.answer(payload, self.pin)), {'accepted': True})
        self.assertTrue(future.result(0))

    def test_malformed_responses(self):
        future, payload = self.ask()
        good = self.phone.answer(payload, self.pin)
        p384 = base64.b64encode(ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        for bad in ({**good, 'extra': 1}, {**good, 'approved': 'yes'}, {**good, 'request': payload['request'].upper()},
                    {**good, 'public_key': p384}, {**good, 'public_key': 'not base64!'},
                    {**good, 'signature': base64.b64encode(b'x' * 81).decode()}, {k: v for k, v in good.items() if k != 'signature'}):
            self.assertEqual(self.respond(bad), {'error': 'invalid-request'})
        self.assertFalse(future.done())
        self.assertEqual(self.respond(good), {'accepted': True})

    def test_phone_that_cannot_be_asked(self):
        self.receipt = {'state': 'complete', 'result': {'error': 'revoked'}}
        future, _ = self.ask()
        with self.assertRaises(ApprovalUnavailable): future.result(0)
        self.receipt = OSError('no route')
        future, _ = self.ask()
        with self.assertRaises(ApprovalUnavailable): future.result(0)
        self.assertEqual(self.broker.pending(), 0)

    def test_timer_expiry_and_close(self):
        broker = ApprovalBroker(self.directory, call=lambda *args, **kwargs: {'state': 'complete', 'result': {'error': 'needs-user'}},
                                submit=lambda work: work())
        future = broker.request(PHONE, 'Unlock Settings', 'Settings', timeout=1)
        self.assertIs(future.result(3), False)
        future = broker.request(PHONE, 'Unlock Settings', 'Settings', timeout=60)
        broker.close()
        self.assertIs(future.result(0), False)


if __name__ == '__main__':
    unittest.main()
