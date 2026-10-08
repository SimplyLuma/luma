"""Explicit same-device recovery using its existing private key and fresh login.

The challenge is inert metadata. Only a verified server receipt updates the
registration binding; local pairing permissions and device keys are untouched.
"""
import base64
import json
import math
import os
import stat
import time
import uuid
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from .account import AccountError
from .transport import fingerprint, _unique


def _decode(value, limit):
    if not isinstance(value,str) or len(value)>limit*2:
        raise AccountError('device_recovery_invalid_challenge')
    try:raw=base64.b64decode(value,validate=True)
    except ValueError:raise AccountError('device_recovery_invalid_challenge') from None
    if len(raw)>limit:raise AccountError('device_recovery_invalid_challenge')
    return raw


def recover(registration, *, now=time.time):
    if not registration.enabled():raise AccountError('device_recovery_disabled')
    saved=registration._load()
    if not saved or not saved.get('device_id'):raise AccountError('device_recovery_unregistered')
    identifier=saved['device_id']
    try:valid=str(uuid.UUID(identifier))==identifier
    except (ValueError,TypeError,AttributeError):valid=False
    if not valid:raise AccountError('device_recovery_unregistered')
    model=registration.model
    bearer=model.authority_token()
    identity=model.snapshot().get('identity') or {}
    binding={k:identity.get(k) for k in ('issuer','subject','account_id','session_id')}
    if any(not isinstance(v,str) or not v for v in binding.values()):raise AccountError('account_unavailable')
    old=saved.get('binding') or {}
    if any(binding[k]!=old.get(k) for k in ('issuer','subject','account_id')):
        raise AccountError('device_recovery_account_mismatch')
    cert_path=registration.directory/'device.pem'
    key_path=registration.directory/'device.key'
    def private_read(path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>16384:
                raise AccountError('device_recovery_identity_invalid')
            return stream.read(16385)
    cert_pem=private_read(cert_path)
    pin=fingerprint(cert_pem.decode('ascii'))
    if pin!=old.get('certificate_sha256'):raise AccountError('device_recovery_identity_invalid')
    binding['certificate_sha256']=pin
    cert=x509.load_pem_x509_certificate(cert_pem)
    key=serialization.load_pem_private_key(private_read(key_path),password=None)
    if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):
        raise AccountError('device_recovery_identity_invalid')
    if key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)!=cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo):
        raise AccountError('device_recovery_identity_invalid')
    def current():
        token=model.authority_token()
        if not registration.enabled() or any(model.snapshot().get('identity',{}).get(k)!=v for k,v in binding.items() if k!='certificate_sha256'):
            raise AccountError('account_unavailable')
        return token
    if old==binding:return {'status':'registered','device_id':identifier}
    def accept(result,recovered=False):
        if (recovered and result.get('recovered') is not True or result.get('device_id')!=identifier
            or result.get('account_id')!=binding['account_id'] or result.get('session_id')!=binding['session_id']
            or result.get('certificate_sha256')!=pin):
            raise AccountError('device_recovery_invalid_receipt')
        registration._save(dict(saved,binding=binding))
        current()
        return {'status':'registered','device_id':identifier}
    # This endpoint verifies ownership by the exact current SID; the account's
    # general device listing is deliberately insufficient proof of recovery.
    try:receipt=model.api.request('GET',f'/v1/devices/{identifier}/registration',current())
    except AccountError as error:
        if error.status!=403:raise
    else:return accept(receipt)
    reply=model.api.request('POST',f'/v1/devices/{identifier}/recovery-challenges',current(),{})
    raw=_decode(reply.get('payload_b64'),4096)
    try:payload=json.loads(raw,object_pairs_hook=_unique)
    except (ValueError,UnicodeError):raise AccountError('device_recovery_invalid_challenge') from None
    expected=dict(version=1,purpose='luma-connect-device-recovery',device_id=identifier,
                  account_id=binding['account_id'],issuer=binding['issuer'],session_id=binding['session_id'],certificate_sha256=pin)
    if not isinstance(payload,dict) or set(payload)!=set(expected)|{'challenge_id','nonce','expires'} or any(payload.get(k)!=v for k,v in expected.items()):
        raise AccountError('device_recovery_invalid_challenge')
    expiry=payload['expires'];challenge=payload['challenge_id'];nonce=payload['nonce']
    try:valid_challenge=isinstance(challenge,str) and str(uuid.UUID(challenge))==challenge
    except (ValueError,TypeError,AttributeError):valid_challenge=False
    if (type(payload['version']) is not int or not valid_challenge
        or challenge!=reply.get('challenge_id') or expiry!=reply.get('expires')
        or type(expiry) not in (int,float) or not math.isfinite(expiry) or not now()<expiry<=now()+60
        or not isinstance(nonce,str) or len(nonce)!=64 or any(c not in '0123456789abcdef' for c in nonce)):
        raise AccountError('device_recovery_invalid_challenge')
    bearer=current()
    signature=key.sign(raw,ec.ECDSA(hashes.SHA256()))
    try:
        result=model.api.request('POST',f'/v1/devices/{identifier}/recover',bearer,
            dict(challenge_id=challenge,certificate_der_b64=base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode('ascii'),signature_b64=base64.b64encode(signature).decode('ascii')))
    except (AccountError,OSError) as error:
        if isinstance(error,AccountError) and error.status<500:raise
        # A lost receipt can follow a committed rebind. Read its own-SID proof,
        # without replaying the signed challenge or creating another device.
        return accept(model.api.request('GET',f'/v1/devices/{identifier}/registration',current()))
    return accept(result,recovered=True)
