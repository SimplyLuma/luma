"""Persisted rendezvous retry bounds; never retries a native call operation."""
from contextlib import contextmanager
import fcntl
import threading
import json
import os
from pathlib import Path
import random
import secrets
import stat
import time
from .transport import _unique


class CallRetry:
    LIMIT = 3

    def __init__(self, directory, bindings, *, now=time.time, jitter=random.uniform):
        self.path = Path(directory) / 'call-retry.json'
        self.identity = {b.pair_id: [b.account, b.device_id, b.peer, b.epoch, b.role] for b in bindings}
        self.now, self.jitter = now, jitter
        self.rows = {pair: dict(attempts=0, next_try=0, server_until=0, capacity_until=0) for pair in self.identity}
        self.manual = set()
        self.saved_identity={};self.saved_rows={};self.recoveries={}
        self.lock=threading.RLock()
        self._load()

    def _load(self):
        try:fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
        except FileNotFoundError:return
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                    or info.st_mode&0o077 or info.st_size>16384):
                raise PermissionError('private call retry state required')
            with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(16385)
            if len(raw)>16384:raise ValueError('call retry state bound')
        finally:os.close(fd)
        doc=json.loads(raw,object_pairs_hook=_unique)
        if not isinstance(doc,dict) or type(doc.get('version')) is not int or doc['version'] not in (1,2):
            raise ValueError('invalid call retry state')
        version=doc['version']
        if (set(doc)!=({'version','identity','rows'} if version==1 else {'version','identity','rows','recoveries'})
                or not isinstance(doc['identity'],dict) or not isinstance(doc['rows'],dict)
                or set(doc['identity'])!=set(doc['rows']) or len(doc['rows'])>16):
            raise ValueError('invalid call retry state')
        for pair,row in doc['rows'].items():
            identity=doc['identity'][pair]
            fields={'attempts','next_try','server_until'}|({'capacity_until'} if version==2 else set())
            if (not isinstance(pair,str) or not pair or not isinstance(identity,list) or len(identity)!=5
                    or any(not isinstance(v,str) or not v for v in identity)
                    or not isinstance(row,dict) or set(row)!=fields
                    or any(type(v) is not int or not 0<=v<=2**53 for v in row.values())):
                raise ValueError('invalid call retry bounds')
            if version==1:row['capacity_until']=0  # An untyped old cooldown cannot be overridden.
            if pair in self.rows:
                if identity!=self.identity[pair]:raise PermissionError('call retry identity changed')
                self.rows[pair]=row
        recoveries=doc.get('recoveries',{})
        if not isinstance(recoveries,dict) or len(recoveries)>32:raise ValueError('invalid recovery reservations')
        from .relay_control import identifier
        for key,value in recoveries.items():
            if (not isinstance(key,str) or len(key.split('/'))!=2 or key.split('/')[1] not in ('call-control','audio-signaling')
                    or not isinstance(value,dict) or set(value)!={'pair','expires'}
                    or value['pair'] not in doc['identity'] or type(value['expires']) is not int or not 0<value['expires']<=2**53):
                raise ValueError('invalid recovery reservation')
            identifier(key.split('/')[0])
        self.saved_identity=doc['identity'];self.saved_rows=doc['rows'];self.recoveries=recoveries

    @contextmanager
    def _transaction(self):
        with self.lock:
            fd=os.open(self.path.with_suffix('.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                    raise PermissionError('private retry lock required')
                fcntl.flock(fd,fcntl.LOCK_EX)
                self._load()
                yield
            finally:os.close(fd)

    def _save(self):
        parent = self.path.parent
        info = parent.lstat()
        if parent.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('private call retry directory required')
        temporary = parent / ('.call-retry-' + secrets.token_hex(16))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                identity=self.saved_identity|self.identity;rows=self.saved_rows|self.rows
                if len(rows)>16:raise ValueError('call retry pair bound')
                json.dump(dict(version=2,identity=identity,rows=rows,recoveries=self.recoveries),stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            fd = os.open(parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally: temporary.unlink(missing_ok=True)

    def state(self, pair):
        with self._transaction():return self._state(pair)

    def _state(self, pair):
        row = self.rows[pair]; now = self.now()
        server_until=max(row['server_until'],row['capacity_until'])
        if server_until > now:
            return dict(reason='rate_limited', retry_at=server_until)
        if row['attempts'] >= self.LIMIT and pair not in self.manual:
            return dict(reason='retry_required', retry_at=None)
        deadline = max(row['next_try'], row['server_until'],row['capacity_until'])
        return dict(reason='waiting' if deadline > now else 'available', retry_at=deadline if deadline > now else None)

    def reserve(self, pair):
        with self._transaction():
            if self._state(pair)['reason'] != 'available': return False
            row = self.rows[pair]
            row['attempts'] = min(2**53, row['attempts'] + 1)
            delay = min(300, 15 * 2**min(row['attempts'] - 1, 5)) * self.jitter(.8, 1)
            row['next_try'] = int(self.now() + delay + 1)
            self.manual.discard(pair)
            self._save()  # A crash cannot forget an already issued attempt.
            return True

    def rate_limited(self, pair, delay, code=None):
        if type(delay) is not int or not 1 <= delay <= 172800: delay = 60
        with self._transaction():
            row = self.rows[pair]
            field='capacity_until' if code=='call_attempt_limit' else 'server_until'
            row[field] = max(row[field], int(self.now()) + delay)
            self._save()

    def capacity_restored(self,pair,*,authorized=lambda:True):
        """Called only after current authenticated admission says capacity is available."""
        with self._transaction():
            if not authorized():raise PermissionError('call capacity update cancelled')
            self.rows[pair]['capacity_until']=0
            self._save()

    def cooldowns(self,pair):
        with self._transaction():return {k:self.rows[pair][k] for k in ('server_until','capacity_until')}

    def authenticated(self, pair):
        with self._transaction():
            row = self.rows[pair]
            row.update(attempts=0, next_try=0)
            self.manual.discard(pair)
            self._save()  # Server cooldown survives successful existing channels.

    def retry(self, pair):
        with self.lock:self.manual.add(pair)  # One attempt; never resets count/cooldown.

    def reserve_recovery(self,pair,grant,purpose):
        from .relay_control import identifier
        if (not isinstance(grant,dict) or set(grant)!={'recovery_id','expires','purposes'}
                or type(grant['expires']) is not int or not self.now()<grant['expires']<=self.now()+901
                or not isinstance(grant['purposes'],list) or purpose not in grant['purposes']
                or purpose not in ('call-control','audio-signaling')):
            return False
        key=identifier(grant['recovery_id'])+'/'+purpose
        with self._transaction():
            row=self.rows[pair]
            # Authenticated admission supersedes only typed capacity exhaustion.
            if row['server_until']>self.now() or key in self.recoveries:return False
            if purpose=='call-control' and row['next_try']>self.now():return False
            if len(self.recoveries)>=32:raise PermissionError('recovery reservation bound')
            self.recoveries[key]=dict(pair=pair,expires=grant['expires'])
            if purpose=='call-control':
                row['attempts']=min(2**53,max(self.LIMIT,row['attempts']+1))
                row['next_try']=int(self.now())+16
                self.manual.discard(pair)
            self._save()  # Lost responses never make this grant/purpose retryable.
            return True


def description(state):
    reason=state.get('reason')
    if reason=='rate_limited':
        from datetime import datetime
        try:when=datetime.fromtimestamp(state['retry_at']).strftime('%a %I:%M %p')
        except (KeyError,ValueError,OverflowError,OSError,TypeError):return 'Call connection is temporarily limited. Try again later.'
        return 'Call connection is temporarily limited. Try again after '+when+'.'
    if reason=='retry_required':return 'Could not connect. Check your phone, then choose Retry connection.'
    if reason=='waiting':return 'Waiting before reconnecting to your phone.'
    if reason=='connecting':return 'Connecting to your phone.'
    return 'Not connected'
