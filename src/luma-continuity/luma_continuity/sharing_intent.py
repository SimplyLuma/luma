"""Private explicit sharing intent; it conveys no account or capability grant."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import stat
from .transport import _unique


class SharingIntent:
    def __init__(self,directory,bindings,*,service="messages"):
        if service not in {"messages","calls"}:raise ValueError("invalid sharing service")
        self.path=Path(directory)/('relay-sharing.json' if service=='messages' else 'call-relay-sharing.json')
        self.bindings={b.peer:asdict(b) for b in bindings if b.role=='receiver'}

    def load(self):
        if not self.path.exists():return set()
        info=self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or self.path.is_symlink() or info.st_uid!=os.getuid()
                or info.st_mode&0o077 or info.st_size>16384):raise PermissionError('private sharing intent required')
        document=json.loads(self.path.read_text(),object_pairs_hook=_unique)
        if (not isinstance(document,dict) or set(document)!={'version','bindings'} or type(document['version']) is not int
                or document['version']!=1 or not isinstance(document['bindings'],list) or len(document['bindings'])>16):
            raise ValueError('invalid sharing intent')
        selected=set()
        for binding in document['bindings']:
            if not isinstance(binding,dict):raise ValueError('invalid sharing binding')
            peer=binding.get('peer')
            if peer in selected:raise ValueError('duplicate sharing binding')
            if peer in self.bindings and binding==self.bindings[peer]:selected.add(peer)
        return selected

    def save(self,peers):
        if not set(peers)<=self.bindings.keys():raise PermissionError('explicit receiver binding required')
        parent=self.path.parent;info=parent.lstat()
        if parent.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise PermissionError('private identity directory required')
        temporary=parent/('.sharing-'+secrets.token_hex(16))
        try:
            descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(descriptor,'w') as stream:
                json.dump({'version':1,'bindings':[self.bindings[p] for p in sorted(peers)]},stream)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
            descriptor=os.open(parent,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        finally:temporary.unlink(missing_ok=True)
