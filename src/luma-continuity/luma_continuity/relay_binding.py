"""Explicit relay bindings to existing native trust; never an enrollment API."""
from dataclasses import dataclass
import json
import time
from pathlib import Path
from .policy import DIGEST, IDENTIFIER
from .relay_control import identifier
from .relay_exchange import PairedRelayExchange
from . import transport


def load_bindings(path):
    """Optional explicit private configuration; absent never enrolls a device."""
    import os
    import stat
    path=Path(path)
    if not path.exists():return []
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077
            or path.parent.is_symlink() or path.parent.stat().st_mode&0o077):
        raise PermissionError('private relay binding configuration required')
    with path.open('rb') as stream:raw=stream.read(16385)
    if len(raw)>16384:raise ValueError('relay binding configuration bound')
    document=json.loads(raw,object_pairs_hook=transport._unique)
    if (not isinstance(document,dict) or set(document)!={'version','bindings'} or type(document['version']) is not int or document['version']!=1
            or not isinstance(document['bindings'],list) or not 0<len(document['bindings'])<=16):
        raise ValueError('invalid explicit relay bindings')
    return [RelayBinding(**value) for value in document['bindings']]


@dataclass(frozen=True)
class RelayBinding:
    account: str
    peer: str
    epoch: str
    device_id: str
    pair_id: str
    role: str

    def __post_init__(self):
        if not isinstance(self.account,str) or not 0<len(self.account)<=512:
            raise ValueError('existing account required')
        if not isinstance(self.peer,str) or not DIGEST.fullmatch(self.peer): raise ValueError('invalid peer')
        if not isinstance(self.epoch,str) or not IDENTIFIER.fullmatch(self.epoch): raise ValueError('invalid epoch')
        identifier(self.device_id);identifier(self.pair_id)
        if self.role not in {'requester','receiver'}: raise ValueError('invalid relay role')


class BoundRelayExchange:
    """Daemon endpoint for existing Messages requests and receipts only.

    Caller peer and JSON are requests, never authority. The binding is loaded
    by the daemon and the shared journal boundary rechecks each capability.
    """
    def __init__(self, directory, binding, api, *, account):
        if binding.role!='requester' or api.device_id!=binding.device_id:
            raise PermissionError('invalid requester binding')
        self.directory,self.binding,self.api=Path(directory),binding,api
        self.account=account
        self.session=None
        self.exchange=PairedRelayExchange(directory,binding.peer,binding.epoch,
            websocket_factory=self._open,authorized=lambda:self.account()==binding.account)

    def _open(self):
        session=self.api.ensure(self.binding.pair_id)
        if self.account()!=self.binding.account: raise PermissionError('account changed')
        self.session=session
        return self.api.open(session)

    def call(self, peer, encoded):
        if peer!=self.binding.peer or self.account()!=self.binding.account:
            raise PermissionError('unapproved message peer')
        if not isinstance(encoded,str) or not 0<len(encoded.encode('utf-8'))<=transport.MAX_FRAME:
            raise ValueError('invalid message request size')
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value: raise ValueError('duplicate request field')
                value[key]=item
            return value
        def reject(_value): raise ValueError('invalid request number')
        request=json.loads(encoded,object_pairs_hook=unique,parse_constant=reject)
        if (not isinstance(request,dict) or set(request)!={'version','epoch','id','account','capability','expires','payload'}
                or type(request['version']) is not int or request['version']!=1
                or request['account']!=self.binding.account or request['epoch']!=self.binding.epoch
                or request['capability'] not in {'messages.read','messages.send'}
                or not isinstance(request['id'],str) or not IDENTIFIER.fullmatch(request['id'])
                or type(request['expires']) is not int or not time.time()<request['expires']<=time.time()+86400
                or not isinstance(request['payload'],dict)):
            raise PermissionError('unapproved message operation')
        # No field is rewritten, no new ID is generated, no retry occurs here.
        return self.exchange(request)

    def invalidate(self):
        self.session=None
        self.exchange.invalidate()

    def close(self): self.exchange.close()
