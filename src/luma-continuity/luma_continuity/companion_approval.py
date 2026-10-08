"""Approve with your phone (ADR-021 companion profile, `auth.request`/`auth.response`).

A desktop component asks a paired Android phone to confirm something:

    future = broker.request(fingerprint, 'Install 3 updates', 'Software', timeout=60)
    approved = future.result()

The broker generates a 32-byte challenge, calls `auth.request` on the phone and
waits for the phone to call back with `auth.response`. The phone signs

    luma-approval/1|<desktop pin>|<request>|<challenge as sent>|true|false

with ECDSA P-256/SHA-256 using a separate Android Keystore key
("luma-connect-approval") that needs a strong biometric or the device
credential for every use. The desktop pins that public key per phone on the
first verified approval, stored in a sidecar file owned by this module, and
refuses a different key afterwards until the person trusts it again
(`ApprovalKeys.forget`). Keystore invalidates the key when a new fingerprint is
enrolled, so a key change is expected after enrolment and is surfaced rather
than silently accepted.

Every request expires after at most 60 seconds. A response is accepted once:
late, replayed, cross-phone or mis-bound responses never resolve a request.
An unsigned denial resolves the request as not approved; an approval always
needs a valid signature.

Scope: this module is an API for Luma Connect itself. Using it as a polkit
authentication agent, or for any system authorization, is out of scope. That
would move a trust decision for privileged actions onto a phone and is a Class D
change (docs/development/ai-contributor-contract.md) that needs its own accepted
decision, threat model and independent review first.
"""
from __future__ import annotations

import base64
from concurrent.futures import Future
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
import unicodedata

from . import companion, transport
from .policy import DIGEST, IDENTIFIER

DOMAIN = 'luma-approval/1'
MAX_LIFETIME = 60
MAX_REASON = 200
MAX_APP = 64
CHALLENGE_BYTES = 32
MAX_SIGNATURE = 80  # DER ECDSA P-256 is at most 72 bytes
KEYS_FILE = 'companion-approval-keys.json'


class ApprovalKeyChanged(PermissionError):
    """The phone signed with a different approval key than the one pinned for it."""


class ApprovalUnavailable(Exception):
    """The phone could not be asked (unreachable, not granted, or paused on the phone)."""


def message(desktop_pin, request, challenge, approved):
    """Bytes the phone signs. Identical to the Kotlin `Approval.message`."""
    if (not isinstance(desktop_pin, str) or not DIGEST.fullmatch(desktop_pin)
            or not isinstance(request, str) or not IDENTIFIER.fullmatch(request)
            or not canonical_challenge(challenge) or type(approved) is not bool):
        raise ValueError('invalid approval binding')
    return f'{DOMAIN}|{desktop_pin}|{request}|{challenge}|{"true" if approved else "false"}'.encode()


def canonical_challenge(value):
    if not isinstance(value, str): return False
    try: raw = base64.b64decode(value, validate=True)
    except ValueError: return False
    return len(raw) == CHALLENGE_BYTES and base64.b64encode(raw).decode() == value


def displayable(value, limit):
    """Same rule as Kotlin `Approval.displayable`: one readable line, no C* code points."""
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and all(unicodedata.category(character)[0] != 'C' for character in value))


def _strict_b64(value, limit):
    if not isinstance(value, str) or len(value) > limit * 4 // 3 + 4: raise ValueError('invalid base64')
    raw = base64.b64decode(value, validate=True)
    if len(raw) > limit or base64.b64encode(raw).decode() != value: raise ValueError('invalid base64')
    return raw


def load_public_key(spki):
    """A P-256 SubjectPublicKeyInfo, or ValueError."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = serialization.load_der_public_key(spki)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('approval key must be P-256')
    return key


def verify(spki, data, signature):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    try:
        load_public_key(spki).verify(signature, data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


class ApprovalKeys:
    """Pinned approval public keys per phone fingerprint, in a 0600 sidecar beside the journal.

    Kept out of `continuity.db` because `auth.response` is handled inside the
    journal's dispatch transaction, where adapters must not write the database.
    """

    def __init__(self, directory):
        self.path = Path(directory) / KEYS_FILE
        self._lock = threading.Lock()

    def _load(self):
        try: data = json.loads(self.path.read_text())
        except FileNotFoundError: return {}
        if not isinstance(data, dict): raise ValueError('corrupt approval key store')
        return {fingerprint: key for fingerprint, key in data.items()
                if isinstance(fingerprint, str) and DIGEST.fullmatch(fingerprint) and isinstance(key, str)}

    def _store(self, data):
        temporary = self.path.with_name('.' + KEYS_FILE + '-' + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(data, sort_keys=True)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def get(self, fingerprint):
        with self._lock: return self._load().get(fingerprint)

    def pin(self, fingerprint, public_key):
        """Pins `public_key` if nothing is pinned. Returns the key now pinned."""
        with self._lock:
            data = self._load()
            if fingerprint not in data:
                data[fingerprint] = public_key
                self._store(data)
            return data[fingerprint]

    def forget(self, fingerprint):
        """The person trusts the phone's next approval key (after re-enrolment, or removing the phone)."""
        with self._lock:
            data = self._load()
            if data.pop(fingerprint, None) is not None: self._store(data)


def _thread(work):
    threading.Thread(target=work, name='connect-approval-request', daemon=True).start()


class ApprovalBroker:
    """Pending approvals for every paired phone.

    `call(directory, fingerprint, capability, payload, lifetime=)` reaches the
    phone (`companion.call`), `submit(callable)` runs it off the caller's thread,
    `now()` is wall-clock seconds. With `timer=False` expiry happens only in
    `sweep()` and when a response arrives, which keeps tests deterministic.
    """

    def __init__(self, directory, *, call=companion.call, submit=_thread, now=time.time, timer=True, keys=None):
        self.directory, self.call, self.submit, self.now, self.timer = Path(directory), call, submit, now, timer
        self.keys = keys or ApprovalKeys(directory)
        self._pending = {}
        self._lock = threading.Lock()

    def request(self, fingerprint, reason, app, timeout=MAX_LIFETIME):
        if not isinstance(fingerprint, str) or not DIGEST.fullmatch(fingerprint): raise ValueError('invalid device')
        if not displayable(reason, MAX_REASON) or not displayable(app, MAX_APP): raise ValueError('invalid approval text')
        if type(timeout) not in (int, float) or not 0 < timeout <= MAX_LIFETIME: raise ValueError('timeout must be 1 to 60 seconds')
        lifetime = max(1, int(timeout))
        desktop_pin = transport.fingerprint((self.directory / 'device.pem').read_text())
        request, challenge = secrets.token_hex(16), base64.b64encode(secrets.token_bytes(CHALLENGE_BYTES)).decode()
        expires = int(self.now()) + lifetime
        future = Future()
        future.set_running_or_notify_cancel()
        entry = {'fingerprint': fingerprint, 'pin': desktop_pin, 'challenge': challenge, 'expires': expires,
                 'future': future, 'timer': None}
        with self._lock:
            self.sweep_locked()
            self._pending[request] = entry
            if self.timer:
                entry['timer'] = threading.Timer(lifetime, self._expire, args=(request,))
                entry['timer'].daemon = True
                entry['timer'].start()
        payload = {'request': request, 'reason': reason, 'app': app, 'challenge': challenge, 'expires': expires}

        def work():
            try:
                receipt = self.call(self.directory, fingerprint, 'auth.request', payload, lifetime=lifetime)
            except Exception as error:  # never surface transport or phone error text
                self._fail(request, ApprovalUnavailable(type(error).__name__)); return
            result = receipt.get('result') if isinstance(receipt, dict) else None
            if receipt.get('state') != 'complete' or result != {'error': 'needs-user'}:
                reason_code = result.get('error') if isinstance(result, dict) else receipt.get('state')
                self._fail(request, ApprovalUnavailable(str(reason_code)))
        self.submit(work)
        return future

    # ---- resolution
    def _take_locked(self, request):
        entry = self._pending.pop(request, None)
        if entry and entry['timer']: entry['timer'].cancel()
        return entry

    def _fail(self, request, error):
        with self._lock: entry = self._take_locked(request)
        if entry: entry['future'].set_exception(error)

    def _expire(self, request):
        with self._lock: entry = self._take_locked(request)
        if entry: entry['future'].set_result(False)

    def sweep_locked(self):
        moment = self.now()
        expired = [self._take_locked(request) for request, entry in list(self._pending.items()) if moment >= entry['expires']]
        for entry in expired: entry['future'].set_result(False)

    def sweep(self):
        with self._lock: self.sweep_locked()

    def pending(self):
        with self._lock: return len(self._pending)

    def respond(self, peer, payload):
        """`auth.response` adapter. Raises ValueError for malformed payloads (invalid-request)."""
        if set(payload) != {'request', 'approved', 'signature', 'public_key'}: raise ValueError('invalid approval response')
        request, approved, signature, public_key = (payload['request'], payload['approved'], payload['signature'],
                                                    payload['public_key'])
        if not isinstance(request, str) or not IDENTIFIER.fullmatch(request) or type(approved) is not bool:
            raise ValueError('invalid approval response')
        spki = _strict_b64(public_key, 512)
        load_public_key(spki)
        raw_signature = None if signature is None else _strict_b64(signature, MAX_SIGNATURE)
        if approved and raw_signature is None: raise ValueError('an approval must be signed')

        with self._lock:
            entry = self._pending.get(request)
            # Unknown, already answered, or another phone's request: nothing to resolve.
            if entry is None or not hmac.compare_digest(entry['fingerprint'], peer):
                return {'error': 'expired'}
            self._take_locked(request)
        future = entry['future']
        if self.now() >= entry['expires']:
            future.set_result(False)
            return {'error': 'expired'}
        if raw_signature is None:  # an unsigned denial needs no proof of presence
            future.set_result(False)
            return {'accepted': True}
        pinned = self.keys.get(peer)
        if pinned is not None and not hmac.compare_digest(pinned, public_key):
            future.set_exception(ApprovalKeyChanged('the phone presented a different approval key'))
            return {'error': 'key-changed'}
        if not verify(spki, message(entry['pin'], request, entry['challenge'], approved), raw_signature):
            future.set_result(False)
            raise ValueError('approval signature does not verify')
        if pinned is None and self.keys.pin(peer, public_key) != public_key:
            future.set_exception(ApprovalKeyChanged('the phone presented a different approval key'))
            return {'error': 'key-changed'}
        future.set_result(approved)
        return {'accepted': True}

    def forget(self, fingerprint):
        self.keys.forget(fingerprint)

    def close(self):
        with self._lock:
            entries = [self._take_locked(request) for request in list(self._pending)]
        for entry in entries: entry['future'].set_result(False)
