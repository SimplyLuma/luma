"""Explicit cloud rendezvous enrollment for an already trusted native peer.

Cloud approval grants no native capability and never replaces a certificate,
pair epoch, or journal. Both devices must invoke approval using their own
current account credential. Inner TLS remains the device-key proof.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import stat
import time
import uuid

from .account import AccountError
from .policy import Journal
from .relay_binding import RelayBinding, load_bindings
from .transport import fingerprint, _unique


def _uuid(value):
    try:
        if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise AccountError('relay_enrollment_invalid')
    return value


def _save(path,value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise AccountError('relay_enrollment_private_directory_required')
    temporary=path.parent/('.enrollment-'+secrets.token_hex(16))
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:temporary.unlink(missing_ok=True)


def registered_relay_account(directory,registration,model,bindings,current):
    """A persisted relay route requires the exact current native registration."""
    try:
        if not current or not bindings:return None
        saved=registration._load()
        if not saved:return None
        identity=model.snapshot().get('identity') or {}
        expected={k:identity.get(k) for k in ('issuer','subject','account_id','session_id')}
        if any(not isinstance(v,str) or not v for v in expected.values()):return None
        expected['certificate_sha256']=fingerprint((Path(directory)/'device.pem').read_text())
        if saved.get('binding')!=expected or expected['account_id']!=current:return None
        own=_uuid(saved.get('device_id'))
        if any(b.device_id!=own or b.account!=current for b in bindings):return None
        return current
    except Exception:
        return None  # Corrupt/missing receipt must not escape recovery callbacks.


class RelayEnrollment:
    def __init__(self,directory,registration,model,bindings_path,*,enabled,now=time.time):
        self.directory=Path(directory);self.registration=registration;self.model=model
        self.path=Path(bindings_path);self.enabled=enabled;self.now=now
        self.attempt_path=self.directory/'relay-enrollment-attempt.json'

    def _context(self,peer,role):
        if role not in ('requester','receiver') or not self.enabled():
            raise AccountError('relay_enrollment_unavailable')
        token=self.model.authority_token()
        identity=self.model.snapshot().get('identity') or {}
        saved=self.registration._load()
        expected={k:identity.get(k) for k in ('issuer','subject','account_id','session_id')}
        expected['certificate_sha256']=fingerprint((self.directory/'device.pem').read_text())
        if not saved or saved.get('binding')!=expected:
            raise AccountError('device_registration_recovery_required')
        own=_uuid(saved.get('device_id'))
        journal=Journal(self.directory/'continuity.db')
        try:
            row=journal.db.execute('SELECT account,epoch,grants,outgoing_grants,revoked FROM peers WHERE fingerprint=?',(peer,)).fetchone()
            if (not row or row[4] or row[0]!=expected['account_id']
                    or 'messages.read' not in json.loads(row[3] if role=='requester' else row[2])):
                raise AccountError('relay_enrollment_native_consent_required')
            epoch=row[1]
        finally:journal.close()
        return token,(expected,own,peer,epoch,role)

    def enroll(self,peer,peer_device_id,role):
        """Called only by explicit local consent; repeat to finish peer approval."""
        other=_uuid(peer_device_id)
        _,context=self._context(peer,role)
        identity,own,_,epoch,_=context
        if own==other:raise AccountError('relay_enrollment_invalid')
        def request(method,path,body=None):
            token,current=self._context(peer,role)
            if current!=context:raise AccountError('relay_enrollment_identity_changed')
            result=self.model.api.request(method,path,token,body)
            if self._context(peer,role)[1]!=context:
                raise AccountError('relay_enrollment_identity_changed')
            return result
        devices=request('GET','/v1/devices').get('items',[])
        for identifier,pin in ((own,identity['certificate_sha256']),(other,peer)):
            matches=[d for d in devices if d.get('id')==identifier]
            if len(matches)!=1 or matches[0].get('revoked') is not False or matches[0].get('certificate_sha256')!=pin:
                raise AccountError('relay_enrollment_pin_mismatch')
        def matching():
            rows=request('GET','/v1/pairings').get('items',[])
            if not isinstance(rows,list) or len(rows)>=100:
                raise AccountError('relay_enrollment_lookup_incomplete')
            return [p for p in rows if {p.get('left'),p.get('right')}=={own,other}
                    and p.get('revoked') is False and
                    ((p.get('left_approved') is True and p.get('right_approved') is True)
                     or p.get('expires',0)>self.now())]
        pairs=matching()
        if len(pairs)>1:raise AccountError('relay_enrollment_ambiguous')
        if not pairs:
            if self.attempt_path.exists():
                info=self.attempt_path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>4096:
                    raise AccountError('relay_enrollment_invalid')
                attempt=json.loads(self.attempt_path.read_text(),object_pairs_hook=_unique)
                if self.now()-attempt['at']<300:raise AccountError('relay_enrollment_retry_later')
            _save(self.attempt_path,{'at':self.now(),'device_id':own,'peer_device_id':other})
            created=request('POST','/v1/pairings',{'device_id':own,'peer_device_id':other})
            if (created.get('fingerprints')!={own:identity['certificate_sha256'],other:peer}
                    or {created.get('left'),created.get('right')}!={own,other}):
                raise AccountError('relay_enrollment_pin_mismatch')
            pairs=matching()
            if len(pairs)!=1 or pairs[0].get('id')!=created.get('id'):
                raise AccountError('relay_enrollment_ambiguous')
        pair=pairs[0];pair_id=_uuid(pair.get('id'))
        side='left' if pair['left']==own else 'right'
        if pair.get(side+'_approved') is not True:
            request('POST','/v1/pairings/'+pair_id+'/approval',
                    {'device_id':own,'peer_certificate_sha256':peer,'approved':True})
            pairs=matching()
            if len(pairs)!=1 or pairs[0].get('id')!=pair_id:
                raise AccountError('relay_enrollment_ambiguous')
            pair=pairs[0]
        if not (pair.get('left_approved') is True and pair.get('right_approved') is True):
            return {'status':'awaiting_peer','pair_id':pair_id}
        binding=RelayBinding(identity['account_id'],peer,epoch,own,pair_id,role)
        bindings=load_bindings(self.path)
        previous=[b for b in bindings if b.peer==peer]
        if previous and previous!=[binding]:raise AccountError('relay_enrollment_existing_binding_conflict')
        if any(b.account!=binding.account or b.device_id!=own for b in bindings):
            raise AccountError('relay_enrollment_existing_binding_conflict')
        if self._context(peer,role)[1]!=context:raise AccountError('relay_enrollment_identity_changed')
        if not previous:_save(self.path,{'version':1,'bindings':[asdict(b) for b in bindings+[binding]]})
        return {'status':'enrolled','pair_id':pair_id}
