"""The X display an application sees, without the session's X cookie.

Every capsule used to be handed the session's Xauthority file. The cookie in
it is the whole of X security: whoever holds it can connect to the X server
from anywhere that reaches its socket, and a capsule shares the host's network
namespace, where the server's abstract socket lives. An application could keep
the cookie and use it at any time, with or without a window.

An application now gets a socket of its own, at a display number no server
uses, and no cookie. The launcher answers on that socket and passes each
connection to the session's X server, putting the cookie into the connection
setup itself. The application never holds it, so the server's own sockets
refuse it, and X is reachable only through the launcher, while it runs.

This does not separate X clients from each other: one X server still serves
them all, and a client that connects can do what X allows. Giving each
application an X server of its own needs a rootless Xwayland per application,
which the session's compositor does not offer yet; this socket is where one
would be attached.
"""
from __future__ import annotations

import os
import re
import socket
import struct
import threading
from pathlib import Path

from .capsule_secrets import pump

# The display number inside a capsule. No X server on the host uses it, so a
# client that tries the abstract socket first (libxcb before 1.17) is told
# nobody listens there and goes on to the socket file.
NUMBER = 724
SOCKETS = Path("/tmp/.X11-unix")
COOKIE = b"MIT-MAGIC-COOKIE-1"
_LOCAL_DISPLAY = re.compile(r"(?:unix)?:(\d+)(?:\.\d+)?")
_FAMILY_LOCAL, _FAMILY_WILD = 256, 65535


def session_display(value: str | None = None) -> int | None:
    """The number of the session's local X display, or None."""
    match = _LOCAL_DISPLAY.fullmatch(value if value is not None else os.environ.get("DISPLAY", ""))
    return int(match.group(1)) if match else None


def read_cookie(path: Path, number: int) -> bytes | None:
    """The MIT cookie for a local display from an Xauthority file."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    offset, fallback = 0, None
    try:
        while offset < len(data):
            family, = struct.unpack_from(">H", data, offset)
            offset += 2
            fields = []
            for _field in range(4):
                length, = struct.unpack_from(">H", data, offset)
                fields.append(data[offset + 2:offset + 2 + length])
                offset += 2 + length
            _address, display, name, value = fields
            if name != COOKIE or family not in (_FAMILY_LOCAL, _FAMILY_WILD):
                continue
            if display == str(number).encode():
                return value
            if not display:
                fallback = value
    except struct.error:
        return None
    return fallback


def authorised_setup(prefix: bytes, cookie: bytes | None) -> tuple[bytes, int] | None:
    """A connection setup carrying the cookie, and how much of the client's
    own authorisation to discard. None when the bytes are not a setup."""
    if len(prefix) != 12 or prefix[:1] not in (b"l", b"B"):
        return None
    order = "<" if prefix[:1] == b"l" else ">"
    major, minor, name_length, data_length = struct.unpack(order + "HHHH", prefix[2:10])
    discard = (name_length + 3) // 4 * 4 + (data_length + 3) // 4 * 4
    if discard > 4096:
        return None
    if cookie is None:
        return prefix[:1] + b"\0" + struct.pack(order + "HHHHH", major, minor, 0, 0, 0), discard
    pad = lambda value: value + b"\0" * (-len(value) % 4)  # noqa: E731
    return (prefix[:1] + b"\0" + struct.pack(order + "HHHHH", major, minor, len(COOKIE), len(cookie), 0)
            + pad(COOKIE) + pad(cookie)), discard


def _exactly(connection: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise OSError("closed during setup")
        data += chunk
    return data


class DisplayRelay:
    """The application's X socket: the session's X server, authorised here."""

    def __init__(self, listen: Path, server: Path, cookie: bytes | None):
        self.path, self.server, self.cookie = listen, server, cookie
        self._listener: socket.socket | None = None

    def start(self) -> None:
        self.path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        listener.listen(32)
        self._listener = listener
        threading.Thread(target=self._accept, args=(listener,), daemon=True, name="luma-capsule-display").start()

    def _accept(self, listener: socket.socket) -> None:
        while True:
            try:
                client, _address = listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True, name="luma-capsule-x11").start()

    def _serve(self, client: socket.socket) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(30)
            setup = authorised_setup(_exactly(client, 12), self.cookie)
            if setup is None:
                raise OSError("not an X connection")
            _exactly(client, setup[1])
            client.settimeout(None)
            server.connect(str(self.server))
            server.sendall(setup[0])
        except OSError:
            client.close()
            server.close()
            return
        done = [threading.Event(), threading.Event()]

        def carry(source: socket.socket, sink: socket.socket, mine: int) -> None:
            pump(source, sink)
            done[mine].set()
            if done[1 - mine].is_set():
                client.close()
                server.close()
        threading.Thread(target=carry, args=(server, client, 1), daemon=True, name="luma-capsule-x11-down").start()
        carry(client, server, 0)

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        self.path.unlink(missing_ok=True)


def relay_for_session(listen: Path, runtime: Path, display: str | None = None,
                      authority: str | None = None) -> DisplayRelay | None:
    """A relay to the session's X server, or None when there is no local one.

    ``display`` and ``authority`` name another server of this session instead:
    the one a hidden compositor keeps for a trial start."""
    number = session_display(display)
    if number is None or not (SOCKETS / f"X{number}").exists():
        return None
    cookie = None
    authority = os.environ.get("XAUTHORITY") if display is None else authority
    if authority:
        try:
            resolved = Path(authority).resolve(strict=True)
        except OSError:
            resolved = None
        # Only a file the session itself keeps in its runtime directory.
        if resolved is not None and resolved.is_file() and resolved.is_relative_to(runtime.resolve()):
            cookie = read_cookie(resolved, number)
    return DisplayRelay(listen, SOCKETS / f"X{number}", cookie)
