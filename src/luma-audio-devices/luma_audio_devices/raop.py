# SPDX-License-Identifier: MPL-2.0
"""AirPlay (RAOP) receivers: identity, sink parameters and a connection check.

The TXT-record mapping follows PipeWire's module-raop-discover
(src/modules/module-raop-discover.c, PipeWire 1.6.8, MIT) so a receiver
chosen in Luma gets exactly the sink parameters PipeWire itself would have
used. The RTSP check sends the same OPTIONS request and Digest/Basic
authentication as module-raop-sink, before any sink exists, so a wrong
password or a receiver that refuses strangers is reported to the person
instead of producing a silent output.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import ipaddress
import os
import re
import socket

from .classify import AIRPLAY_NODE_PREFIX

__all__ = ("Receiver", "ProbeResult", "parse_service_name", "txt_to_dict", "sink_module_args",
           "probe", "PROBE_OK", "PROBE_PASSWORD_REQUIRED", "PROBE_PASSWORD_INCORRECT",
           "PROBE_DENIED", "PROBE_UNREACHABLE", "PROBE_UNSUPPORTED", "dns_escape_instance",
           "receiver_kind")

SERVICE_TYPE = "_raop._tcp"

PROBE_OK = "ok"
PROBE_PASSWORD_REQUIRED = "password-required"
PROBE_PASSWORD_INCORRECT = "password-incorrect"
PROBE_DENIED = "denied"
PROBE_UNREACHABLE = "unreachable"
PROBE_UNSUPPORTED = "unsupported"

RAOP_AUTH_USER = "iTunes"          # the user name module-raop-sink authenticates with

# AirPlay status flags ("sf"/"flags" TXT key)
STATUS_PIN_REQUIRED = 0x8
STATUS_PASSWORD_REQUIRED = 0x80

_HEX_ID = re.compile(r"^([0-9A-Fa-f]{12})@(.+)$")


def parse_service_name(service_name: str) -> tuple[str, str]:
    """("0615378145AF@Nick’s MacBook Pro") -> ("0615378145af", "Nick’s MacBook Pro").

    RAOP instance names start with the receiver's 48-bit device id, which
    stays the same when the receiver is renamed or changes address. Names
    without one fall back to the instance name itself."""
    match = _HEX_ID.match(service_name)
    if match:
        return match.group(1).lower(), match.group(2)
    name = service_name.split("@", 1)[-1] or service_name
    digest = hashlib.sha256(service_name.encode("utf-8")).hexdigest()[:12]
    return f"n{digest}", name


def txt_to_dict(txt) -> dict[str, str]:
    """Avahi hands TXT records over as lists of byte arrays: b"key=value"."""
    result: dict[str, str] = {}
    for entry in txt or ():
        if isinstance(entry, (list, tuple)):
            entry = bytes(entry)
        if isinstance(entry, str):
            entry = entry.encode("utf-8", "surrogateescape")
        key, _, value = bytes(entry).partition(b"=")
        name = key.decode("ascii", "replace").strip().lower()
        if name and name not in result:
            result[name] = value.decode("utf-8", "replace")
    return result


def _in_list(value: str, needle: str) -> bool:
    return needle in [part.strip() for part in value.split(",")]


def receiver_kind(model: str) -> str:
    """The icon family for a receiver model string such as "Mac15,9"."""
    lowered = model.lower()
    if lowered.startswith(("mac", "imac")):
        return "computer"
    if lowered.startswith(("appletv", "tv")) or "tv" in lowered:
        return "tv"
    return "speaker"


@dataclass
class Receiver:
    """One resolved _raop._tcp service."""

    service_name: str
    host_name: str
    address: str
    port: int
    interface: int = -1
    txt: dict[str, str] = field(default_factory=dict)
    local: bool = False

    @property
    def id(self) -> str:
        return parse_service_name(self.service_name)[0]

    @property
    def name(self) -> str:
        return parse_service_name(self.service_name)[1]

    @property
    def model(self) -> str:
        return self.txt.get("am", "")

    @property
    def kind(self) -> str:
        return receiver_kind(self.model)

    @property
    def transport(self) -> str:
        value = self.txt.get("tp", "UDP")
        if _in_list(value, "UDP"):
            return "udp"
        if _in_list(value, "TCP"):
            return "tcp"
        return value.lower()

    @property
    def encryption(self) -> str:
        value = self.txt.get("et", "0")
        if _in_list(value, "5"):
            return "fp_sap25"
        if _in_list(value, "4"):
            return "auth_setup"
        if _in_list(value, "1"):
            return "RSA"
        return "none"

    @property
    def codec(self) -> str:
        value = self.txt.get("cn", "0")
        for code, name in (("0", "PCM"), ("1", "ALAC"), ("2", "AAC"), ("3", "AAC-ELD")):
            if _in_list(value, code):
                return name
        return "unknown"

    def _status_flags(self) -> int:
        for key in ("sf", "flags"):
            value = self.txt.get(key)
            if value:
                try:
                    return int(value, 0)
                except ValueError:
                    continue
        return 0

    @property
    def announces_password(self) -> bool:
        return (self.txt.get("pw", "").lower() == "true"
                or bool(self._status_flags() & STATUS_PASSWORD_REQUIRED))

    @property
    def needs_pin(self) -> bool:
        return bool(self._status_flags() & STATUS_PIN_REQUIRED)

    @property
    def supported(self) -> bool:
        # module-raop-sink streams PCM or ALAC only.
        return self.codec in ("PCM", "ALAC") and not self.needs_pin

    @property
    def node_name(self) -> str:
        return AIRPLAY_NODE_PREFIX + self.id

    def connect_address(self) -> str:
        """The address module-raop-sink should dial, scoped like
        module-raop-discover scopes link-local addresses."""
        try:
            parsed = ipaddress.ip_address(self.address.split("%", 1)[0])
        except ValueError:
            return self.address
        if parsed.is_link_local and self.interface >= 0 and "%" not in self.address:
            return f"{self.address}%{self.interface}"
        return self.address


def sink_module_args(receiver: Receiver, password: str | None = None) -> dict:
    """Arguments for libpipewire-module-raop-sink for this receiver."""
    args: dict = {
        "raop.ip": receiver.connect_address(),
        "raop.port": str(receiver.port),
        "raop.name": receiver.service_name,
        "raop.hostname": receiver.host_name,
        "raop.transport": receiver.transport,
        "raop.encryption.type": receiver.encryption,
        "raop.audio.codec": receiver.codec,
        "node.name": receiver.node_name,
        "node.description": receiver.name,
        "stream.props": {
            "node.network": True,
            "luma.airplay.id": receiver.id,
            "device.icon-name": "audio-speakers",
            "device.model": receiver.model,
            "media.name": f"AirPlay to {receiver.name}",
        },
    }
    if receiver.interface >= 0:
        args["raop.ifindex"] = str(receiver.interface)
    for key, prop in (("ch", "audio.channels"), ("sr", "audio.rate")):
        if receiver.txt.get(key):
            args[prop] = receiver.txt[key]
    if password:
        args["raop.password"] = password
    return args


def dns_escape_instance(label: str) -> str:
    """Escape a DNS-SD instance name for use as the first label of a record
    name, as avahi_service_name_join does."""
    return label.replace("\\", "\\\\").replace(".", "\\.")


@dataclass(frozen=True)
class ProbeResult:
    status: str
    detail: str = ""


def _digest_header(method: str, uri: str, realm: str, nonce: str, password: str) -> str:
    ha1 = hashlib.md5(f"{RAOP_AUTH_USER}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return (f'Digest username="{RAOP_AUTH_USER}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{response}"')


def _basic_header(password: str) -> str:
    token = base64.b64encode(f"{RAOP_AUTH_USER}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _parse_challenge(value: str) -> tuple[str, dict[str, str]]:
    scheme, _, rest = value.strip().partition(" ")
    params = dict(re.findall(r'(\w+)="([^"]*)"', rest))
    return scheme, params


def _read_response(sock: socket.socket) -> tuple[int, dict[str, str]]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    head = data.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
    lines = head.split("\r\n")
    match = re.match(r"RTSP/\d\.\d\s+(\d{3})", lines[0] if lines else "")
    if not match:
        raise ConnectionError("not an RTSP response")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return int(match.group(1)), headers


def probe(address: str, port: int, password: str | None = None, *, timeout: float = 4.0) -> ProbeResult:
    """Ask the receiver whether it will accept a stream from us.

    Blocking; the service runs it on a worker thread."""
    host = address
    scope = None
    if "%" in address:
        host, scope = address.split("%", 1)
    try:
        family = socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
    except ValueError:
        family = socket.AF_INET
    instance = os.urandom(8).hex().upper()

    def request(sock: socket.socket, cseq: int, authorization: str | None) -> tuple[int, dict[str, str]]:
        lines = [
            "OPTIONS * RTSP/1.0",
            f"CSeq: {cseq}",
            "User-Agent: Luma/1.0",
            f"Client-Instance: {instance}",
            f"DACP-ID: {instance}",
        ]
        if authorization:
            lines.append(f"Authorization: {authorization}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        return _read_response(sock)

    try:
        if family == socket.AF_INET6:
            target = (host, port, 0, int(scope) if scope and scope.isdigit() else 0)
        else:
            target = (host, port)
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(target)
            status, headers = request(sock, 1, None)
            if status == 200:
                return ProbeResult(PROBE_OK)
            if status in (403, 470):
                return ProbeResult(PROBE_DENIED, f"RTSP {status}")
            if status != 401:
                return ProbeResult(PROBE_UNSUPPORTED, f"RTSP {status}")
            challenge = headers.get("www-authenticate", "")
            if not password:
                return ProbeResult(PROBE_PASSWORD_REQUIRED)
            scheme, params = _parse_challenge(challenge)
            if scheme.lower() == "digest" and "realm" in params and "nonce" in params:
                authorization = _digest_header("OPTIONS", "*", params["realm"], params["nonce"], password)
            elif scheme.lower() == "basic":
                authorization = _basic_header(password)
            else:
                return ProbeResult(PROBE_UNSUPPORTED, f"authentication scheme {scheme or 'missing'}")
            status, _headers = request(sock, 2, authorization)
            if status == 200:
                return ProbeResult(PROBE_OK)
            if status == 401:
                return ProbeResult(PROBE_PASSWORD_INCORRECT)
            if status in (403, 470):
                return ProbeResult(PROBE_DENIED, f"RTSP {status}")
            return ProbeResult(PROBE_UNSUPPORTED, f"RTSP {status}")
    except (OSError, ConnectionError) as error:
        return ProbeResult(PROBE_UNREACHABLE, str(error))
