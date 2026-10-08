# SPDX-License-Identifier: Apache-2.0
"""A small in-process IMAP4rev1 server over TLS for agent tests.

It implements only what Charlie's background agent speaks: CAPABILITY, LOGIN,
AUTHENTICATE XOAUTH2, SELECT/EXAMINE, STATUS, UID SEARCH/FETCH/STORE, IDLE and
LOGOUT on a single INBOX. The certificate authority and server certificate are
generated per run with the ``openssl`` command; clients trust that authority
through an injected SSL context, never by disabling verification.
"""
from __future__ import annotations

from base64 import b64decode
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path
import re
import select
import socket
import ssl
import subprocess
import threading
import time


def make_certificates(directory: Path) -> tuple[Path, Path, Path]:
    """Create a throwaway CA and a localhost server certificate; returns (ca, cert, key)."""
    directory.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = directory / "ca.key", directory / "ca.pem"
    key, csr, cert = directory / "server.key", directory / "server.csr", directory / "server.pem"
    extensions = directory / "server.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid\n"
    )

    def run(*arguments: str) -> None:
        subprocess.run(["openssl", *arguments], check=True, capture_output=True)

    run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_cert), "-days", "2", "-subj", "/CN=Charlie Test CA",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "subjectKeyIdentifier=hash")
    run("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
        "-keyout", str(key), "-out", str(csr), "-subj", "/CN=localhost")
    run("x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key), "-CAcreateserial",
        "-out", str(cert), "-days", "2", "-extfile", str(extensions))
    return ca_cert, cert, key


def client_context(ca: Path):
    """A verifying client context factory that trusts only the test authority."""
    def factory() -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(ca))
    return factory


def make_message(uid: int, sender: str = "Ada Lovelace <ada@example.test>", subject: str | None = None,
                 to: str = "reader@example.test", body: str = "Hello from the test server.") -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject if subject is not None else f"Test message {uid}"
    message["Message-ID"] = f"<test-{uid}-{time.monotonic_ns()}@example.test>"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content(body)
    return message.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


class _Message:
    __slots__ = ("uid", "flags", "raw")

    def __init__(self, uid: int, raw: bytes, flags: set[str]) -> None:
        self.uid, self.raw, self.flags = uid, raw, flags


_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\([^)]*\)|\S+')


def _tokens(text: str) -> list[str]:
    return [token[1:-1].replace('\\"', '"').replace("\\\\", "\\") if token.startswith('"') else token
            for token in _TOKEN.findall(text)]


class FakeImapServer:
    def __init__(self, cert: Path, key: str | Path, *, username: str = "reader@example.test",
                 password: str = "correct horse", oauth_token: str = "", idle: bool = True,
                 uidvalidity: int = 7) -> None:
        self.username, self.password, self.oauth_token = username, password, oauth_token
        self.idle_supported = idle
        self.uidvalidity = uidvalidity
        self.uidnext = 1
        self.messages: list[_Message] = []
        self.lock = threading.RLock()
        self.logins = 0
        self.failed_logins = 0
        self.commands: list[str] = []
        self.idling: set["_Connection"] = set()
        self.connections: set["_Connection"] = set()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(cert), str(key))
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self._closing = False
        self._thread = threading.Thread(target=self._accept, name="fake-imap", daemon=True)

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> "FakeImapServer":
        self._thread.start()
        return self

    def close(self) -> None:
        self._closing = True
        try:
            self.listener.close()
        except OSError:
            pass
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            connection.close()

    def _accept(self) -> None:
        while not self._closing:
            try:
                raw, _address = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), name="fake-imap-connection", daemon=True).start()

    def _serve(self, raw: socket.socket) -> None:
        try:
            tls = self.context.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            raw.close()
            return
        connection = _Connection(self, tls)
        with self.lock:
            self.connections.add(connection)
        try:
            connection.run()
        finally:
            with self.lock:
                self.connections.discard(connection)
                self.idling.discard(connection)
            connection.close()

    # -- mailbox -----------------------------------------------------------------
    def capabilities(self) -> str:
        values = ["IMAP4rev1", "AUTH=PLAIN", "AUTH=XOAUTH2"]
        if self.idle_supported:
            values.append("IDLE")
        return " ".join(values)

    def deliver(self, *raws: bytes, flags: tuple[str, ...] = ()) -> list[int]:
        with self.lock:
            uids = []
            for raw in raws:
                self.messages.append(_Message(self.uidnext, raw, set(flags)))
                uids.append(self.uidnext)
                self.uidnext += 1
            exists = len(self.messages)
            for connection in list(self.idling):
                connection.known_exists = exists
                connection.push(f"* {exists} EXISTS\r\n* {len(raws)} RECENT\r\n")
        return uids

    def flags(self, uid: int) -> set[str]:
        with self.lock:
            return set(next(message.flags for message in self.messages if message.uid == uid))

    def rebuild(self, uidvalidity: int) -> None:
        """Renumber the mailbox under a new UIDVALIDITY, as a server rebuild would."""
        with self.lock:
            self.uidvalidity = uidvalidity
            for index, message in enumerate(self.messages, start=1):
                message.uid = index
            self.uidnext = len(self.messages) + 1

    def logged_in_connections(self) -> int:
        with self.lock:
            return sum(1 for connection in self.connections if connection.authenticated)

    def wait_for(self, predicate, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if predicate():
                    return True
            time.sleep(0.02)
        return False


class _Connection:
    def __init__(self, server: FakeImapServer, sock: ssl.SSLSocket) -> None:
        self.server, self.sock = server, sock
        self.buffer = b""
        self.authenticated = False
        self.selected = False
        self.readonly = False
        self.known_exists = 0
        self.closed = False
        self._pending: list[str] = []
        self._pending_lock = threading.Lock()

    # -- io ----------------------------------------------------------------------
    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            socket.socket.shutdown(self.sock, socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, text: str | bytes) -> None:
        self.sock.sendall(text.encode("utf-8") if isinstance(text, str) else text)

    def push(self, text: str) -> None:
        with self._pending_lock:
            self._pending.append(text)

    def _flush_pending(self) -> None:
        with self._pending_lock:
            pending, self._pending = self._pending, []
        for text in pending:
            self.send(text)

    def readline(self, *, idle: bool = False) -> bytes | None:
        while b"\r\n" not in self.buffer:
            if idle:
                self._flush_pending()
                if not self.sock.pending():
                    readable, _, _ = select.select([self.sock], [], [], 0.05)
                    if not readable:
                        continue
            try:
                chunk = self.sock.recv(65536)
            except (OSError, ssl.SSLError):
                return None
            if not chunk:
                return None
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\r\n", 1)
        return line

    # -- protocol ----------------------------------------------------------------------
    def run(self) -> None:
        self.send(f"* OK [CAPABILITY {self.server.capabilities()}] Fake IMAP ready\r\n")
        while not self.closed:
            line = self.readline()
            if line is None:
                return
            text = line.decode("utf-8", "replace")
            parts = text.split(" ", 2)
            if len(parts) < 2:
                self.send("* BAD missing command\r\n")
                continue
            tag, command = parts[0], parts[1].upper()
            arguments = parts[2] if len(parts) > 2 else ""
            with self.server.lock:
                self.server.commands.append(command if command != "UID" else f"UID {arguments.split(' ')[0].upper()}")
            handler = getattr(self, f"_command_{command.lower()}", None)
            if handler is None:
                self.send(f"{tag} BAD unknown command\r\n")
                continue
            if handler(tag, arguments) is False:
                return

    def _command_capability(self, tag: str, _arguments: str) -> None:
        self.send(f"* CAPABILITY {self.server.capabilities()}\r\n{tag} OK CAPABILITY completed\r\n")

    def _command_noop(self, tag: str, _arguments: str) -> None:
        self.send(f"{tag} OK NOOP completed\r\n")

    def _command_logout(self, tag: str, _arguments: str) -> bool:
        self.send(f"* BYE logging out\r\n{tag} OK LOGOUT completed\r\n")
        return False

    def _command_login(self, tag: str, arguments: str) -> None:
        tokens = _tokens(arguments)
        with self.server.lock:
            ok = len(tokens) == 2 and tokens[0] == self.server.username and tokens[1] == self.server.password
            if ok:
                self.server.logins += 1
            else:
                self.server.failed_logins += 1
        if ok:
            self.authenticated = True
            self.send(f"{tag} OK LOGIN completed\r\n")
        else:
            self.send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n")

    def _command_authenticate(self, tag: str, arguments: str) -> None:
        if arguments.strip().upper() != "XOAUTH2":
            self.send(f"{tag} NO unsupported mechanism\r\n")
            return
        self.send("+ \r\n")
        line = self.readline()
        if line is None:
            return
        try:
            decoded = b64decode(line).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        expected = f"user={self.server.username}\x01auth=Bearer {self.server.oauth_token}\x01\x01"
        with self.server.lock:
            ok = bool(self.server.oauth_token) and decoded == expected
            if ok:
                self.server.logins += 1
            else:
                self.server.failed_logins += 1
        if ok:
            self.authenticated = True
            self.send(f"{tag} OK AUTHENTICATE completed\r\n")
        else:
            self.send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n")

    def _require_auth(self, tag: str) -> bool:
        if not self.authenticated:
            self.send(f"{tag} NO not authenticated\r\n")
            return False
        return True

    def _select(self, tag: str, arguments: str, readonly: bool) -> None:
        if not self._require_auth(tag):
            return
        if _tokens(arguments)[:1] != ["INBOX"]:
            self.send(f"{tag} NO no such mailbox\r\n")
            return
        with self.server.lock:
            exists = len(self.server.messages)
            text = (f"* {exists} EXISTS\r\n* 0 RECENT\r\n* FLAGS (\\Seen \\Flagged)\r\n"
                    f"* OK [UIDVALIDITY {self.server.uidvalidity}] UIDs valid\r\n"
                    f"* OK [UIDNEXT {self.server.uidnext}] Predicted next UID\r\n")
        self.selected, self.readonly, self.known_exists = True, readonly, exists
        mode = "READ-ONLY" if readonly else "READ-WRITE"
        self.send(text + f"{tag} OK [{mode}] {'EXAMINE' if readonly else 'SELECT'} completed\r\n")

    def _command_select(self, tag: str, arguments: str) -> None:
        self._select(tag, arguments, False)

    def _command_examine(self, tag: str, arguments: str) -> None:
        self._select(tag, arguments, True)

    def _command_status(self, tag: str, arguments: str) -> None:
        if not self._require_auth(tag):
            return
        with self.server.lock:
            values = {
                "MESSAGES": len(self.server.messages),
                "UNSEEN": sum(1 for message in self.server.messages if "\\Seen" not in message.flags),
                "UIDNEXT": self.server.uidnext,
                "UIDVALIDITY": self.server.uidvalidity,
            }
        requested = re.findall(r"[A-Z]+", arguments.split("(", 1)[-1].upper())
        items = " ".join(f"{name} {values[name]}" for name in requested if name in values)
        self.send(f"* STATUS INBOX ({items})\r\n{tag} OK STATUS completed\r\n")

    def _uids(self, sequence: str) -> list[_Message]:
        messages = self.server.messages
        highest = messages[-1].uid if messages else 0
        chosen: dict[int, _Message] = {}
        by_uid = {message.uid: message for message in messages}
        for part in sequence.split(","):
            if ":" in part:
                start, end = part.split(":", 1)
                low = highest if start == "*" else int(start)
                high = highest if end == "*" else int(end)
                low, high = min(low, high), max(low, high)
                for uid in by_uid:
                    if low <= uid <= high:
                        chosen[uid] = by_uid[uid]
            else:
                uid = highest if part == "*" else int(part)
                if uid in by_uid:
                    chosen[uid] = by_uid[uid]
        return [chosen[uid] for uid in sorted(chosen)]

    def _command_uid(self, tag: str, arguments: str) -> None:
        if not self._require_auth(tag) or not self.selected:
            self.send(f"{tag} NO no mailbox selected\r\n")
            return
        sub, _, rest = arguments.partition(" ")
        sub = sub.upper()
        with self.server.lock:
            if sub == "SEARCH":
                tokens = rest.split()
                sequence = "1:*"
                unseen = False
                index = 0
                while index < len(tokens):
                    token = tokens[index].upper()
                    if token == "UID" and index + 1 < len(tokens):
                        sequence = tokens[index + 1]
                        index += 2
                        continue
                    if token == "UNSEEN":
                        unseen = True
                    index += 1
                found = [message.uid for message in self._uids(sequence)
                         if not unseen or "\\Seen" not in message.flags]
                self.send(f"* SEARCH{''.join(' ' + str(uid) for uid in found)}\r\n{tag} OK SEARCH completed\r\n")
            elif sub == "FETCH":
                sequence, _, items = rest.partition(" ")
                items = items.upper()
                output = b""
                for message in self._uids(sequence):
                    seq = self.server.messages.index(message) + 1
                    fields = [f"UID {message.uid}"]
                    if "FLAGS" in items:
                        fields.append(f"FLAGS ({' '.join(sorted(message.flags))})")
                    if "RFC822.SIZE" in items:
                        fields.append(f"RFC822.SIZE {len(message.raw)}")
                    head = f"* {seq} FETCH ({' '.join(fields)}".encode()
                    if "BODY.PEEK[HEADER]" in items:
                        header = message.raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                        head += f" BODY[HEADER] {{{len(header)}}}\r\n".encode() + header
                    elif "BODY.PEEK[]" in items or "BODY[]" in items:
                        head += f" BODY[] {{{len(message.raw)}}}\r\n".encode() + message.raw
                        if "BODY[]" in items.replace("BODY.PEEK[]", ""):
                            message.flags.add("\\Seen")
                    output += head + b")\r\n"
                self.send(output + f"{tag} OK FETCH completed\r\n".encode())
            elif sub == "STORE":
                if self.readonly:
                    self.send(f"{tag} NO mailbox is read-only\r\n")
                    return
                sequence, operation, flags = (rest.split(" ", 2) + ["", ""])[:3]
                names = set(re.findall(r"\\?[A-Za-z]+", flags))
                output = ""
                for message in self._uids(sequence):
                    if operation.upper().startswith("+FLAGS"):
                        message.flags |= names
                    elif operation.upper().startswith("-FLAGS"):
                        message.flags -= names
                    seq = self.server.messages.index(message) + 1
                    update = f"* {seq} FETCH (UID {message.uid} FLAGS ({' '.join(sorted(message.flags))}))\r\n"
                    if not operation.upper().endswith(".SILENT"):
                        output += update
                    for connection in self.server.idling:
                        if connection is not self:
                            connection.push(update)
                self.send(output + f"{tag} OK STORE completed\r\n")
            else:
                self.send(f"{tag} BAD unsupported UID command\r\n")

    def _command_idle(self, tag: str, _arguments: str) -> bool | None:
        if not self.server.idle_supported:
            self.send(f"{tag} BAD IDLE not supported\r\n")
            return
        self.send("+ idling\r\n")
        with self.server.lock:
            self.server.idling.add(self)
            exists = len(self.server.messages)
            if exists != self.known_exists:
                # Like real servers: report what changed since the last response.
                self.known_exists = exists
                self.push(f"* {exists} EXISTS\r\n")
        try:
            while True:
                line = self.readline(idle=True)
                if line is None:
                    return False
                if line.strip().upper() == b"DONE":
                    break
        finally:
            with self.server.lock:
                self.server.idling.discard(self)
        self._flush_pending()
        self.send(f"{tag} OK IDLE terminated\r\n")
