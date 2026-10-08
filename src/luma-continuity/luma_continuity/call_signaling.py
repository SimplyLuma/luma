"""Ephemeral SDP/ICE on an existing mutually pinned native TLS connection.

The call owner supplies one explicitly consented call/session and closes this
channel on any account, call, permission or lifecycle change. No signal is
stored, retried, used as a native command, or treated as consent.
"""
import json
from pathlib import Path
import threading
from .policy import Journal,IDENTIFIER
from . import transport


class PairedAudioSignals:
    def __init__(self,stream,directory,peer,*,account,epoch,call,session,incoming,
                 authorized,receive,failed,dispatch,channel_factory=None):
        if (not all(isinstance(v,str) and IDENTIFIER.fullmatch(v) for v in (epoch,call,session))
                or type(incoming) is not bool or not isinstance(account,str) or not 0<len(account)<=512):
            raise ValueError('one explicit audio session required')
        self.directory,self.peer=Path(directory),peer
        self.binding={'account':account,'epoch':epoch,'call':call,'session':session}
        self.incoming,self.authorized=incoming,authorized
        self.receive,self.failed,self.dispatch=receive,failed,dispatch
        self.closed=threading.Event();self.sent=0;self.received=0;self.pending=0;self.lock=threading.RLock()
        from .call_channel import PinnedJSONChannel
        factory=channel_factory or (lambda **kw:PinnedJSONChannel(stream,peer,**kw))
        self.channel=factory(check=self._allowed,receive=self._deliver,failed=self._lost)
        self.worker=self.channel.worker

    def _allowed(self):
        if self.closed.is_set() or not self.authorized():raise PermissionError('audio consent unavailable')
        journal=Journal(self.directory/'continuity.db')
        try:
            row=journal.db.execute('SELECT account,epoch,grants,outgoing_grants,revoked FROM peers WHERE fingerprint=?',(self.peer,)).fetchone()
            if (not journal.enabled() or not row or row[4] or row[0]!=self.binding['account']
                    or row[1]!=self.binding['epoch'] or 'calls.audio' not in json.loads(row[2] if self.incoming else row[3])):
                raise PermissionError('paired audio permission required')
        finally:journal.close()

    def start(self):self.channel.start()

    def send(self,message):
        self._allowed()
        raw=transport.encode(message)
        if len(raw)>18432:raise ValueError('audio signal bound')
        with self.lock:
            if self.sent>=34:raise ValueError('audio signal count bound')
            envelope=dict(self.binding,sequence=self.sent,message=json.loads(raw,object_pairs_hook=transport._unique))
            self.channel.send(envelope);self.sent+=1

    def _deliver(self,value):
        self._allowed()
        if (not isinstance(value,dict) or set(value)!=set(self.binding)|{'sequence','message'}
                or any(value[k]!=v for k,v in self.binding.items())
                or type(value['sequence']) is not int or value['sequence']!=self.received
                or self.received>=34 or len(transport.encode(value['message']))>18432):
            raise ValueError('obsolete or invalid audio signal')
        self.received+=1
        with self.lock:
            if self.pending>=34:raise ValueError('audio dispatch bound')
            self.pending+=1
        def deliver():
            with self.lock:self.pending-=1
            if self.closed.is_set():return False
            try:self._allowed();self.receive(value['message'])
            except Exception:self._lost()
            return False
        self.dispatch(deliver)

    def _lost(self):
        with self.lock:
            if self.closed.is_set():return
            self.close()
        self.dispatch(lambda:(self.failed(),False)[1])

    def close(self):
        self.closed.set();self.channel.close()
