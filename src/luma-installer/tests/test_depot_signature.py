# SPDX-License-Identifier: Apache-2.0
"""The catalogue signature check: RFC 8032 vectors, minisign files, tampering."""

import base64
from pathlib import Path
import unittest
from unittest import mock

from luma_installer import depot_signature as ds

import minisign_signer as signer

FIXTURES = Path(__file__).resolve().parent / 'fixtures/minisign'

# RFC 8032, section 7.1: TEST 1, TEST 2, TEST 3 and TEST 1024 is omitted for length.
RFC8032 = (
    ('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bac'
     'c61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e'
     '458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025', 'af82',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290'
     'ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
)


class Ed25519(unittest.TestCase):
    def test_rfc8032_vectors_verify_with_the_reference_arithmetic(self):
        for public, message, signature in RFC8032:
            with self.subTest(public=public[:8]):
                self.assertTrue(ds.reference_verify(bytes.fromhex(public), bytes.fromhex(message),
                                                    bytes.fromhex(signature)))

    def test_rfc8032_vectors_verify_through_the_preferred_path(self):
        for public, message, signature in RFC8032:
            self.assertTrue(ds.ed25519_verify(bytes.fromhex(public), bytes.fromhex(message),
                                              bytes.fromhex(signature)))

    def test_the_fallback_is_used_when_cryptography_is_absent(self):
        public, message, signature = RFC8032[1]
        with mock.patch.dict('sys.modules', {'cryptography': None,
                                             'cryptography.exceptions': None,
                                             'cryptography.hazmat.primitives.asymmetric.ed25519': None}):
            self.assertTrue(ds.ed25519_verify(bytes.fromhex(public), bytes.fromhex(message),
                                              bytes.fromhex(signature)))

    def test_any_changed_bit_fails(self):
        public, message, signature = (bytes.fromhex(part) for part in RFC8032[2])
        for index in (0, 31, 32, 63):
            altered = bytearray(signature)
            altered[index] ^= 1
            self.assertFalse(ds.reference_verify(public, message, bytes(altered)))
        self.assertFalse(ds.reference_verify(public, message + b'\0', signature))
        altered_key = bytearray(public)
        altered_key[5] ^= 4
        self.assertFalse(ds.reference_verify(bytes(altered_key), message, signature))

    def test_non_canonical_s_and_bad_points_are_refused(self):
        public, message, signature = (bytes.fromhex(part) for part in RFC8032[0])
        s = int.from_bytes(signature[32:], 'little') + ds._L
        self.assertFalse(ds.reference_verify(public, message, signature[:32] + s.to_bytes(32, 'little')))
        self.assertFalse(ds.reference_verify(b'\xff' * 32, message, signature))
        self.assertFalse(ds.reference_verify(public[:31], message, signature))

    def test_the_test_signer_reproduces_the_rfc_signature(self):
        # Ed25519 is deterministic: the helper must match TEST 1 exactly.
        self.assertEqual(signer.public_key().hex(), RFC8032[0][0])
        self.assertEqual(signer.sign(b'').hex(), RFC8032[0][2])


class Minisign(unittest.TestCase):
    def setUp(self):
        self.message = b'{"schema_version": 4}\n'
        self.key = signer.public_key_file()

    def test_prehashed_and_legacy_signatures_verify(self):
        for prehashed in (True, False):
            with self.subTest(prehashed=prehashed):
                comment = ds.verify_file(self.key, self.message,
                                         signer.signature_file(self.message, prehashed=prehashed))
                self.assertIn('catalog-4.json', comment)

    def test_changed_contents_are_refused(self):
        signature = signer.signature_file(self.message)
        with self.assertRaises(ds.SignatureError):
            ds.verify_file(self.key, self.message + b' ', signature)

    def test_an_altered_trusted_comment_is_refused(self):
        lines = signer.signature_file(self.message).decode().splitlines()
        lines[2] = 'trusted comment: timestamp:1\tfile:something-else.json'
        with self.assertRaises(ds.SignatureError):
            ds.verify_file(self.key, self.message, '\n'.join(lines) + '\n')

    def test_another_key_is_refused_before_any_arithmetic(self):
        other = signer.signature_file(self.message, key_id=bytes(8))
        with mock.patch.object(ds, 'ed25519_verify') as verify, self.assertRaises(ds.SignatureError):
            ds.verify_file(self.key, self.message, other)
        verify.assert_not_called()
        seed = bytes(range(32))
        with self.assertRaises(ds.SignatureError):
            ds.verify_file(self.key, self.message, signer.signature_file(self.message, seed=seed))

    def test_malformed_files_are_refused(self):
        signature = signer.signature_file(self.message)
        raw = base64.b64decode(self.key.decode().splitlines()[1])
        cases = (
            (self.key, b'not a signature'),
            (self.key, signature.replace(b'trusted comment: ', b'comment: ')),
            (self.key, b'x' * (ds.SIGNATURE_MAX_BYTES + 1)),
            (b'untrusted comment: k\n' + base64.b64encode(b'Xx' + raw[2:]) + b'\n', signature),
            (b'untrusted comment: k\n' + base64.b64encode(raw[:-1]) + b'\n', signature),
            (b'untrusted comment: k\n!!!!\n', signature),
            (b'\xff\xfe', signature),
        )
        for key, sig in cases:
            with self.subTest(key=key[:24], sig=sig[:24]), self.assertRaises(ds.SignatureError):
                ds.verify_file(key, self.message, sig)
        algorithm = bytearray(base64.b64decode(signature.decode().splitlines()[1]))
        algorithm[:2] = b'EX'
        lines = signature.decode().splitlines()
        lines[1] = base64.b64encode(bytes(algorithm)).decode()
        with self.assertRaises(ds.SignatureError):
            ds.verify_file(self.key, self.message, '\n'.join(lines))


@unittest.skipUnless((FIXTURES / 'test.pub').is_file(), 'minisign fixtures not present')
class MadeByMinisign(unittest.TestCase):
    """Files produced by the minisign tool itself (see fixtures/minisign/README)."""

    def test_the_tools_signatures_verify(self):
        key = (FIXTURES / 'test.pub').read_bytes()
        message = (FIXTURES / 'catalog-4.json').read_bytes()
        for name in ('catalog-4.json.minisig', 'catalog-4.json.legacy.minisig'):
            path = FIXTURES / name
            if not path.is_file():
                continue
            with self.subTest(name=name):
                ds.verify_file(key, message, path.read_bytes())
                with mock.patch.dict('sys.modules', {'cryptography': None,
                                                     'cryptography.exceptions': None,
                                                     'cryptography.hazmat.primitives.asymmetric.ed25519': None}):
                    ds.verify_file(key, message, path.read_bytes())
                with self.assertRaises(ds.SignatureError):
                    ds.verify_file(key, message.replace(b'4', b'5', 1), path.read_bytes())


if __name__ == '__main__':
    unittest.main()
