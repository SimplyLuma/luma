"""Owned relay carrier with the same local admission/receipt rules as LAN.

The daemon supplies a current authorized session's outer WSS connection.
Neither transport retries operations or changes account/pairing identity.
"""
from contextlib import contextmanager
import threading
from .local import ScopedExchange
from .relay import TLSRelayStream
from . import transport


class PairedRelayExchange(ScopedExchange):
    def __init__(self, directory, peer, epoch, *, websocket_factory, authorized, timeout=10):
        super().__init__(directory, peer, epoch, timeout=timeout, authorized=authorized)
        self.websocket_factory = websocket_factory
        self._operation = threading.Lock()
        self._state = threading.Lock()
        self._stream = None
        self._generation = 0
        self._closed = False

    def invalidate(self):
        """Interrupt current I/O and fence any unfinished old session creation."""
        with self._state:
            self._generation += 1
            stream, self._stream = self._stream, None
        if stream: stream.close()

    def close(self):
        with self._state: self._closed = True
        self.invalidate()

    def _current_stream(self):
        with self._state:
            if self._closed: raise PermissionError('relay exchange closed')
            if self._stream is not None: return self._stream
            generation = self._generation
        tls = transport.context(self.directory/'device.pem', self.directory/'device.key',
            self.directory/'peers'/(self.peer+'.pem'), server=False)
        websocket = self.websocket_factory()
        stream = TLSRelayStream(websocket, tls, self.peer, server=False, timeout=self.timeout)
        try:
            stream.handshake()
            with self._state:
                if self._closed or generation != self._generation:
                    raise PermissionError('obsolete relay session')
                self._stream = stream
            return stream
        except BaseException:
            stream.close()
            raise

    @contextmanager
    def _connect(self):
        # One request/receipt at a time on the reused encrypted stream. A failed
        # operation is never automatically retried here; the durable queue and
        # receiver journal remain authoritative about its immutable identity.
        with self._operation:
            try:
                yield self._current_stream()
            except BaseException:
                self.invalidate()
                raise
