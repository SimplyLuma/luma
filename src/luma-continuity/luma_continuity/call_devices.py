"""Daemon-owned call consent, selection, subscription and typed exchange.

Uses existing account-bound pairing grants. Selection is explicit and private;
messages permissions and configuration are never modified here.
"""
import json
import os
from pathlib import Path
import secrets
import threading
from .message_provider import SelectedPhone
from .policy import Journal
from .transport import _unique
from .call_events import CallExchange,CallSubscription


class CallDevices:
    def __init__(self,directory,path,*,account,changed,receiver_factory,on_incoming=lambda:None):
        self.directory,self.path=Path(directory),Path(path)
        self.account,self.changed,self.receiver_factory=account,changed,receiver_factory
        self.on_incoming=on_incoming
        self.lock=threading.RLock();self.command=threading.Lock()
        self.selection=None;self.exchange=None;self.subscription=None;self.receiver=None
        self.desired=None;self.connected=False;self.generation=0
        self.relay=None;self.dispatch=lambda callback:callback()
        self._load()

    def _load(self):
        if not self.path.exists():return
        info=self.path.lstat()
        if (self.path.is_symlink() or info.st_uid!=os.getuid() or info.st_mode&0o077
                or self.path.parent.stat().st_mode&0o077 or info.st_size>8192):
            raise PermissionError('private call selection required')
        document=json.loads(self.path.read_text(),object_pairs_hook=_unique)
        if set(document)!={'version','phone'} or type(document['version']) is not int or document['version']!=1:
            raise ValueError('invalid call selection')
        self.selection=SelectedPhone(**document['phone'])

    def permission(self,peer,capability,*,incoming=False,epoch=None,account=None):
        current=self.account()
        if not current or account is not None and current!=account:return False
        if not (self.directory/'continuity.db').exists():return False
        journal=Journal(self.directory/'continuity.db')
        try:
            row=journal.db.execute('SELECT account,epoch,grants,outgoing_grants,revoked FROM peers WHERE fingerprint=?',(peer,)).fetchone()
            return bool(row and not row[4] and row[0]==current and (epoch is None or row[1]==epoch)
                        and capability in json.loads(row[2] if incoming else row[3]))
        finally:journal.close()

    def set_permission(self,peer,capability,incoming,enabled):
        if capability not in {'calls.read','calls.control','calls.audio'} or type(incoming) is not bool or type(enabled) is not bool:
            raise ValueError('explicit call consent required')
        account=self.account()
        if not account:raise PermissionError('current account required')
        journal=Journal(self.directory/'continuity.db')
        try:
            journal.db.execute('BEGIN IMMEDIATE')
            row=journal.db.execute('SELECT account,grants,outgoing_grants,revoked FROM peers WHERE fingerprint=?',(peer,)).fetchone()
            if not row or row[3] or row[0]!=account or self.account()!=account:raise PermissionError('current peer required')
            incoming_grants,outgoing_grants=set(json.loads(row[1])),set(json.loads(row[2]))
            grants=incoming_grants if incoming else outgoing_grants
            if enabled:grants.add(capability)
            else:grants.discard(capability)
            journal.set_grants(peer,incoming_grants,outgoing_grants=outgoing_grants)
            journal.db.execute('COMMIT')
        finally:
            if journal.db.in_transaction:journal.db.execute('ROLLBACK')
            journal.close()
        self.suspend();self.resume()

    def select(self,peer,label,address,port,*,carrier="lan"):
        account=self.account()
        if not self.permission(peer,'calls.read'):raise PermissionError('call read permission required')
        journal=Journal(self.directory/'continuity.db')
        try:epoch=journal.db.execute('SELECT epoch FROM peers WHERE fingerprint=?',(peer,)).fetchone()[0]
        finally:journal.close()
        selected=SelectedPhone(peer,epoch,account,label,address,port,carrier)
        parent=self.path.parent;parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if parent.is_symlink() or parent.stat().st_mode&0o077:raise PermissionError('private configuration required')
        temporary=parent/('.calls-'+secrets.token_hex(16))
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump({'version':1,'phone':vars(selected)},stream);stream.flush();os.fsync(stream.fileno())
            if not self.permission(peer,'calls.read',account=account,epoch=epoch):raise PermissionError('call selection changed')
            os.replace(temporary,self.path)
        finally:temporary.unlink(missing_ok=True)
        self.suspend()
        with self.lock:self.selection=selected
        self.resume()

    def state(self):
        with self.lock:
            selected=self.selection
            connected=self.relay.connected(selected.peer) if self.relay and selected and selected.carrier=="relay" else self.connected
            available=bool(connected and selected and self.permission(selected.peer,'calls.read',account=selected.account,epoch=selected.epoch))
            return dict(selected=selected is not None,peer=selected.peer if selected else None,
                        account=selected.account if selected else None,epoch=selected.epoch if selected else None,
                        connected=available,connection=self.relay.retry_state(selected.peer) if self.relay and selected else dict(reason='unavailable',retry_at=None),audio=self.relay.audio_state() if self.relay else dict(status="phone",muted=False),control=bool(available and selected and
                            self.permission(selected.peer,'calls.control',account=selected.account,epoch=selected.epoch)))

    def resume(self):
        with self.lock:
            selected=self.selection
            if self.subscription and self.subscription.thread and not self.subscription.thread.is_alive():
                self.subscription=None;self.exchange=None;self.connected=False
            if self.relay:self.dispatch(lambda:(self.relay.resume(),False)[1])
            if selected and selected.carrier=="lan" and self.subscription is None and self.permission(selected.peer,'calls.read',account=selected.account,epoch=selected.epoch):
                generation=self.generation
                exchange=CallExchange(self.directory,selected.peer,selected.epoch,selected.address,selected.port,
                    account=selected.account,timeout=5,authorized=lambda:self.account()==selected.account)
                def changed(connected):
                    with self.lock:
                        if generation!=self.generation:return
                        self.connected=connected
                    self.changed()
                self.exchange=exchange;self.subscription=CallSubscription(exchange,changed,
                    lambda:self.on_incoming() if generation==self.generation and self.connected and
                        self.permission(selected.peer,'calls.read',account=selected.account,epoch=selected.epoch) else None)
                self.subscription.start()
            if self.desired and self.receiver is None:
                account,peer,epoch,address,port=self.desired
                allowed=lambda:self.permission(peer,'calls.read',incoming=True,account=account,epoch=epoch)
                if allowed():
                    receiver=self.receiver_factory(peer,address,port,allowed,
                        lambda:self.permission(peer,'calls.control',incoming=True,account=account,epoch=epoch))
                    receiver.start();self.receiver=receiver

    def share(self,peer,address,port):
        if not self.permission(peer,'calls.read',incoming=True):raise PermissionError('call sharing permission required')
        journal=Journal(self.directory/'continuity.db')
        try:epoch=journal.db.execute('SELECT epoch FROM peers WHERE fingerprint=?',(peer,)).fetchone()[0]
        finally:journal.close()
        self.stop_sharing()
        with self.lock:self.desired=(self.account(),peer,epoch,address,port)
        self.resume()

    def exchange_call(self,request):
        if not self.command.acquire(blocking=False):raise PermissionError('call operation already pending')
        try:
            with self.lock:
                exchange=self.exchange;generation=self.generation;selected=self.selection
                connected=self.connected
                if self.relay and selected and selected.carrier=='relay':
                    exchange=lambda request:self.relay.exchange(selected.peer,request)
                    connected=self.relay.connected(selected.peer)
                if (not exchange or not connected or request.get('account')!=selected.account
                        or request.get('epoch')!=selected.epoch):raise PermissionError('phone unavailable')
            result=exchange(request)
            with self.lock:
                if generation!=self.generation or not self.state()["connected"]:raise PermissionError('phone disconnected')
            return result
        finally:self.command.release()

    def suspend(self):
        with self.lock:
            self.generation+=1;self.connected=False
            subscription,receiver=self.subscription,self.receiver
            self.subscription=self.receiver=self.exchange=None
        if subscription:subscription.stop()
        if receiver:receiver.stop()
        if self.relay:self.dispatch(lambda:(self.relay.pause(),False)[1])
        self.changed()

    def stop_sharing(self):
        with self.lock:self.desired=None;receiver=self.receiver;self.receiver=None
        if receiver:receiver.stop()

    def stop(self):
        with self.lock:self.desired=None
        self.suspend()
