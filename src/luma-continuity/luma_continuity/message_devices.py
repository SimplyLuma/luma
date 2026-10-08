"""Local consent broker for already paired message devices; no implicit grants."""
import json
import os
from pathlib import Path
import secrets
import threading
from functools import wraps
from .policy import Journal
from .message_provider import SelectedPhone


def _serialized(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self._state_lock:return method(self,*args,**kwargs)
    return call


class MessageDevices:
    def __init__(self, directory, selection_path, *, account, receiver_factory=None):
        self.directory, self.selection_path = Path(directory), Path(selection_path)
        self.account = account
        self.receiver_factory = receiver_factory
        self.receiver = None
        self.shared_peer = None
        self.desired_sharing = None
        self._state_lock=threading.RLock()

    def devices(self):
        account=self.account()
        if not account or not (self.directory/'continuity.db').exists():return []
        journal=Journal(self.directory/'continuity.db')
        try:
            rows=journal.db.execute('SELECT fingerprint,epoch,outgoing_grants,grants FROM peers WHERE account=? AND revoked=0',(account,)).fetchall()
            return [dict(peer=p,epoch=e,can_read='messages.read' in json.loads(outgoing),
                         can_share='messages.read' in json.loads(incoming),can_send='messages.send' in json.loads(outgoing),
                         can_receive_sends='messages.send' in json.loads(incoming),sharing=p==self.shared_peer and bool(self.receiver and getattr(self.receiver,'running',True)))
                    for p,e,outgoing,incoming in rows]
        finally:journal.close()

    def _peer(self, peer, capability):
        account=self.account()
        row=next((row for row in self.devices() if row['peer']==peer),None)
        if not account or not row or not row[capability]:raise PermissionError('paired message permission required')
        return account,row

    def select(self, peer, label, address, port):
        account,row=self._peer(peer,'can_read')
        selected=SelectedPhone(peer,row['epoch'],account,label,address,port)
        document={'version':1,'identity_directory':str(self.directory),'phone':vars(selected)}
        parent=self.selection_path.parent;parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if parent.is_symlink() or parent.stat().st_mode & 0o077:raise PermissionError('private selection directory required')
        temporary=parent/('.selection-'+secrets.token_hex(16))
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:json.dump(document,stream);stream.flush();os.fsync(stream.fileno())
            if self._peer(peer,'can_read')[0] != account:raise PermissionError('account changed')
            os.replace(temporary,self.selection_path)
        finally:temporary.unlink(missing_ok=True)

    def select_companion(self, peer):
        """Use a paired companion phone (ADR-021, no account) as the Messages source."""
        from .companion import Registry
        device=Registry(self.directory).active().get(peer)
        if not device or 'messages.read' not in device['outgoing']:
            raise PermissionError('companion phone with message access required')
        if not device['host'] or not device['port']:
            raise PermissionError('companion phone has not connected yet')
        selected=SelectedPhone(peer,device['epoch'],None,device['name'][:128],device['host'],device['port'],'companion')
        self._write_selection(selected)

    def _write_selection(self, selected):
        document={'version':1,'identity_directory':str(self.directory),'phone':vars(selected)}
        parent=self.selection_path.parent;parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if parent.is_symlink() or parent.stat().st_mode & 0o077:raise PermissionError('private selection directory required')
        temporary=parent/('.selection-'+secrets.token_hex(16))
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:json.dump(document,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.selection_path)
        finally:temporary.unlink(missing_ok=True)

    def revoke(self,peer):
        if peer not in {row['peer'] for row in self.devices()}:raise PermissionError('current-account peer required')
        if self.shared_peer==peer or self.desired_sharing and self.desired_sharing[1]==peer:self.stop()
        journal=Journal(self.directory/'continuity.db')
        try:journal.revoke(peer)
        finally:journal.close()
        # Keep local cache and selection; provider sees revoked grants and stops.

    def set_sending(self, peer, incoming, enabled):
        """Explicit local consent; preserve the existing account, epoch and reads."""
        if type(incoming) is not bool or type(enabled) is not bool:
            raise ValueError('boolean consent required')
        account,_ = self._peer(peer, 'can_share' if incoming else 'can_read')
        journal=Journal(self.directory/'continuity.db')
        try:
            journal.db.execute('BEGIN IMMEDIATE')
            row=journal.db.execute('SELECT account,grants,outgoing_grants,revoked FROM peers WHERE fingerprint=?',(peer,)).fetchone()
            if not row or row[3] or row[0]!=account or self.account()!=account:
                raise PermissionError('current paired account required')
            grants=set(json.loads(row[1] if incoming else row[2]))
            if 'messages.read' not in grants:raise PermissionError('message access required')
            if enabled:grants.add('messages.send')
            else:grants.discard('messages.send')
            journal.set_grants(peer, grants if incoming else json.loads(row[1]),
                               outgoing_grants=json.loads(row[2]) if incoming else grants)
            journal.db.execute('COMMIT')
        except BaseException:
            if journal.db.in_transaction:journal.db.execute('ROLLBACK')
            raise
        finally:journal.close()

    @_serialized
    def share(self,peer,address,port):
        account,row=self._peer(peer,'can_share')
        if self.receiver_factory is None:raise RuntimeError('native receiver unavailable')
        self.suspend()
        epoch=row['epoch']
        def allowed():
            try:
                current,details=self._peer(peer,'can_share')
                return current==account and details['epoch']==epoch
            except (OSError,PermissionError):return False
        receiver=self.receiver_factory(peer,address,port,allowed)
        receiver.start();self.receiver=receiver;self.shared_peer=peer
        self.desired_sharing=(account,peer,epoch,address,port)

    @_serialized
    def suspend(self):
        """Stop active I/O while retaining this session's explicit intent."""
        if self.receiver:self.receiver.stop()
        self.receiver=None;self.shared_peer=None

    @_serialized
    def resume(self):
        desired=self.desired_sharing
        if desired is None:return
        account,peer,epoch,address,port=desired
        if not self.account():return  # transient locked/offline observation
        try:current,row=self._peer(peer,'can_share')
        except PermissionError:
            self.stop();raise
        if current!=account or row['epoch']!=epoch:
            self.stop();raise PermissionError('sharing identity changed')
        if self.receiver and getattr(self.receiver,'running',True):return
        # share revalidates and never creates or widens a grant.
        self.share(peer,address,port)

    @_serialized
    def stop(self):
        self.desired_sharing=None
        self.suspend()

    def pairing_document(self, operation, document='', verified_pin=''):
        """User-driven public invitation exchange; no token or private-key export."""
        account=self.account()
        if not account:raise PermissionError('current account required')
        from .bootstrap import create_identity
        from .transport import fingerprint
        from .pairing import create_offer,accept_offer,finish_offer
        if not self.directory.exists():
            self.directory.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            create_identity(self.directory)
        pin=fingerprint((self.directory/'device.pem').read_text())
        if self.account()!=account:raise PermissionError('account changed')
        if operation=='create':
            result=create_offer(self.directory,a_to_b=['messages.read'],b_to_a=[],account=account)
        elif operation=='accept':
            result=accept_offer(self.directory,document.encode(),verified_a_pin=verified_pin,
                                a_to_b=['messages.read'],b_to_a=[],account=account)
        elif operation=='finish':
            finish_offer(self.directory,document.encode(),verified_b_pin=verified_pin,account=account)
            return {'kind':'complete','fingerprint':pin}
        else:raise ValueError('unsupported pairing action')
        return {'kind':'offer' if operation=='create' else 'response','fingerprint':pin,'document':result.decode()}
