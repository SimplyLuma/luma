"""Daemon-owned relay rendezvous and message sessions for explicit bindings."""
import random
import threading
from pathlib import Path
from .account import AccountError
from .policy import Journal
from .relay_api import RelayAPI
from .relay import connect_wss
from .relay_binding import BoundRelayExchange
from .relay_control import RelayDirectory
from .relay_receiver import RelayReceiverSession
from .sharing_intent import SharingIntent


class RelayRuntime:
    def __init__(self, directory, bindings, api, *, bearer, account, adapter_factory=None, websocket_connector=connect_wss):
        if not bindings: raise ValueError('explicit relay bindings required')
        if len({(b.account,b.device_id) for b in bindings})!=1 or len({b.peer for b in bindings})!=len(bindings):
            raise ValueError('one explicit device/account binding required')
        self.directory,self.bindings,self.account=Path(directory),tuple(bindings),account
        self.bound_account=bindings[0].account
        self.enabled=threading.Event();self.stopped=threading.Event()
        self.lock=threading.RLock();self.generation=0;self.thread=None
        self.receivers={};self.adapter_factory=adapter_factory
        self.intent=SharingIntent(directory,bindings)
        self.sharing_peers=self.intent.load()
        self.api=RelayAPI(api,bindings[0].device_id,bearer=bearer,
            authorized=lambda:self.enabled.is_set() and not self.stopped.is_set() and self.account()==self.bound_account,
            connector=websocket_connector)
        self.directory_state=RelayDirectory(bindings[0].device_id,[b.pair_id for b in bindings],api.config.api_origin)
        self.requesters={b.peer:BoundRelayExchange(directory,b,self.api,account=self._account) for b in bindings if b.role=='requester'}
        self.last_error=None

    def _account(self):
        return self.account() if self.enabled.is_set() and not self.stopped.is_set() else None

    def exchange(self, peer, encoded):
        with self.lock:
            if self._account()!=self.bound_account or peer not in self.requesters:
                raise PermissionError('message relay unavailable')
            requester=self.requesters[peer]
        return requester.call(peer,encoded)

    def can_publish(self, peer, encoded):
        """Recheck grants after worker completion, before a UI receives data."""
        if self._account()!=self.bound_account or peer not in self.requesters:return False
        try:
            import json
            with self.requesters[peer].exchange._admit(json.loads(encoded)):pass
            return True
        except Exception:return False

    def accepts_sharing(self, peer):
        return any(b.peer==peer and b.role=='receiver' for b in self.bindings)

    def enable_sharing(self, peer):
        binding=next((b for b in self.bindings if b.peer==peer and b.role=='receiver'),None)
        if binding is None or not self._allowed(binding):raise PermissionError('sharing not approved')
        with self.lock:
            selected=self.sharing_peers|{peer}
            self.intent.save(selected);self.sharing_peers=selected
        self._reconcile([],self.generation)

    def stop_sharing(self):
        with self.lock:
            self.intent.save(set());self.sharing_peers.clear()
            receivers=list(self.receivers.values());self.receivers.clear()
        for receiver in receivers:receiver.stop()

    def sharing(self, peer):
        return peer in self.sharing_peers and self._account()==self.bound_account

    def resume(self):
        with self.lock:
            if self.stopped.is_set() or self.account()!=self.bound_account:return
            self.enabled.set()
            if self.thread is None:
                self.thread=threading.Thread(target=self._events,name='connect-relay-events',daemon=True)
                self.thread.start()

    def pause(self):
        with self.lock:
            self.generation+=1;self.enabled.clear()
            receivers=list(self.receivers.values());self.receivers.clear()
        self.api.cancel_events()
        for receiver in receivers:receiver.stop()
        for requester in self.requesters.values():requester.invalidate()

    def close(self):
        self.stopped.set();self.pause();self.enabled.set()
        for requester in self.requesters.values():requester.close()

    def _allowed(self, binding):
        if self._account()!=binding.account:return False
        journal=Journal(self.directory/'continuity.db')
        try:
            import json
            row=journal.db.execute('SELECT account,epoch,grants,revoked FROM peers WHERE fingerprint=?',(binding.peer,)).fetchone()
            return bool(row and row[0]==binding.account and row[1]==binding.epoch and not row[3]
                        and 'messages.read' in json.loads(row[2]))
        finally:journal.close()

    def _reconcile(self, obsolete, generation):
        with self.lock:
            if generation!=self.generation or not self.enabled.is_set():return
            for session in obsolete:
                receiver=self.receivers.pop(session.session_id,None)
                if receiver:receiver.stop()
                for requester in self.requesters.values():
                    if requester.session and requester.session.session_id==session.session_id:requester.invalidate()
            for binding in self.bindings:
                session=self.directory_state.sessions.get(binding.pair_id)
                if (binding.role!='receiver' or binding.peer not in self.sharing_peers
                        or session is None or session.session_id in self.receivers):continue
                if self.adapter_factory is None or not self._allowed(binding):continue
                allowed=lambda b=binding:self._allowed(b)
                receiver=RelayReceiverSession(self.directory,binding.peer,
                    websocket_factory=lambda s=session:self.api.open(s),authorized=allowed,
                    adapter_factory=lambda b=binding,a=allowed:self.adapter_factory(b,a),allow_send=True)
                self.receivers[session.session_id]=receiver
                def run(r=receiver,sid=session.session_id):
                    try:r.run()
                    except Exception:pass  # Only coarse state leaves this owner.
                    finally:
                        with self.lock:
                            if self.receivers.get(sid) is r:self.receivers.pop(sid,None)
                threading.Thread(target=run,name='connect-relay-receiver',daemon=True).start()

    def _events(self):
        failures=0
        while not self.stopped.is_set():
            self.enabled.wait()
            if self.stopped.is_set():break
            generation=self.generation
            delay=None
            try:
                for event,cursor,data in self.api.events(self.directory_state.cursor):
                    if self.stopped.is_set() or not self.enabled.is_set() or generation!=self.generation:break
                    with self.lock:
                        obsolete=self.directory_state.apply(event,cursor,data)
                        self._reconcile(obsolete,generation)
                    failures=0;self.last_error=None
            except AccountError as error:
                self.last_error=error.code;delay=error.retry_after
            except Exception:self.last_error='relay_unavailable'
            if self.stopped.is_set():break
            # A control stream loss invalidates data-plane authorization. Any
            # committed native receipt remains in the durable receiver journal.
            with self.lock:
                if generation!=self.generation:continue
                receivers=list(self.receivers.values());self.receivers.clear()
            for receiver in receivers:receiver.stop()
            for requester in self.requesters.values():requester.invalidate()
            if not self.enabled.is_set():continue
            failures=min(failures+1,6)
            delay=delay if delay is not None else min(60,2**failures)*random.uniform(.8,1)
            self.stopped.wait(delay)
