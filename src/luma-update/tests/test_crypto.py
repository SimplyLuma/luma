# SPDX-License-Identifier: Apache-2.0
import base64
from pathlib import Path
import unittest

import fakes  # noqa: F401  (sets sys.path)
from luma_update import ed25519, minisign

# RFC 8032 section 7.1, TEST 1-3.
RFC8032 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]

FIXTURES = Path(__file__).parent / "fixtures"


class Ed25519Vectors(unittest.TestCase):
    def test_rfc8032_public_key_sign_verify(self):
        for secret, public, message, signature in RFC8032:
            secret, public, message, signature = map(bytes.fromhex, (secret, public, message, signature))
            self.assertEqual(ed25519.public_key(secret), public)
            self.assertEqual(ed25519.sign(secret, message), signature)
            self.assertTrue(ed25519.verify(public, message, signature))

    def test_rejects_tampering(self):
        _, public, message, signature = map(bytes.fromhex, RFC8032[2])
        self.assertFalse(ed25519.verify(public, message + b"\0", signature))
        flipped = bytearray(signature)
        flipped[0] ^= 1
        self.assertFalse(ed25519.verify(public, message, bytes(flipped)))
        other = bytes.fromhex(RFC8032[1][1])
        self.assertFalse(ed25519.verify(other, message, signature))

    def test_rejects_non_canonical_s(self):
        _, public, message, signature = map(bytes.fromhex, RFC8032[1])
        s = int.from_bytes(signature[32:], "little") + ed25519.L
        malleated = signature[:32] + s.to_bytes(32, "little")
        self.assertFalse(ed25519.verify(public, message, malleated))

    def test_rejects_bad_encodings(self):
        _, _, message, signature = map(bytes.fromhex, RFC8032[0])
        unreduced = (2 ** 255 - 1).to_bytes(32, "little")  # y >= p
        self.assertFalse(ed25519.verify(unreduced, message, signature))
        self.assertFalse(ed25519.verify(b"\0" * 31, message, signature))
        self.assertFalse(ed25519.verify(bytes.fromhex(RFC8032[0][1]), message, signature[:63]))


class Minisign(unittest.TestCase):
    def test_round_trip_prehashed_and_legacy(self):
        for prehashed in (True, False):
            public, signature = minisign.sign_for_tests(fakes.SECRET, fakes.KEY_ID, b"graph", "timestamp:1",
                                                        prehashed=prehashed)
            key = minisign.PublicKey.parse(public)
            parsed = minisign.verify(b"graph", signature.encode(), [key])
            self.assertEqual(parsed.trusted_comment, "timestamp:1")

    def test_wrong_key_data_and_comment(self):
        public, signature = minisign.sign_for_tests(fakes.SECRET, fakes.KEY_ID, b"graph", "timestamp:1")
        key = minisign.PublicKey.parse(public)
        with self.assertRaises(minisign.SignatureError):
            minisign.verify(b"graph!", signature.encode(), [key])
        other_public, _ = minisign.sign_for_tests(bytes(32), fakes.KEY_ID, b"", "")
        with self.assertRaises(minisign.SignatureError):
            minisign.verify(b"graph", signature.encode(), [minisign.PublicKey.parse(other_public)])
        stranger = minisign.PublicKey(key_id=b"\1" * 8, key=key.key)
        with self.assertRaisesRegex(minisign.SignatureError, "does not trust"):
            minisign.verify(b"graph", signature.encode(), [stranger])
        lines = signature.splitlines()
        lines[2] = "trusted comment: timestamp:2"
        with self.assertRaisesRegex(minisign.SignatureError, "trusted comment"):
            minisign.verify(b"graph", ("\n".join(lines) + "\n").encode(), [key])

    def test_malformed_signature_files(self):
        public, signature = minisign.sign_for_tests(fakes.SECRET, fakes.KEY_ID, b"graph", "t")
        key = minisign.PublicKey.parse(public)
        for bad in (b"", b"untrusted comment: x\n", signature.encode()[:-20], b"\xff" * 10,
                    signature.replace("trusted comment:", "comment:").encode(), b"x" * 5000):
            with self.assertRaises(minisign.SignatureError):
                minisign.verify(b"graph", bad, [key])
        with self.assertRaises(minisign.SignatureError):
            minisign.PublicKey.parse("untrusted comment: k\n" + base64.b64encode(b"Xx" + bytes(40)).decode())

    def test_real_minisign_fixture(self):
        """A signature made by the minisign 0.12 tool itself, not by this module."""
        data = (FIXTURES / "minisign-graph.json").read_bytes()
        key = minisign.PublicKey.parse((FIXTURES / "minisign-test.pub").read_text())
        signature = (FIXTURES / "minisign-graph.json.minisig").read_bytes()
        parsed = minisign.verify(data, signature, [key])
        self.assertIn("file:minisign-graph.json", parsed.trusted_comment)
        with self.assertRaises(minisign.SignatureError):
            minisign.verify(data + b" ", signature, [key])
        legacy = FIXTURES / "minisign-graph.json.legacy.minisig"
        self.assertEqual(minisign.Signature.parse(legacy.read_bytes()).algorithm, b"Ed")
        minisign.verify(data, legacy.read_bytes(), [key])


if __name__ == "__main__":
    unittest.main()
