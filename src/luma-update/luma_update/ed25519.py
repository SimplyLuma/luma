# SPDX-License-Identifier: Apache-2.0
"""Ed25519 signature verification in pure Python (RFC 8032, section 5.1).

The update graph is signed with minisign, whose signatures are Ed25519. The
Luma image has no guarantee of shipping a Python cryptography library, so the
agent carries this small, dependency-free verifier. It follows the RFC's
reference algorithm and is strict about encodings:

* a point encoding whose y coordinate is not reduced modulo p is rejected;
* a signature whose S is not below the group order L is rejected
  (malleability, RFC 8032 section 5.1.7 step 1);
* the check is the cofactorless ``[S]B == R + [k]A`` comparison of encoded
  points, as in the RFC's reference implementation.

Verification is not constant time. It handles only public data (keys, messages
and signatures), so that does not leak a secret. ``sign`` exists for the test
suite and for building local test graphs; the device never holds a signing key.
"""

from __future__ import annotations

import hashlib

__all__ = ("verify", "sign", "public_key", "InvalidEncoding")

p = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
d = (-121665 * pow(121666, p - 2, p)) % p
SQRT_M1 = pow(2, (p - 1) // 4, p)

_ZERO = (0, 1, 1, 0)


class InvalidEncoding(ValueError):
    """A key or signature is not a valid Ed25519 encoding."""


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _add(P, Q):
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % p
    b = (y1 + x1) * (y2 + x2) % p
    c = 2 * t1 * t2 * d % p
    dd = 2 * z1 * z2 % p
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % p, g * h % p, f * g % p, e * h % p)


def _mul(s: int, P):
    Q = _ZERO
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _recover_x(y: int, sign: int) -> int | None:
    if y >= p:
        return None
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p) % p
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * SQRT_M1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_GY = 4 * pow(5, p - 2, p) % p
_GX = _recover_x(_GY, 0)
BASE = (_GX, _GY, 1, _GX * _GY % p)


def _compress(P) -> bytes:
    x, y, z, _ = P
    zinv = pow(z, p - 2, p)
    x, y = x * zinv % p, y * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        raise InvalidEncoding("an Ed25519 point is 32 bytes")
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        raise InvalidEncoding("not a point on Ed25519")
    return (x, y, 1, x * y % p)


def _secret_expand(secret: bytes) -> tuple[int, bytes]:
    if len(secret) != 32:
        raise ValueError("an Ed25519 secret key is 32 bytes")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _secret_expand(secret)
    return _compress(_mul(a, BASE))


def sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _secret_expand(secret)
    A = _compress(_mul(a, BASE))
    r = int.from_bytes(_sha512(prefix + message), "little") % L
    R = _compress(_mul(r, BASE))
    k = int.from_bytes(_sha512(R + A + message), "little") % L
    s = (r + k * a) % L
    return R + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Return True only for a valid signature; malformed input is False."""
    if len(public) != 32 or len(signature) != 64:
        return False
    try:
        A = _decompress(public)
        R_bytes = signature[:32]
        _decompress(R_bytes)
    except InvalidEncoding:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(_sha512(R_bytes + public + message), "little") % L
    sB = _mul(s, BASE)
    kA = _mul(k, A)
    # [S]B - [k]A must encode to exactly R.
    neg_kA = ((p - kA[0]) % p, kA[1], kA[2], (p - kA[3]) % p)
    return _compress(_add(sB, neg_kA)) == R_bytes
