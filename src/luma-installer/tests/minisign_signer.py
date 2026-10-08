# SPDX-License-Identifier: Apache-2.0
"""Test-only Ed25519 signing in minisign's format.

Depot only ever verifies. Tests need to produce signed catalogues, so this
signs with the RFC 8032 algorithm using the same curve arithmetic Depot's
fallback verifier uses. It is checked against the RFC 8032 test vector (a
deterministic signature must match byte for byte) and against signatures the
minisign tool made, so a mistake here cannot make a broken verifier pass.

The keys below are throwaway test keys. They sign nothing outside tests.
"""

import base64
import hashlib

from luma_installer import depot_signature as ds

#: RFC 8032, section 7.1, TEST 1 secret key -- public, and used only here.
TEST_SEED = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
TEST_KEY_ID = bytes.fromhex('1122334455667788')


def _compress(point):
    x, y, z, _t = point
    inverse = pow(z, ds._P - 2, ds._P)
    x, y = x * inverse % ds._P, y * inverse % ds._P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')


def public_key(seed=TEST_SEED):
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], 'little')
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return _compress(ds._multiply(scalar, ds._BASE))


def sign(message, seed=TEST_SEED):
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], 'little')
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    prefix = digest[32:]
    public = _compress(ds._multiply(scalar, ds._BASE))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), 'little') % ds._L
    encoded_r = _compress(ds._multiply(r, ds._BASE))
    h = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), 'little') % ds._L
    s = (r + h * scalar) % ds._L
    return encoded_r + s.to_bytes(32, 'little')


def public_key_file(seed=TEST_SEED, key_id=TEST_KEY_ID):
    raw = b'Ed' + key_id + public_key(seed)
    return ('untrusted comment: minisign public key ' + key_id.hex().upper() + '\n'
            + base64.b64encode(raw).decode() + '\n').encode()


def signature_file(message, seed=TEST_SEED, key_id=TEST_KEY_ID, *, prehashed=True,
                   trusted_comment='timestamp:1790000000\tfile:catalog-4.json\thashed'):
    signed = hashlib.blake2b(message, digest_size=64).digest() if prehashed else message
    signature = sign(signed, seed)
    algorithm = b'ED' if prehashed else b'Ed'
    global_signature = sign(signature + trusted_comment.encode(), seed)
    return ('untrusted comment: signature from minisign secret key\n'
            + base64.b64encode(algorithm + key_id + signature).decode() + '\n'
            + 'trusted comment: ' + trusted_comment + '\n'
            + base64.b64encode(global_signature).decode() + '\n').encode()
