"""One daemon-owned reusable encrypted peer session, no autonomous reconnect."""
from pathlib import Path
import threading
from . import transport
from .policy import Journal, DIGEST
from .relay import TLSRelayStream
from .session import Receiver


class RelayReceiverSession:
    def __init__(self, directory, peer, *, websocket_factory, authorized, adapter_factory, allow_send=False):
        if not isinstance(peer,str) or not DIGEST.fullmatch(peer):raise ValueError('invalid relay peer')
        self.directory,self.peer=Path(directory),peer
        self.websocket_factory,self.authorized=websocket_factory,authorized
        self.adapter_factory,self.allow_send=adapter_factory,bool(allow_send)
        self.stopped=threading.Event()
        self._lock=threading.Lock()
        self._stream=None

    def stop(self):
        self.stopped.set()
        with self._lock: stream=self._stream
        if stream: stream.close()

    def run(self):
        if self.stopped.is_set() or not self.authorized(): raise PermissionError('sharing unavailable')
        tls=transport.context(self.directory/'device.pem',self.directory/'device.key',
            self.directory/'peers'/(self.peer+'.pem'),server=True)
        stream=TLSRelayStream(self.websocket_factory(),tls,self.peer,server=True,timeout=20)
        journal=close=None
        try:
            with self._lock:
                if self.stopped.is_set(): raise PermissionError('session stopped')
                self._stream=stream
            stream.handshake()
            if self.stopped.is_set() or not self.authorized(): raise PermissionError('sharing unavailable')
            journal=Journal(self.directory/'continuity.db')
            adapter,close=self.adapter_factory()
            adapters={'messages.read':adapter}
            if self.allow_send: adapters['messages.send']=adapter
            receiver=Receiver(journal,adapters)
            while not self.stopped.is_set() and self.authorized():
                stream.wait_for_request()
                request=transport.receive(stream)
                if self.stopped.is_set() or not self.authorized(): raise PermissionError('sharing unavailable')
                # Receiver dispatch retains durable operation IDs and rechecks
                # local peer/account/epoch/capability admission for each record.
                response=receiver.handle(self.peer,request)
                if self.stopped.is_set() or not self.authorized(): raise PermissionError('sharing unavailable')
                transport.send(stream,response)
        finally:
            with self._lock: self._stream=None
            stream.close()
            if close: close()
            if journal: journal.close()
