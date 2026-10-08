# SPDX-License-Identifier: Apache-2.0
"""minisign public keys and signatures (https://jedisct1.github.io/minisign/).

Format, as written by minisign 0.8 and later:

* public key: an ``untrusted comment:`` line, then base64 of
  ``"Ed" || key_id[8] || ed25519_public_key[32]``;
* signature: an ``untrusted comment:`` line, base64 of
  ``algorithm[2] || key_id[8] || ed25519_signature[64]``, a
  ``trusted comment:`` line, and base64 of the global signature, which is an
  Ed25519 signature over ``signature[64] || trusted_comment_bytes``.

``algorithm`` is ``ED`` (the file's BLAKE2b-512 digest is signed; minisign's
default) or ``Ed`` (the raw file is signed; legacy). Both are accepted, the
key id must match, and the trusted comment must verify too, so it can be
relied on. Nothing here trusts the untrusted comments.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
from pathlib import Path

from . import ed25519

__all__ = ("PublicKey", "Signature", "SignatureError", "verify", "load_keys", "sign_for_tests")

MAX_SIGNATURE_BYTES = 4096


class SignatureError(Exception):
    """The signature is malformed, from an unknown key, or does not verify."""


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    key: bytes
    source: str = ""

    @property
    def key_id_hex(self) -> str:
        # minisign prints the id as the little-endian 64-bit integer in hex.
        return self.key_id[::-1].hex().upper()

    @classmethod
    def parse(cls, text: str, source: str = "") -> "PublicKey":
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        if lines and lines[0].startswith("untrusted comment:"):
            lines = lines[1:]
        if len(lines) != 1:
            raise SignatureError(f"{source or 'public key'}: expected one base64 key line")
        raw = _b64(lines[0], "public key")
        if len(raw) != 42 or raw[:2] != b"Ed":
            raise SignatureError(f"{source or 'public key'}: not a minisign Ed25519 public key")
        return cls(key_id=raw[2:10], key=raw[10:], source=source)


@dataclass(frozen=True)
class Signature:
    algorithm: bytes
    key_id: bytes
    signature: bytes
    trusted_comment: str
    global_signature: bytes

    @classmethod
    def parse(cls, data: bytes) -> "Signature":
        if len(data) > MAX_SIGNATURE_BYTES:
            raise SignatureError("signature file is too large")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise SignatureError("signature file is not UTF-8") from None
        lines = text.replace("\r\n", "\n").split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        if len(lines) != 4 or not lines[0].startswith("untrusted comment:"):
            raise SignatureError("signature file does not have minisign's four lines")
        raw = _b64(lines[1], "signature")
        if len(raw) != 74 or raw[:2] not in (b"ED", b"Ed"):
            raise SignatureError("not a minisign Ed25519 signature")
        prefix = "trusted comment: "
        if not lines[2].startswith(prefix):
            raise SignatureError("signature has no trusted comment")
        global_signature = _b64(lines[3], "global signature")
        if len(global_signature) != 64:
            raise SignatureError("global signature has the wrong length")
        return cls(algorithm=raw[:2], key_id=raw[2:10], signature=raw[10:],
                   trusted_comment=lines[2][len(prefix):], global_signature=global_signature)


def _b64(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError(f"{what} is not valid base64") from None


def verify(data: bytes, signature_file: bytes, keys: list[PublicKey] | tuple[PublicKey, ...]) -> Signature:
    """Verify ``data`` against a minisign signature from one of ``keys``.

    Returns the parsed signature (its trusted comment is authenticated) or
    raises SignatureError.
    """
    signature = Signature.parse(signature_file)
    matching = [key for key in keys if key.key_id == signature.key_id]
    if not matching:
        raise SignatureError("signed by a key this computer does not trust")
    message = hashlib.blake2b(data, digest_size=64).digest() if signature.algorithm == b"ED" else data
    for key in matching:
        if not ed25519.verify(key.key, message, signature.signature):
            continue
        comment = signature.trusted_comment.encode("utf-8")
        if not ed25519.verify(key.key, signature.signature + comment, signature.global_signature):
            raise SignatureError("the trusted comment does not verify")
        return signature
    raise SignatureError("signature does not verify")


def load_keys(directories) -> list[PublicKey]:
    """Load every ``*.pub`` minisign key from the given directories, in order."""
    keys: list[PublicKey] = []
    for directory in directories:
        path = Path(directory)
        if not path.is_dir():
            continue
        for item in sorted(path.glob("*.pub")):
            try:
                keys.append(PublicKey.parse(item.read_text(encoding="utf-8"), str(item)))
            except (OSError, UnicodeDecodeError, SignatureError):
                continue
    return keys


def sign_for_tests(secret: bytes, key_id: bytes, data: bytes, trusted_comment: str,
                   prehashed: bool = True) -> tuple[str, str]:
    """Produce a (public key file, signature file) pair. Tests and local rigs only."""
    public = ed25519.public_key(secret)
    algorithm = b"ED" if prehashed else b"Ed"
    message = hashlib.blake2b(data, digest_size=64).digest() if prehashed else data
    signature = ed25519.sign(secret, message)
    global_signature = ed25519.sign(secret, signature + trusted_comment.encode("utf-8"))
    key_id_hex = key_id[::-1].hex().upper()
    public_file = (f"untrusted comment: minisign public key {key_id_hex}\n"
                   + base64.b64encode(b"Ed" + key_id + public).decode() + "\n")
    signature_file = ("untrusted comment: signature from minisign secret key\n"
                      + base64.b64encode(algorithm + key_id + signature).decode() + "\n"
                      + f"trusted comment: {trusted_comment}\n"
                      + base64.b64encode(global_signature).decode() + "\n")
    return public_file, signature_file
