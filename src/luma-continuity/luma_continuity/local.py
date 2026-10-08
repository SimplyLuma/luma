"""Explicit paired-device LAN exchange for the native provider worker.

No discovery, service startup, account bypass, or capability approval occurs
here. An owner supplies a numeric address and a previously approved peer. Each
request uses one mutually authenticated TLS connection and a bounded deadline.
"""
from contextlib import contextmanager
import ipaddress
import json
from pathlib import Path
import socket
import threading
from . import transport
from .policy import DIGEST, IDENTIFIER, Denied, Journal


@contextmanager
def bounded_stream(raw, tls, *, server, timeout):
    stream = tls.wrap_socket(raw, server_side=server, do_handshake_on_connect=False)
    def expire():
        try: stream.shutdown(socket.SHUT_RDWR)
        except OSError: pass
    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    try:
        stream.settimeout(timeout)
        timer.start()
        stream.do_handshake()
        yield stream
    finally:
        timer.cancel()
        stream.close()


class ScopedExchange:
    """Common authorization and receipt boundary for an authenticated carrier."""
    def __init__(self, directory, peer, epoch, *, timeout=10, authorized=lambda: True):
        if (not isinstance(peer, str) or not DIGEST.fullmatch(peer)
                or not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch)
                or type(timeout) not in (int, float) or not 0 < timeout <= 20):
            raise ValueError('invalid paired endpoint')
        self.directory, self.peer, self.epoch = Path(directory), peer, epoch
        self.timeout = timeout
        self.authorized = authorized

    @contextmanager
    def _admit(self, request):
        journal = Journal(self.directory / 'continuity.db')
        try:
            # Serialize transmission admission with local revoke/set_grants.
            # Revocation waits for an already admitted bounded send to finish.
            journal.db.execute('BEGIN IMMEDIATE')
            if not journal.enabled():raise Denied('continuity disabled')
            row = journal.db.execute('SELECT epoch,account,outgoing_grants,revoked FROM peers WHERE fingerprint=?',
                                     (self.peer,)).fetchone()
            if (not row or row[3] or row[0] != self.epoch
                    or request.get('epoch') != self.epoch or request.get('account') != row[1]
                    or request.get('capability') not in json.loads(row[2])):
                raise Denied('selected device or capability is no longer approved')
            if not self.authorized():
                raise Denied('account authorization is no longer current')
            yield
            journal.db.execute('COMMIT')
        finally:
            if journal.db.in_transaction: journal.db.execute('ROLLBACK')
            journal.close()

    def __call__(self, request):
        transport.encode(request)
        with self._admit(request): pass
        with self._connect() as stream:
            with self._admit(request):
                transport.send(stream, request)
            response = transport.receive(stream)
            # Reject results revoked while in flight. This check is the
            # receipt admission point; UI owners must also gate rendering.
            with self._admit(request): pass
            if (set(response) != {'state', 'result'}
                    or response['state'] not in {'complete', 'unknown', 'denied', 'invalid'}
                    or response['result'] is not None and not isinstance(response['result'], dict)):
                raise ValueError('invalid paired-device receipt')
        return response


class PairedExchange(ScopedExchange):
    """LAN carrier; never retries a request itself."""
    def __init__(self, directory, peer, epoch, address, port, *, timeout=10, authorized=lambda: True):
        super().__init__(directory, peer, epoch, timeout=timeout, authorized=authorized)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid paired endpoint')
        self.address = str(ipaddress.ip_address(address))
        self.port = port

    @contextmanager
    def _connect(self):
        peer_cert = self.directory / 'peers' / (self.peer + '.pem')
        tls = transport.context(self.directory / 'device.pem', self.directory / 'device.key',
                                peer_cert, server=False)
        with socket.create_connection((self.address, self.port), timeout=self.timeout) as raw:
            with bounded_stream(raw, tls, server=False, timeout=self.timeout) as stream:
                transport.authenticate(stream, self.peer)
                yield stream
