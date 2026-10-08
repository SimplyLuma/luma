"""Device certificate rotation for companion pairings (`device.rotate`, ADR-021).

Device certificates expire (the desktop's after 30 days, a phone's after at most
398). Rotation replaces a certificate without pairing again:

1. The rotating device creates a new key and certificate.
2. Over the existing mutual TLS link, still authenticated by the old certificate, it
   calls `device.rotate` with the new certificate and a proof: an ECDSA signature by
   the NEW key over `luma-rotate/1|<old pin>|<new pin>|<receiver pin>|<epoch>`.
3. The receiver checks the certificate rules, the proof and that the old pin is the
   authenticated caller, then moves the peer to the new pin keeping epoch, account and
   grants. The old pin is refused from then on.

The request proves the old key (TLS) and the new key (proof), and binds both to this
pairing and this receiver, so a captured proof cannot be replayed elsewhere.

`device.rotate` is granted in both directions for every companion pairing: without it a
pairing would silently die at certificate expiry.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from . import transport
from .pairing import _certificate, _persist_certificate
from .policy import DIGEST, IDENTIFIER, Denied, Journal

ROTATE = 'device.rotate'
PROOF_PREFIX = 'luma-rotate/1'


def proof_message(old_pin, new_pin, receiver_pin, epoch):
    for value in (old_pin, new_pin, receiver_pin):
        if not isinstance(value, str) or not DIGEST.fullmatch(value):
            raise ValueError('invalid pin')
    if not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch):
        raise ValueError('invalid epoch')
    return f'{PROOF_PREFIX}|{old_pin}|{new_pin}|{receiver_pin}|{epoch}'.encode('ascii')


def sign_proof(key_path, message):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError('device keys are elliptic-curve keys')
    return base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode('ascii')


def verify_proof(certificate_pem, message, proof):
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    if not isinstance(proof, str) or len(proof) > 512:
        raise ValueError('invalid rotation proof')
    signature = base64.b64decode(proof, validate=True)
    key = x509.load_pem_x509_certificate(certificate_pem.encode('ascii')).public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError('device keys are elliptic-curve keys')
    try:
        key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise ValueError('rotation proof does not match the new certificate') from None


class RotationAdapters:
    """Receiver side. Validation happens during dispatch; the journal change runs in
    `after_request`, outside dispatch's write transaction (see CompanionListener)."""

    def __init__(self, directory, *, now=time.time, changed=None):
        self.directory, self.now = Path(directory), now
        self.changed = changed or (lambda *_: None)
        self._pending = {}
        self._lock = threading.Lock()

    def for_peer(self, peer):
        return {ROTATE: lambda capability, payload: self.stage(peer, payload)}

    def stage(self, peer, payload):
        if set(payload) != {'certificate', 'pin', 'proof'}:
            raise ValueError('invalid rotation')
        certificate, pin = payload['certificate'], payload['pin']
        _certificate(certificate, pin, self.now())
        if pin == peer:
            raise ValueError('rotation must change the certificate')
        journal = Journal(self.directory / 'continuity.db')
        try:
            row = journal.db.execute('SELECT epoch,revoked FROM peers WHERE fingerprint=?', (peer,)).fetchone()
        finally:
            journal.close()
        if not row or row[1]:
            raise PermissionError('device removed')
        receiver = transport.fingerprint((self.directory / 'device.pem').read_text())
        verify_proof(certificate, proof_message(peer, pin, receiver, row[0]), payload['proof'])
        with self._lock:
            self._pending[peer] = (pin, certificate)
        return {'accepted': True, 'pin': pin}

    def after_request(self, peer):
        with self._lock:
            pending = self._pending.pop(peer, None)
        if pending is None:
            return
        pin, certificate = pending
        _persist_certificate(self.directory, pin, certificate)
        journal = Journal(self.directory / 'continuity.db')
        try:
            journal.rotate(peer, pin)
        finally:
            journal.close()
        (self.directory / 'peers' / (peer + '.pem')).unlink(missing_ok=True)
        self.changed('rotated', pin)


def needs_rotation(directory, *, days=10, now=None):
    """True when this device's certificate expires within `days`."""
    from cryptography import x509
    cert = x509.load_pem_x509_certificate((Path(directory) / 'device.pem').read_bytes())
    moment = time.time() if now is None else now
    return cert.not_valid_after_utc.timestamp() - moment < days * 86400


def rotate_identity(directory, peers, call, *, days=30):
    """Rotates this device's identity and tells each companion in `peers`.

    `peers` maps fingerprint -> epoch. `call(fingerprint, capability, payload)` performs a
    journaled request using the CURRENT identity. If no peer accepts, nothing changes and the
    owner can retry later. If some accept, the new identity is installed (those peers already
    refuse the old pin) and the result lists the peers that must pair again.
    Luma-to-Luma pairings are not rotated here; they still re-pair (ADR-021 gate).
    """
    directory = Path(directory)
    staging = Path(tempfile.mkdtemp(prefix='.rotate-', dir=directory))
    try:
        new_dir = staging / 'identity'
        from .bootstrap import create_identity
        create_identity(new_dir, days=days)
        new_pem = (new_dir / 'device.pem').read_text()
        new_pin = transport.fingerprint(new_pem)
        old_pin = transport.fingerprint((directory / 'device.pem').read_text())
        failures = {}
        for fingerprint, epoch in sorted(peers.items()):
            message = proof_message(old_pin, new_pin, fingerprint, epoch)
            payload = {'certificate': new_pem, 'pin': new_pin, 'proof': sign_proof(new_dir / 'device.key', message)}
            try:
                receipt = call(fingerprint, ROTATE, payload)
                if receipt.get('state') != 'complete' or (receipt.get('result') or {}).get('pin') != new_pin:
                    failures[fingerprint] = 'rejected'
            except (OSError, ValueError, Denied, EOFError) as error:
                failures[fingerprint] = type(error).__name__
        if failures and len(failures) == len(peers):
            return {'rotated': False, 'pin': old_pin, 'failures': failures}
        # Keep the previous identity beside the new one for diagnosis; it is never loaded again.
        for name in ('device.pem', 'device.key'):
            previous = directory / f'{name}.previous'
            previous.unlink(missing_ok=True)
            os.replace(directory / name, previous)
            os.replace(new_dir / name, directory / name)
            os.chmod(directory / name, 0o600)
        return {'rotated': True, 'pin': new_pin, 'failures': failures}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
