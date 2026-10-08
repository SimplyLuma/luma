# SPDX-License-Identifier: Apache-2.0
"""Minisign verification for the signed Depot catalogue.

The catalogue snapshot is signed with a dedicated Ed25519 key in minisign's
format (ADR-028, section 4.1). Depot verifies it before a single byte of the
document is parsed, so an unsigned or altered catalogue is never read.

Two implementations of the same Ed25519 check live here. When
``python3-cryptography`` is importable its OpenSSL code is used; the Luma image
does not ship it, so on Luma the RFC 8032 reference arithmetic below is what
runs, and the package adds no dependency for it. Verification handles only public data -- the public key, the
message and the signature -- so the timing of that fallback discloses nothing
secret. Both paths are tested against the RFC 8032 vectors and against a
signature made by the minisign tool itself.

Supported signature algorithms:

* ``ED`` -- minisign's default since 0.10: the Ed25519 signature is over the
  BLAKE2b-512 digest of the file.
* ``Ed`` -- the legacy form: the signature is over the file itself.

Either way the trusted comment is authenticated by the global signature, and a
signature whose key id differs from the shipped key is refused before any
arithmetic happens.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac

SIGNATURE_MAX_BYTES = 4096
PUBLIC_KEY_MAX_BYTES = 1024


class SignatureError(ValueError):
    """The signature, the key, or the pair of them cannot be trusted."""


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    key: bytes


@dataclass(frozen=True)
class Signature:
    algorithm: bytes
    key_id: bytes
    signature: bytes
    trusted_comment: str
    global_signature: bytes


def _decode(line: str, length: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(line.strip(), validate=True)
    except (binascii.Error, ValueError) as error:
        raise SignatureError(f'The {what} is not valid base64.') from error
    if len(raw) != length:
        raise SignatureError(f'The {what} has an unexpected length.')
    return raw


def _lines(data: bytes | str, maximum: int, what: str) -> list[str]:
    if isinstance(data, bytes):
        if len(data) > maximum:
            raise SignatureError(f'The {what} is too large.')
        try:
            data = data.decode('utf-8')
        except UnicodeDecodeError as error:
            raise SignatureError(f'The {what} is not text.') from error
    elif len(data) > maximum:
        raise SignatureError(f'The {what} is too large.')
    return [line.rstrip('\r') for line in data.split('\n')]


def parse_public_key(data: bytes | str) -> PublicKey:
    """Read a minisign public key file (or the bare base64 line)."""
    lines = [line for line in _lines(data, PUBLIC_KEY_MAX_BYTES, 'public key') if line.strip()]
    if lines and lines[0].startswith('untrusted comment:'):
        lines = lines[1:]
    if len(lines) != 1:
        raise SignatureError('The public key file is not in minisign format.')
    raw = _decode(lines[0], 42, 'public key')
    if raw[:2] != b'Ed':
        raise SignatureError('The public key is not an Ed25519 minisign key.')
    return PublicKey(raw[2:10], raw[10:])


def parse_signature(data: bytes | str) -> Signature:
    lines = _lines(data, SIGNATURE_MAX_BYTES, 'signature')
    while lines and not lines[-1].strip():
        lines.pop()
    if (len(lines) != 4 or not lines[0].startswith('untrusted comment:')
            or not lines[2].startswith('trusted comment: ')):
        raise SignatureError('The signature is not in minisign format.')
    raw = _decode(lines[1], 74, 'signature')
    algorithm = raw[:2]
    if algorithm not in (b'Ed', b'ED'):
        raise SignatureError('The signature uses an unsupported algorithm.')
    return Signature(algorithm, raw[2:10], raw[10:],
                     lines[2][len('trusted comment: '):],
                     _decode(lines[3], 64, 'global signature'))


def verify(public_key: PublicKey, message: bytes, signature: Signature) -> str:
    """Verify ``message``; return the authenticated trusted comment.

    Raises :class:`SignatureError` for any mismatch. Nothing is returned for a
    partial success: the file signature and the comment signature must both
    hold.
    """
    if not hmac.compare_digest(public_key.key_id, signature.key_id):
        raise SignatureError('The catalogue was signed with a different key.')
    signed = (hashlib.blake2b(message, digest_size=64).digest()
              if signature.algorithm == b'ED' else message)
    if not ed25519_verify(public_key.key, signed, signature.signature):
        raise SignatureError('The catalogue signature does not match its contents.')
    comment = signature.trusted_comment.encode('utf-8')
    if not ed25519_verify(public_key.key, signature.signature + comment,
                          signature.global_signature):
        raise SignatureError('The signature comment was altered.')
    return signature.trusted_comment


def verify_file(public_key_data: bytes | str, message: bytes,
                signature_data: bytes | str) -> str:
    return verify(parse_public_key(public_key_data), message, parse_signature(signature_data))


# ── Ed25519 ──────────────────────────────────────────────────────────────

def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return reference_verify(public_key, message, signature)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


# RFC 8032, section 5.1, in extended twisted Edwards coordinates.
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = 2 * t1 * t2 * _D % _P
    D = 2 * z1 * z2 % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _multiply(scalar, point):
    result = (0, 1, 1, 0)
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a, b):
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(data):
    y = int.from_bytes(data, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_BASE_Y = 4 * pow(5, _P - 2, _P) % _P
_BASE = (_recover_x(_BASE_Y, 0), _BASE_Y, 1, _recover_x(_BASE_Y, 0) * _BASE_Y % _P)


def reference_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 Ed25519 verification without any third-party code."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    point_a = _decompress(public_key)
    if point_a is None:
        return False
    point_r = _decompress(signature[:32])
    if point_r is None:
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(),
                       'little') % _L
    return _equal(_multiply(s, _BASE), _add(point_r, _multiply(h, point_a)))
