"""Local, explicitly verified message-device invitations. No automatic trust.

Public files are proposals, not authentication. Users compare the full pin on
the other device; mutual TLS subsequently proves possession of each private key.
A is the inviting device, B the accepting device. a_to_b allows A to call B.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from .policy import Journal, DIGEST, IDENTIFIER
from .transport import encode, fingerprint, _unique

SUPPORTED = frozenset({'messages.read', 'messages.send'})
SCHEMA = 'org.projectluma.message-pairing/v1'
COMMON = {'schema', 'kind', 'offer_id', 'epoch', 'created_at', 'expires_at',
          'account', 'a_pin', 'a_to_b', 'b_to_a'}


def _scopes(value):
    if (not isinstance(value, list) or any(not isinstance(item, str) for item in value)
            or len(value) != len(set(value)) or not set(value) <= SUPPORTED):
        raise ValueError('unsupported or duplicate message permissions')
    return sorted(value)


def _certificate(pem, pin, now):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec, rsa, ed25519, ed448
    from cryptography.x509.oid import ExtendedKeyUsageOID
    if (not isinstance(pem, str) or len(pem) > 16384
            or not re.fullmatch(r'-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\n?', pem)
            or not isinstance(pin, str) or not DIGEST.fullmatch(pin)
            or not hmac.compare_digest(fingerprint(pem), pin)):
        raise ValueError('invalid device certificate or pin')
    cert = x509.load_pem_x509_certificate(pem.encode('ascii'))
    if cert.not_valid_before_utc.timestamp() > now + 30 or cert.not_valid_after_utc.timestamp() <= now:
        raise ValueError('device certificate is not currently valid')
    if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError('device leaf certificate required')
    if not cert.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature:
        raise ValueError('device signing key required')
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    if not {ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH} <= set(eku):
        raise ValueError('mutual TLS device certificate required')
    key = cert.public_key()
    if not ((isinstance(key, ec.EllipticCurvePublicKey) and key.key_size >= 256)
            or (isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048)
            or isinstance(key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey))):
        raise ValueError('unsupported device key')


def _parse(data, kind, now):
    if not isinstance(data, bytes) or not 0 < len(data) <= 32768:
        raise ValueError('invalid pairing document size')
    doc = json.loads(data, object_pairs_hook=_unique)
    extra = {'a_certificate'} if kind == 'offer' else {'offer_digest', 'b_pin', 'b_certificate'}
    if not isinstance(doc, dict) or set(doc) != COMMON | extra or doc['schema'] != SCHEMA or doc['kind'] != kind:
        raise ValueError('unsupported pairing document')
    for field in ('offer_id', 'epoch'):
        if not isinstance(doc[field], str) or not IDENTIFIER.fullmatch(doc[field]): raise ValueError('invalid pairing identity')
    created, expires = doc['created_at'], doc['expires_at']
    if (type(created) is not int or type(expires) is not int or not created < expires <= created + 600
            or created > now + 30 or expires <= now):
        raise ValueError('expired or invalid pairing invitation')
    if doc['account'] is not None and (not isinstance(doc['account'], str) or not 0 < len(doc['account']) <= 512):
        raise ValueError('invalid account binding')
    if not isinstance(doc['a_pin'], str) or not DIGEST.fullmatch(doc['a_pin']): raise ValueError('invalid inviter pin')
    _scopes(doc['a_to_b']); _scopes(doc['b_to_a'])
    if not (doc['a_to_b'] or doc['b_to_a']): raise ValueError('no selected permissions')
    side = 'a' if kind == 'offer' else 'b'
    _certificate(doc[side + '_certificate'], doc[side + '_pin'], now)
    return doc


def _journal(directory):
    journal = Journal(Path(directory) / 'continuity.db')
    journal.db.executescript('''
      CREATE TABLE IF NOT EXISTS local_offers(id TEXT PRIMARY KEY, digest TEXT NOT NULL, document BLOB NOT NULL, consumed INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS accepted_offers(id TEXT PRIMARY KEY, digest TEXT NOT NULL);
    ''')
    return journal


def _persist_certificate(directory, pin, pem):
    peers = Path(directory) / 'peers'
    peers.mkdir(mode=0o700, exist_ok=True)
    if peers.is_symlink() or peers.stat().st_mode & 0o077: raise PermissionError('private peer directory required')
    target = peers / (pin + '.pem')
    if target.exists() or target.is_symlink():
        if target.is_symlink() or fingerprint(target.read_text()) != pin: raise PermissionError('invalid existing peer certificate')
        return
    temporary = peers / ('.pending-' + secrets.token_hex(16))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(pem); file.flush(); os.fsync(file.fileno())
        os.replace(temporary, target)
        fd = os.open(peers, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def _approve(journal, directory, peer, pem, doc, incoming, outgoing, replace):
    old = journal.db.execute('SELECT account FROM peers WHERE fingerprint=?', (peer,)).fetchone()
    if old and (replace is not True or old[0] != doc['account']):
        raise PermissionError('explicit replacement with the same account binding required')
    # Validate/mutate only inside a transaction; a certificate write failure
    # rolls back permission and invitation consumption. An orphan public cert
    # after a later DB failure grants nothing by itself.
    journal._approve_locked(peer, doc['epoch'], incoming, account=doc['account'], outgoing_grants=outgoing)
    _persist_certificate(directory, peer, pem)


def create_offer(directory, *, a_to_b, b_to_a, account=None, now=None):
    now = int(time.time()) if now is None else now
    certificate = (Path(directory) / 'device.pem').read_text()
    doc = dict(schema=SCHEMA, kind='offer', offer_id=secrets.token_hex(16), epoch=secrets.token_hex(16),
               created_at=now, expires_at=now + 600, account=account, a_pin=fingerprint(certificate),
               a_certificate=certificate, a_to_b=_scopes(a_to_b), b_to_a=_scopes(b_to_a))
    data = encode(doc); _parse(data, 'offer', now)
    journal = _journal(directory)
    try:
        if journal.db.execute('SELECT count(*) FROM local_offers').fetchone()[0] >= 1000:
            raise ValueError('local invitation limit reached')
        journal.db.execute('INSERT INTO local_offers VALUES(?,?,?,0)',
                           (doc['offer_id'], hashlib.sha256(data).hexdigest(), data))
    finally: journal.close()
    return data


def accept_offer(directory, data, *, verified_a_pin, a_to_b, b_to_a, account=None, replace=False, now=None):
    clock = time.time if now is None else lambda: now
    offer = _parse(data, 'offer', clock())
    if (not isinstance(verified_a_pin, str) or not hmac.compare_digest(verified_a_pin, offer['a_pin'])
            or account != offer['account']): raise PermissionError('independent pin or account confirmation failed')
    selected_a, selected_b = _scopes(a_to_b), _scopes(b_to_a)
    if not set(selected_a) <= set(offer['a_to_b']) or not set(selected_b) <= set(offer['b_to_a']):
        raise PermissionError('permissions exceed invitation')
    pem = (Path(directory) / 'device.pem').read_text()
    if fingerprint(pem) == offer['a_pin']: raise PermissionError('cannot pair a device with itself')
    response = {key: offer[key] for key in COMMON}
    response.update(kind='response', offer_digest=hashlib.sha256(encode(offer)).hexdigest(),
                    b_pin=fingerprint(pem), b_certificate=pem, a_to_b=selected_a, b_to_a=selected_b)
    encoded = encode(response); _parse(encoded, 'response', clock())
    journal = _journal(directory)
    try:
        journal.db.execute('BEGIN IMMEDIATE')
        _parse(data, 'offer', clock())
        journal.db.execute('INSERT INTO accepted_offers VALUES(?,?)', (offer['offer_id'], response['offer_digest']))
        _approve(journal, directory, offer['a_pin'], offer['a_certificate'], offer, selected_a, selected_b, replace)
        _parse(data, 'offer', clock())
        journal.db.execute('COMMIT')
    finally:
        if journal.db.in_transaction: journal.db.execute('ROLLBACK')
        journal.close()
    return encoded


def finish_offer(directory, data, *, verified_b_pin, account=None, replace=False, now=None):
    clock = time.time if now is None else lambda: now
    response = _parse(data, 'response', clock())
    if (not isinstance(verified_b_pin, str) or not hmac.compare_digest(verified_b_pin, response['b_pin'])
            or response['account'] != account): raise PermissionError('independent pin or account confirmation failed')
    journal = _journal(directory)
    try:
        journal.db.execute('BEGIN IMMEDIATE')
        _parse(data, 'response', clock())
        row = journal.db.execute('SELECT digest,document,consumed FROM local_offers WHERE id=?', (response['offer_id'],)).fetchone()
        if not row or row[2] or response['offer_digest'] != row[0]: raise PermissionError('invitation absent or already consumed')
        offer = _parse(row[1], 'offer', clock())
        local_pin = fingerprint((Path(directory) / 'device.pem').read_text())
        if local_pin != offer['a_pin'] or response['b_pin'] == local_pin:
            raise PermissionError('local identity changed or self-pairing attempted')
        for key in COMMON - {'kind', 'a_to_b', 'b_to_a'}:
            if response[key] != offer[key]: raise PermissionError('response does not match invitation')
        if not set(response['a_to_b']) <= set(offer['a_to_b']) or not set(response['b_to_a']) <= set(offer['b_to_a']):
            raise PermissionError('response expands permissions')
        _approve(journal, directory, response['b_pin'], response['b_certificate'], response,
                 response['b_to_a'], response['a_to_b'], replace)
        journal.db.execute('UPDATE local_offers SET consumed=1 WHERE id=?', (response['offer_id'],))
        _parse(data, 'response', clock())
        journal.db.execute('COMMIT')
    finally:
        if journal.db.in_transaction: journal.db.execute('ROLLBACK')
        journal.close()
    return {'peer': response['b_pin'], 'epoch': response['epoch'],
            'incoming': response['b_to_a'], 'outgoing': response['a_to_b']}
