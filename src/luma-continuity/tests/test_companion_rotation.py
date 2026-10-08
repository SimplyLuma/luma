"""Certificate rotation for companion pairings. Synthetic identities on loopback."""
import os
from pathlib import Path
import secrets
import ssl
import tempfile
import time
import unittest

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity import transport
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, Registry
from luma_continuity.companion_rotation import (RotationAdapters, needs_rotation, proof_message, rotate_identity,
                                                sign_proof, verify_proof)
from luma_continuity.local import PairedExchange
from luma_continuity.policy import Denied, Journal




class RotationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.desktop, self.phone = root / 'desktop', root / 'phone'
        create_identity(self.desktop)
        create_identity(self.phone)
        self.events = []
        self.adapters = DesktopAdapters(self.desktop, downloads=root / 'Downloads',
                                        clipboard_set=lambda text, sensitive: self.events.append(text))
        self.rotation = RotationAdapters(self.desktop, changed=lambda kind, pin: self.events.append((kind, pin)))

        def factory(peer):
            return {**self.adapters.for_peer(peer), **self.rotation.for_peer(peer)}

        def after(peer):
            self.adapters.after_request(peer)
            self.rotation.after_request(peer)

        self.listener = CompanionListener(self.desktop, addresses=['127.0.0.1'], adapter_factory=factory, port=0, after=after)
        self.port = self.listener.start()
        self.addCleanup(self.listener.stop)
        session = PairingSession(self.desktop, name='Desk', addresses=['127.0.0.1'], listen_port=self.port,
                                 phone_to_desktop=['clipboard.write', 'device.rotate'], desktop_to_phone=['device.rotate'])
        phone_pem = (self.phone / 'device.pem').read_text()
        response = session.handle({'schema': 'org.projectluma.companion-pairing/v1', 'kind': 'request', 'token': session.token,
                                   'certificate': phone_pem, 'pin': transport.fingerprint(phone_pem), 'name': 'Pixel',
                                   'model': 'Pixel 9', 'platform': 'android', 'listen_port': 47811,
                                   'phone_to_desktop': ['clipboard.write', 'device.rotate'], 'desktop_to_phone': ['device.rotate']},
                                  '127.0.0.1')
        self.epoch = response['epoch']
        self.desktop_pin = session.pin
        self.phone_pin = transport.fingerprint(phone_pem)
        (self.phone / 'peers').mkdir(mode=0o700, exist_ok=True)
        (self.phone / 'peers' / (self.desktop_pin + '.pem')).write_text((self.desktop / 'device.pem').read_text())
        journal = Journal(self.phone / 'continuity.db')
        journal.approve(self.desktop_pin, self.epoch, {'device.rotate'}, outgoing_grants={'clipboard.write', 'device.rotate'})
        journal.close()

    def call(self, directory, capability, payload):
        request = {'version': 1, 'epoch': self.epoch, 'id': secrets.token_hex(16), 'account': None,
                   'capability': capability, 'expires': int(time.time()) + 60, 'payload': payload}
        return PairedExchange(directory, self.desktop_pin, self.epoch, '127.0.0.1', self.port)(request)

    def new_phone_identity(self):
        replacement = Path(self.temp.name) / 'phone-next'
        create_identity(replacement, days=398)
        pem = (replacement / 'device.pem').read_text()
        return replacement, pem, transport.fingerprint(pem)

    def wait_rotated(self):
        for _ in range(50):
            if any(isinstance(event, tuple) and event[0] == 'rotated' for event in self.events):
                return True
            time.sleep(.05)
        return False

    def test_phone_rotates_and_keeps_grants(self):
        replacement, pem, pin = self.new_phone_identity()
        proof = sign_proof(replacement / 'device.key', proof_message(self.phone_pin, pin, self.desktop_pin, self.epoch))
        receipt = self.call(self.phone, 'device.rotate', {'certificate': pem, 'pin': pin, 'proof': proof})
        self.assertEqual(receipt['result'], {'accepted': True, 'pin': pin})
        self.assertTrue(self.wait_rotated())
        device = Registry(self.desktop).active()
        self.assertIn(pin, device)
        self.assertNotIn(self.phone_pin, device)
        self.assertEqual(device[pin]['incoming'], ['clipboard.write', 'device.rotate'])
        self.assertEqual(device[pin]['epoch'], self.epoch)
        # The phone switches to the new identity; the old one is refused.
        for name in ('device.pem', 'device.key'):
            os.replace(replacement / name, self.phone / name)
        self.assertEqual(self.call(self.phone, 'clipboard.write', {'text': 'after rotation'})['state'], 'complete')
        self.assertIn('after rotation', self.events)
        self.assertFalse((self.desktop / 'peers' / (self.phone_pin + '.pem')).exists())

    def test_old_identity_is_refused_after_rotation(self):
        backup = Path(self.temp.name) / 'phone-old'
        backup.mkdir(mode=0o700)
        for name in ('device.pem', 'device.key', 'continuity.db'):
            (backup / name).write_bytes((self.phone / name).read_bytes())
        (backup / 'peers').mkdir(mode=0o700)
        (backup / 'peers' / (self.desktop_pin + '.pem')).write_text((self.desktop / 'device.pem').read_text())
        replacement, pem, pin = self.new_phone_identity()
        proof = sign_proof(replacement / 'device.key', proof_message(self.phone_pin, pin, self.desktop_pin, self.epoch))
        self.call(self.phone, 'device.rotate', {'certificate': pem, 'pin': pin, 'proof': proof})
        self.assertTrue(self.wait_rotated())
        with self.assertRaises((OSError, ssl.SSLError, EOFError, ValueError, PermissionError)):
            self.call(backup, 'clipboard.write', {'text': 'stale key'})
        self.assertNotIn('stale key', self.events)

    def test_proof_must_be_signed_by_new_key_and_bound_to_pairing(self):
        replacement, pem, pin = self.new_phone_identity()
        # Signed with the OLD key: rejected.
        wrong_key = sign_proof(self.phone / 'device.key', proof_message(self.phone_pin, pin, self.desktop_pin, self.epoch))
        self.assertEqual(self.call(self.phone, 'device.rotate', {'certificate': pem, 'pin': pin, 'proof': wrong_key})['result'],
                         {'error': 'invalid-request'})
        # Bound to a different receiver: rejected.
        other = sign_proof(replacement / 'device.key', proof_message(self.phone_pin, pin, 'e' * 64, self.epoch))
        self.assertEqual(self.call(self.phone, 'device.rotate', {'certificate': pem, 'pin': pin, 'proof': other})['result'],
                         {'error': 'invalid-request'})
        # Pin not matching the certificate: rejected.
        good = sign_proof(replacement / 'device.key', proof_message(self.phone_pin, pin, self.desktop_pin, self.epoch))
        self.assertEqual(self.call(self.phone, 'device.rotate', {'certificate': pem, 'pin': 'f' * 64, 'proof': good})['result'],
                         {'error': 'invalid-request'})
        time.sleep(.2)
        self.assertIn(self.phone_pin, Registry(self.desktop).active())

    def test_rotation_into_an_existing_pairing_is_denied(self):
        journal = Journal(self.desktop / 'continuity.db')
        try:
            other = 'a' * 64
            journal.approve(other, 'b' * 32, {'clipboard.write'})
            with self.assertRaises(Denied):
                journal.rotate(self.phone_pin, other)
        finally:
            journal.close()

    def test_desktop_rotation_helper_installs_new_identity_when_a_peer_accepts(self):
        old_pin = self.desktop_pin
        seen = []

        def fake_call(fingerprint, capability, payload):
            seen.append((fingerprint, capability))
            message = proof_message(old_pin, payload['pin'], fingerprint, self.epoch)
            verify_proof(payload['certificate'], message, payload['proof'])
            return {'state': 'complete', 'result': {'accepted': True, 'pin': payload['pin']}}

        result = rotate_identity(self.desktop, {self.phone_pin: self.epoch}, fake_call)
        self.assertTrue(result['rotated'])
        self.assertEqual(seen, [(self.phone_pin, 'device.rotate')])
        self.assertEqual(transport.fingerprint((self.desktop / 'device.pem').read_text()), result['pin'])
        self.assertTrue((self.desktop / 'device.pem.previous').exists())
        self.assertEqual((self.desktop / 'device.key').stat().st_mode & 0o077, 0)

    def test_desktop_rotation_keeps_identity_when_nobody_accepts(self):
        before = (self.desktop / 'device.pem').read_text()
        result = rotate_identity(self.desktop, {self.phone_pin: self.epoch}, lambda *args: (_ for _ in ()).throw(OSError('offline')))
        self.assertFalse(result['rotated'])
        self.assertEqual((self.desktop / 'device.pem').read_text(), before)

    def test_needs_rotation(self):
        self.assertFalse(needs_rotation(self.desktop, days=10))
        self.assertTrue(needs_rotation(self.desktop, days=31))


if __name__ == '__main__':
    unittest.main()
