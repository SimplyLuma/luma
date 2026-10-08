"""One explicitly started, daemon-owned paired TLS message-read listener."""
import ipaddress
from pathlib import Path
import socket
import threading
from .local import bounded_stream
from .policy import Journal
from .session import Receiver
from . import transport


class ReadReceiver:
    def __init__(self, directory, peer, *, address, port, authorized, adapter_factory, allow_send=False, event_driven=False):
        self.directory, self.peer = Path(directory), peer
        self.authorized, self.adapter_factory = authorized, adapter_factory
        self.allow_send = bool(allow_send)
        self.event_driven = bool(event_driven)
        endpoint=ipaddress.ip_address(address)
        if endpoint.is_unspecified or endpoint.is_multicast:
            raise ValueError("explicit unicast interface required")
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('explicit unprivileged listener port required')
        self.address, self.port = address, port
        self.stop_event = threading.Event()
        self.listener = self.active = self.thread = None
        self.error = None

    def start(self):
        if self.thread is not None: raise RuntimeError('receiver already started')
        if not self.authorized(): raise PermissionError('message sharing is not approved')
        tls = transport.context(self.directory/'device.pem', self.directory/'device.key',
            self.directory/'peers'/(self.peer+'.pem'), server=True)
        listener = socket.socket(socket.AF_INET6 if ':' in self.address else socket.AF_INET)
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try: listener.bind((self.address,self.port));listener.listen(2);listener.settimeout(None if self.event_driven else .25)
        except BaseException: listener.close();raise
        self.listener = listener
        def run():
            journal = Journal(self.directory/'continuity.db')
            adapter = close = None
            try:
                adapter, close = self.adapter_factory()
                adapters = {'messages.read':adapter}
                if self.allow_send: adapters['messages.send'] = adapter
                receiver = Receiver(journal, adapters)
                while not self.stop_event.is_set() and self.authorized():
                    try: raw,_ = listener.accept()
                    except socket.timeout: continue
                    except OSError: break
                    try:
                        self.active = raw
                        with raw, bounded_stream(raw,tls,server=True,timeout=5) as stream:
                            self.active = stream
                            peer = transport.authenticate(stream,self.peer)
                            if self.authorized() and not self.stop_event.is_set(): receiver.serve_one(stream,peer)
                    except (OSError,ValueError,EOFError,PermissionError): pass
                    finally: self.active = None
            except Exception:
                self.error = 'receiver_unavailable'
            finally:
                if close: close()
                journal.close();listener.close()
        self.thread = threading.Thread(target=run,name='connect-message-reader',daemon=True)
        self.thread.start()

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set())

    def stop(self, *, wait=False):
        self.stop_event.set()
        for stream in (self.active,self.listener):
            if stream:
                try: stream.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                stream.close()
        if wait and self.thread: self.thread.join(timeout=6)
