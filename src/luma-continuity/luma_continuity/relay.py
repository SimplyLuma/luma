"""Opaque WSS carrier for inner mutually authenticated TLS 1.3.

WebSocket is injected to permit exercising the crypto carrier without network
provisioning. Production callers must establish authenticated wss:// first.
"""
import hashlib
import hmac
import ssl
import time
from .transport import ALPN

CHUNK = 65536


class TLSRelayStream:
    def __init__(self, websocket, context, peer_fingerprint, *, server, timeout=20):
        self.websocket = websocket
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = context.wrap_bio(self.incoming, self.outgoing, server_side=server)
        self.peer_fingerprint = peer_fingerprint
        self.timeout = timeout
        self.authenticated = False
        self._prefetched = b''

    def _flush(self):
        while self.outgoing.pending:
            self.websocket.send(self.outgoing.read(CHUNK))

    def _feed(self, deadline):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("relay deadline")
        data = self.websocket.recv(timeout=remaining)
        if not isinstance(data, bytes) or not 0 < len(data) <= CHUNK:
            raise ValueError("invalid relay frame")
        self.incoming.write(data)

    def handshake(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.tls.do_handshake()
                self._flush()
                break
            except ssl.SSLWantReadError:
                self._flush(); self._feed(deadline)
        actual = hashlib.sha256(self.tls.getpeercert(binary_form=True)).hexdigest()
        if (self.tls.version() != "TLSv1.3" or self.tls.selected_alpn_protocol() != ALPN
                or not hmac.compare_digest(actual, self.peer_fingerprint)):
            raise PermissionError("unapproved device or protocol")
        self.authenticated = True
        return actual

    def sendall(self, data):
        if not self.authenticated:
            raise PermissionError("handshake required")
        view = memoryview(data)
        deadline = time.monotonic() + self.timeout
        while view:
            try:
                written = self.tls.write(view[:16384]); view = view[written:]
                self._flush()
            except ssl.SSLWantReadError:
                self._flush(); self._feed(deadline)

    def recv(self, size):
        if not self.authenticated:
            raise PermissionError("handshake required")
        if self._prefetched:
            value,self._prefetched=self._prefetched[:size],self._prefetched[size:]
            return value
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                return self.tls.read(size)
            except ssl.SSLWantReadError:
                self._flush(); self._feed(deadline)
            except ssl.SSLZeroReturnError:
                return b""

    def wait_for_request(self):
        """Interruptible idle receive; start a deadline when encrypted data arrives.

        The owning session closes this WSS on sleep, auth loss or server expiry.
        No periodic application request or local timer is needed while idle.
        """
        if not self.authenticated: raise PermissionError('handshake required')
        if self._prefetched: return
        deadline = None
        while True:
            try:
                value = self.tls.read(1)
                if not value: raise EOFError('relay closed')
                self._prefetched = value
                return
            except ssl.SSLWantReadError:
                self._flush();self._feed(deadline)
                if deadline is None: deadline = time.monotonic() + self.timeout
            except ssl.SSLZeroReturnError:
                raise EOFError('relay closed') from None

    def close(self):
        self.authenticated = False
        self.websocket.close()


def connect_wss(url, ticket, *, bearer=None, tls_context=None):
    """Maintained websockets client, exact route supplied by cloud owner.

    No redirects to plaintext, ambient proxy credentials or TLS key logging.
    Tickets are headers, never URLs; do not log headers/exceptions with secrets.
    """
    from urllib.parse import urlsplit
    from websockets.sync.client import connect
    target = urlsplit(url)
    if (target.scheme != "wss" or not target.hostname or target.username or
            target.password or target.query or target.fragment):
        raise ValueError("authenticated WSS endpoint required")
    if not isinstance(ticket, str) or not ticket or "\r" in ticket or "\n" in ticket:
        raise ValueError("invalid relay ticket")
    outer = tls_context or ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if tls_context is None:outer.load_default_certs()
    if (outer.verify_mode!=ssl.CERT_REQUIRED or not outer.check_hostname or outer.keylog_filename):
        raise ValueError('verified TLS context required')
    outer.minimum_version = ssl.TLSVersion.TLSv1_2
    headers = {"X-Luma-Relay-Ticket": ticket}
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    return connect(url, ssl=outer, additional_headers=headers, proxy=None,
        max_size=CHUNK, max_queue=4, compression=None, open_timeout=20, close_timeout=5)
