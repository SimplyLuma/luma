# SPDX-License-Identifier: Apache-2.0
"""The pure-Python Ed25519 verifier agrees with OpenSSL, and minisign parsing edge cases.

Regression test from the independent review's crypto_diff proof. OpenSSL 3's
Ed25519 verification is the reference where the ``openssl`` command is
installed; the crafted cases also carry the answers OpenSSL 3.6 gave, so they
run everywhere. (``openssl pkeyutl -rawin`` cannot read an empty message file,
so the differential part uses non-empty messages; RFC 8032's empty-message
vector is in test_crypto.)
"""

import base64
import hashlib
import os
import random
import shutil
import subprocess
import tempfile
import unittest

import fakes  # noqa: F401  (sets sys.path)
from luma_update import ed25519 as E, minisign

SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def _openssl_ed25519():
    tool = shutil.which("openssl")
    if tool is None:
        return None
    probe = subprocess.run([tool, "list", "-public-key-algorithms"], capture_output=True, text=True)
    return tool if "ED25519" in probe.stdout.upper() else None


OPENSSL = _openssl_ed25519()


class Differential(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def openssl_verify(self, public, message, signature):
        key, data, sig = (os.path.join(self.tmp.name, n) for n in ("k.der", "m", "s"))
        with open(key, "wb") as f:
            f.write(SPKI_PREFIX + public)
        with open(data, "wb") as f:
            f.write(message)
        with open(sig, "wb") as f:
            f.write(signature)
        return subprocess.run([OPENSSL, "pkeyutl", "-verify", "-pubin", "-keyform", "DER", "-inkey", key, "-rawin",
                               "-in", data, "-sigfile", sig], capture_output=True).returncode == 0

    def crafted(self):
        secret = bytes(range(32))
        public = E.public_key(secret)
        message = b"graph"
        signature = E.sign(secret, message)
        r, s = signature[:32], int.from_bytes(signature[32:], "little")
        cases = [
            ("valid", public, message, signature, True),
            ("S + L (non-canonical S)", public, message, r + (s + E.L).to_bytes(32, "little"), False),
            ("S = L", public, message, r + E.L.to_bytes(32, "little"), False),
            ("S = 2^256 - 1", public, message, r + (2 ** 256 - 1).to_bytes(32, "little"), False),
            ("R identity, S = 0", public, message, (1).to_bytes(32, "little") + bytes(32), False),
            ("R order 2, S = 0", public, message, (E.p - 1).to_bytes(32, "little") + bytes(32), False),
            ("R order 4, S = 0", public, message, bytes(32) + bytes(32), False),
            ("R negative zero x, S = 0", public, message, (1 | (1 << 255)).to_bytes(32, "little") + bytes(32), False),
            # The identity as public key verifies R = identity, S = 0 for any message under RFC 8032's
            # equation; OpenSSL agrees. Graph keys are fixed, trusted files, never taken from a signature.
            ("identity key", (1).to_bytes(32, "little"), message, (1).to_bytes(32, "little") + bytes(32), True),
        ]
        return cases

    def test_crafted_edge_cases(self):
        for label, public, message, signature, expected in self.crafted():
            self.assertEqual(E.verify(public, message, signature), expected, label)
            if OPENSSL:
                self.assertEqual(self.openssl_verify(public, message, signature), expected, f"openssl: {label}")

    @unittest.skipUnless(OPENSSL, "OpenSSL with Ed25519 is not installed")
    def test_random_signatures_and_bit_flips_agree_with_openssl(self):
        rng = random.Random(20260915)
        for length in range(1, 25):
            secret = bytes(rng.getrandbits(8) for _ in range(32))
            public = E.public_key(secret)
            message = bytes(rng.getrandbits(8) for _ in range(length))
            signature = E.sign(secret, message)
            self.assertTrue(E.verify(public, message, signature))
            self.assertTrue(self.openssl_verify(public, message, signature))
            for bit in (0, 255, 256, 511):
                mutated = bytearray(signature)
                mutated[bit // 8] ^= 1 << (bit % 8)
                self.assertEqual(E.verify(public, message, bytes(mutated)),
                                 self.openssl_verify(public, message, bytes(mutated)), (length, bit))


class MinisignParsing(unittest.TestCase):
    def setUp(self):
        secret = bytes(range(32))
        public_file, self.signature = minisign.sign_for_tests(secret, b"\x01" * 8, b"graph",
                                                              "timestamp:1\tfile:stable.json")
        self.key = minisign.PublicKey.parse(public_file)
        self.lines = self.signature.splitlines()

    def accepted(self, text, data=b"graph"):
        try:
            minisign.verify(data, text.encode(), [self.key])
            return True
        except minisign.SignatureError:
            return False

    def test_signature_file_variants(self):
        lines = self.lines
        raw = base64.b64decode(lines[1])
        legacy = "\n".join([lines[0], base64.b64encode(b"Ed" + raw[2:]).decode(), lines[2], lines[3]]) + "\n"
        self.assertTrue(self.accepted(self.signature))
        # Relabelling a prehashed signature as legacy makes it cover the raw data: refused.
        self.assertFalse(self.accepted(legacy))
        # ... and it verifies only for data that is itself the BLAKE2b-512 digest, as minisign's legacy mode does.
        self.assertTrue(self.accepted(legacy, data=hashlib.blake2b(b"graph", digest_size=64).digest()))
        self.assertTrue(self.accepted(self.signature.replace("\n", "\r\n")))
        self.assertTrue(self.accepted("\n".join([lines[0], lines[1], lines[2] + "\r", lines[3]]) + "\n"))
        self.assertFalse(self.accepted("\n".join([lines[0], "", lines[1], lines[2], lines[3]]) + "\n"))
        self.assertFalse(self.accepted(self.signature, data=b"graph\n"))


if __name__ == "__main__":
    unittest.main()
