"""Native account registration of an existing locally paired identity.

Registration reports this device, never approves a cloud pair or grants access.
The server binds a row to the OIDC session; a changed session requires deliberate
recovery instead of silently creating duplicate device rows.
"""
import json
import math
import os
from pathlib import Path
import secrets
import stat
import time
import uuid
from .account import AccountError
from .policy import Journal
from .transport import fingerprint,_unique


class DeviceRegistration:
    def __init__(self,directory,model,*,enabled,now=time.time):
        self.directory=Path(directory);self.model=model;self.enabled=enabled;self.now=now
        self.path=self.directory/'cloud-device.json'

    def _load(self):
        try:info=self.path.lstat()
        except FileNotFoundError:return None
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or info.st_mode&0o077 or info.st_size>4096):raise AccountError('device_registration_invalid')
        value=json.loads(self.path.read_text(),object_pairs_hook=_unique)
        if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1:
            raise AccountError('device_registration_invalid')
        return value

    def _save(self,value):
        info=self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise AccountError('device_registration_invalid')
        temporary=self.directory/('.registration-'+secrets.token_hex(16))
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump(value,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
            fd=os.open(self.directory,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:temporary.unlink(missing_ok=True)

    def ensure(self,name):
        if not self.enabled():return {'status':'disabled'}
        if not (self.directory/'device.pem').is_file():return {'status':'unavailable'}
        if not isinstance(name,str) or not 0<len(name)<=80 or any(ord(c)<32 for c in name):
            raise AccountError('device_name_invalid')
        bearer=self.model.authority_token()
        identity=self.model.snapshot().get('identity') or {}
        binding={k:identity.get(k) for k in ('issuer','subject','account_id','session_id')}
        if any(not isinstance(v,str) or not v for v in binding.values()):raise AccountError('account_unavailable')
        pin=fingerprint((self.directory/'device.pem').read_text())
        binding['certificate_sha256']=pin
        journal=Journal(self.directory/'continuity.db')
        try:
            accounts={r[0] for r in journal.db.execute('SELECT account FROM peers WHERE revoked=0')}
            if accounts and accounts!={identity['account_id']}:raise AccountError('device_registration_account_mismatch')
        finally:journal.close()
        saved=self._load()
        if saved:
            if saved.get('binding')!=binding:raise AccountError('device_registration_recovery_required')
            if saved.get('device_id'):
                return {'status':'registered','device_id':saved['device_id']}
            attempted=saved.get('attempted_at')
            if type(attempted) not in (int,float) or not math.isfinite(attempted):raise AccountError('device_registration_invalid')
            if self.now()-attempted<900:return {'status':'retry_later'}
        pending={'version':1,'binding':binding,'attempted_at':self.now()}
        self._save(pending)  # bound retries across process restart and lost replies
        if not self.enabled():return {'status':'disabled'}
        # Obtain authority again after filesystem work; pending OAuth credentials
        # may only be consumed by the verification path.
        bearer=self.model.authority_token()
        if any(self.model.snapshot().get('identity',{}).get(k)!=v for k,v in binding.items() if k!='certificate_sha256'):
            raise AccountError('account_unavailable')
        result=self.model.api.request('POST','/v1/devices',bearer,
            {'name':name,'certificate_sha256':pin})
        identifier=result.get('id')
        try:valid=isinstance(identifier,str) and str(uuid.UUID(identifier))==identifier
        except ValueError:valid=False
        if (not valid or result.get('certificate_sha256')!=pin or result.get('revoked') is not False):
            raise AccountError('device_registration_invalid_receipt')
        # Preserve the receipt even if authority was lost in flight. It records
        # the original binding only; it conveys no transport permission.
        self._save(dict(pending,device_id=identifier))
        if not self.enabled():return {'status':'disabled'}
        return {'status':'registered','device_id':identifier}
