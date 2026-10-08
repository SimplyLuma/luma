"""TLS 1.3 device transport using Python/OpenSSL, never application crypto.

Trust anchors are explicitly approved device certificates, not system CAs.
Exact peer certificate pinning is additionally mandatory after the handshake.
The same stream may be carried by a separately authenticated opaque relay.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import ssl
import struct
import time
from pathlib import Path

MAX_FRAME = 1024 * 1024
ALPN = "luma-continuity/1"


def fingerprint(pem: str) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def context(cert: Path, key: Path, approved_peer: Path, *, server: bool) -> ssl.SSLContext:
    # Construct explicitly: create_default_context honors SSLKEYLOGFILE.
    result = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    if not server:
        # Identity is the approved leaf fingerprint, not relay hostname.
        result.check_hostname = False
    result.verify_mode = ssl.CERT_REQUIRED
    result.minimum_version = ssl.TLSVersion.TLSv1_3
    result.maximum_version = ssl.TLSVersion.TLSv1_3
    result.verify_flags |= ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    result.load_cert_chain(cert, key)
    result.load_verify_locations(cafile=str(approved_peer))
    result.set_alpn_protocols([ALPN])
    if server:
        result.num_tickets = 0
    return result


def authenticate(stream: ssl.SSLSocket, approved_fingerprint: str) -> str:
    actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
    if (stream.version() != "TLSv1.3" or stream.selected_alpn_protocol() != ALPN
            or not hmac.compare_digest(actual, approved_fingerprint)):
        raise PermissionError("unapproved device or protocol")
    return actual


def encode(value: dict) -> bytes:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError("frame size")
    return data


def send(stream, value: dict) -> None:
    data = encode(value)
    stream.sendall(struct.pack("!I", len(data)) + data)


def _read(stream, count: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < count:
        if time.monotonic() >= deadline:
            raise TimeoutError("frame deadline")
        part = stream.recv(count - len(data))
        if not part:
            raise EOFError("truncated frame")
        data.extend(part)
    return bytes(data)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def receive(stream) -> dict:
    deadline = time.monotonic() + 20
    size, = struct.unpack("!I", _read(stream, 4, deadline))
    if not 0 < size <= MAX_FRAME:
        raise ValueError("frame size")
    def reject(value):
        raise ValueError("nonfinite JSON")
    value = json.loads(_read(stream, size, deadline), object_pairs_hook=_unique, parse_constant=reject)
    if not isinstance(value, dict):
        raise ValueError("object required")
    return value
