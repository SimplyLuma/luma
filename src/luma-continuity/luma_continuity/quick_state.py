"""Persistent user intent, separate from identity and peer connectivity."""
import json
import os
from pathlib import Path
import secrets
import stat


class LinkIntent:
    def __init__(self, path):self.path=Path(path)

    def load(self):
        try:info=self.path.lstat()
        except FileNotFoundError:return True  # preserve existing explicit grants
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or info.st_mode&0o077 or info.st_size>128):
            raise PermissionError('private link intent required')
        value=json.loads(self.path.read_text())
        if type(value) is not bool:raise ValueError('invalid link intent')
        return value

    def save(self, enabled):
        if type(enabled) is not bool:raise ValueError('boolean required')
        parent=self.path.parent
        parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise PermissionError('private link directory required')
        temporary=parent/('.link-'+secrets.token_hex(16))
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump(enabled,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
            fd=os.open(parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:temporary.unlink(missing_ok=True)


def quick_state(state, enabled):
    if not enabled:status='disabled'
    elif state.get('service_status')=='unconfigured':status='unconfigured'
    elif state.get('account_status')=='locked':status='locked'
    elif state.get('busy'):status='connecting'
    elif (state.get('account_status')!='signed_in' or state.get('stale')
          or state.get('service_status')!='ready'):status='offline'
    else:status='ready'
    # No maintained authenticated peer-liveness observation exists yet.
    return dict(schema_version=1,enabled=enabled,status=status,connected_peers=0)
