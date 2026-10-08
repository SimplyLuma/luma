"""Luma Connect long-lived streams: `luma-input/1` and `luma-files/1` (ADR-021).

Connection-per-request TLS is right for control messages but wrong for
high-rate traffic: a trackpad sends twenty batches a second and a 4 GiB file is
8 192 journaled base64 chunks. Both streams are authorized once by an ordinary
journaled request and then carried on one mutually authenticated TLS 1.3
connection to a single-use listener, exactly like `luma-media/1`
(`companion_media.MediaSession` supplies the listener, the 30 second window,
the eight failed-handshake limit, the wake pipe and the close semantics).

Wire format: docs/research/luma-connect-android-protocol.md section 5b.

`luma-input/1` (phone to desktop)
    1. Journaled `input.control` with payload exactly `{"stream": {}}`.
       Result `{"session": <32 hex>, "port": n}`.
    2. The phone connects with ALPN `luma-input/1` and sends `{"session"}`.
    3. Each further frame is an ordinary `input.control` batch `{"events": [...]}`,
       validated by `DesktopAdapters.input_events` (the same code, not a copy) and
       applied through the same injector, so lock-screen refusal is unchanged.
    4. The desktop sends `{"state": "needs-user"}` when the injector starts
       refusing and `{"state": "accepted"}` when it applies events again.
    5. Buttons and keys still held when the stream ends are released.
    Authorization is re-read at least every five seconds while streaming.

`luma-files/1` (either direction; the sender connects to the receiver)
    1. Journaled `files.write` with `{"stream": {"transfer", "name", "size", "sha256"}}`.
       The whole-file SHA-256 is known up front, so the sender reads the file
       twice (once to hash, once to send). For a 4 GiB file that is roughly 10-40 s
       of extra local I/O; in exchange the receiver never commits unverified data and
       a resume needs no per-chunk digests.
       Result `{"session", "port", "offset"}`; `offset` is where a persisted partial
       file ends, or 0.
    2. The sender connects with ALPN `luma-files/1`, sends `{"session", "offset"}`
       and then exactly `size - offset` raw bytes. The size delimits the data; a
       TLS half-close is not required (JDK, Conscrypt and Python differ in their
       close_notify handling, so the protocol does not depend on it).
    3. The receiver hashes the stored prefix, appends the bytes, verifies the
       whole-file digest, commits atomically and replies with one frame,
       `{"state": "complete", "name"}` or `{"state": "failed", "error"}`.
    Partial state lives in `transfers/<peer16>-<transfer>.part` with a 0600
    sidecar `transfers/<peer16>-<transfer>.json` written atomically. Partial
    files and sidecars older than seven days are removed when `FileStreams` starts.

Daemon wiring (not applied here; `daemon.py` and `companion_desktop.py` belong to
other owners). In `ConnectService._make_companion`, after the `CompanionService`
is constructed and before it is returned:

    from . import companion_streams as streams
    service = holder[0]
    addresses = lambda: service.listening_on
    inputs = streams.InputStreams(service.adapters, addresses=addresses) if injector else None
    files = streams.FileStreams(service.adapters, addresses=addresses)
    service.adapters.for_peer = streams.stream_adapters(service.adapters, inputs=inputs, files=files)

(`CompanionService._ensure_listener` reads `self.adapters.for_peer` whenever it
creates a listener, which happens after construction.) `_companion_removed` should
also call `inputs.close_peer(peer)` and `files.close_peer(peer)`, and the daemon's
shutdown should call `close_all()` on both before `self.companion.close()`.
Desktop-to-phone sending uses `send_file_stream`, falling back to
`companion_desktop.send_file` on `StreamUnsupported`; see that function.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import select
import socket
import ssl
import stat
import struct
import threading
import time

from . import companion, transport
from .companion import _unicast
from .companion_media import (HANDSHAKE_TIMEOUT, IDLE_TIMEOUT, PACKET_DEADLINE, MediaSession, MediaSessions,
                              StreamError, _shutdown)
from .policy import DIGEST, IDENTIFIER, Denied, Journal

INPUT_ALPN = 'luma-input/1'
FILES_ALPN = 'luma-files/1'
MAX_INPUT_FRAME = 64 * 1024     # 64 events and 1024 characters of text fit with ample margin
AUTH_RECHECK = 5                # seconds between grant re-reads on a streaming input session
MAX_STREAMS = 4                 # per registry, like MAX_SESSIONS for media
STALE_PART_AGE = 7 * 86400
IO_CHUNK = 1024 * 1024
SEND_CHUNK = 256 * 1024
REPLY_TIMEOUT = 120             # the receiver may still be flushing a large file to disk
UNSUPPORTED = frozenset({'invalid-request', 'unavailable'})
_PART = re.compile(r'[0-9a-f]{16}-[0-9a-f]{32}\.(part|json)\Z')
_FRAME = struct.Struct('!I')


class StreamUnsupported(Exception):
    """The receiver does not implement `luma-files/1`; use the chunked `files.write` path."""


def grant_authorizer(directory):
    """Default check for streams the desktop receives: link enabled, peer not revoked, incoming grant present."""
    directory = Path(directory)

    def authorized(peer, capability):
        journal = Journal(directory / 'continuity.db')
        try:
            if not journal.enabled():
                return False
            row = journal.db.execute('SELECT grants,revoked FROM peers WHERE fingerprint=?', (peer,)).fetchone()
            return bool(row) and not row[1] and capability in set(json.loads(row[0]))
        finally:
            journal.close()
    return authorized


class _Replay:
    """Presents one already-read frame to `transport.receive`, so parsing rules are shared, not copied."""

    def __init__(self, data):
        self._data, self._offset = memoryview(data), 0

    def recv(self, count):
        part = self._data[self._offset:self._offset + count]
        self._offset += len(part)
        return bytes(part)


# --------------------------------------------------------------------------- sessions

class StreamSession(MediaSession):
    """A single-use `luma-input/1` or `luma-files/1` session.

    Inherits the listener, accept window, failure limit and close handling of
    `MediaSession`; replaces the handshake's ALPN check and the stream body.
    """

    alpn = None
    capability = None

    def __init__(self, owner, peer, kind):
        super().__init__(owner, peer, kind, False)
        self.result = None

    def _handshake(self, raw, tls):
        stream = None
        timer = threading.Timer(HANDSHAKE_TIMEOUT, _shutdown, args=(raw,))
        timer.daemon = True
        try:
            raw.settimeout(HANDSHAKE_TIMEOUT)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            timer.start()
            stream = tls.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
            stream.do_handshake()
            actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
            if (stream.version() != 'TLSv1.3' or stream.selected_alpn_protocol() != self.alpn
                    or not hmac.compare_digest(actual, self.peer)):
                raise PermissionError('unapproved device or protocol')
            return stream
        except (OSError, ValueError, PermissionError):
            (stream or raw).close()
            return None
        finally:
            timer.cancel()

    def _serve(self, stream):
        timer = threading.Timer(HANDSHAKE_TIMEOUT, _shutdown, args=(stream,))
        timer.daemon = True
        try:
            try:
                if not self._owner.authorized(self.peer, self.capability):
                    raise StreamError('revoked')
                stream.settimeout(HANDSHAKE_TIMEOUT)
                timer.start()
                try:
                    header = transport.receive(stream)
                except (ValueError, EOFError, OSError):
                    raise StreamError('invalid-header') from None
                finally:
                    timer.cancel()
                self.header = self._validate_header(header)
            except StreamError as error:
                self._reply_failure(stream, error.code)
                self._fail(error.code)
                return None
            with self._lock:
                if self.state == 'waiting' and not self._stop.is_set():
                    self.state = 'streaming'
            self._owner._changed(self)
            try:
                self._body(stream)
            except StreamError as error:
                self._reply_failure(stream, error.code)
                self._fail(error.code)
        finally:
            timer.cancel()
        return None

    def _validate_header(self, header):
        raise NotImplementedError

    def _body(self, stream):
        raise NotImplementedError

    def _reply_failure(self, stream, code):
        pass

    def _send(self, stream, value):
        stream.settimeout(PACKET_DEADLINE)
        try:
            transport.send(stream, value)
        finally:
            stream.settimeout(1.0)


class InputSession(StreamSession):
    alpn = INPUT_ALPN
    capability = 'input.control'

    def __init__(self, owner, peer):
        super().__init__(owner, peer, 'input')
        self.frames = 0

    def _validate_header(self, header):
        if not isinstance(header, dict) or set(header) != {'session'}:
            raise StreamError('invalid-header')
        if not isinstance(header['session'], str) or not hmac.compare_digest(header['session'], self.id):
            raise StreamError('wrong-session')
        return header

    def _body(self, stream):
        held = {}
        try:
            self._pump(stream, held)
        finally:
            if held:
                releases = [{**event, 'pressed': False} for event in held.values()]
                try:
                    self._owner.apply(self.peer, {'events': releases[:companion.DesktopAdapters.MAX_INPUT_EVENTS]})
                except Exception:
                    pass  # the injector releases everything itself when its session ends

    def _pump(self, stream, held):
        stream.settimeout(1.0)
        idle = self._owner.idle_timeout
        last = checked = time.monotonic()
        refusing = False
        while not self._stop.is_set():
            if time.monotonic() - checked >= AUTH_RECHECK:
                if not self._owner.authorized(self.peer, self.capability):
                    raise StreamError('revoked')
                checked = time.monotonic()
            if not stream.pending():
                ready = select.select([stream, self._wake_r], [], [], 1.0)[0]
                if self._wake_r in ready:
                    self._drain_wake()
                if stream not in ready:
                    if idle is not None and time.monotonic() - last > idle:
                        raise StreamError('idle-timeout')
                    continue
            prefix = self._read(stream, _FRAME.size, first=True)
            if prefix is None:
                continue
            if prefix == b'':
                self._finish('closed')
                return
            size, = _FRAME.unpack(prefix)
            if not 0 < size <= MAX_INPUT_FRAME:
                raise StreamError('oversized-frame')
            body = self._read(stream, size)
            try:
                frame = transport.receive(_Replay(prefix + body))
            except (ValueError, EOFError, TimeoutError):
                raise StreamError('invalid-frame') from None
            last = time.monotonic()
            try:
                result = self._owner.apply(self.peer, frame)
            except (ValueError, KeyError, TypeError):
                raise StreamError('invalid-input') from None
            except PermissionError:
                raise StreamError('revoked') from None
            except Exception:
                raise StreamError('input-failed') from None
            self.frames += 1
            refused = isinstance(result, dict) and result.get('error') == 'needs-user'
            if refused:
                held.clear()  # a refusing injector has already closed its session and released input
            else:
                for event in frame['events']:
                    if event['type'] in ('button', 'key'):
                        key = (event['type'], event[event['type']])
                        if event['pressed']:
                            held[key] = dict(event)
                        else:
                            held.pop(key, None)
            if refused != refusing:
                refusing = refused
                try:
                    self._send(stream, {'state': 'needs-user' if refused else 'accepted'})
                except (OSError, ValueError):
                    raise StreamError('write-failed') from None
        self._finish('cancelled', 'cancelled')


class FileSession(StreamSession):
    alpn = FILES_ALPN
    capability = 'files.write'

    def __init__(self, owner, peer, offer, offset):
        super().__init__(owner, peer, 'files')
        self.offer, self.offset = dict(offer), offset
        self.received = 0

    def _validate_header(self, header):
        if not isinstance(header, dict) or set(header) != {'session', 'offset'}:
            raise StreamError('invalid-header')
        if not isinstance(header['session'], str) or not hmac.compare_digest(header['session'], self.id):
            raise StreamError('wrong-session')
        if type(header['offset']) is not int or header['offset'] != self.offset:
            raise StreamError('wrong-offset')
        return header

    def _reply_failure(self, stream, code):
        if code == 'cancelled' or self._stop.is_set():
            return
        try:
            self._send(stream, {'state': 'failed', 'error': code})
        except (OSError, ValueError):
            pass

    def _body(self, stream):
        owner, offer = self._owner, self.offer
        part, sidecar = owner.paths(self.peer, offer['transfer'])
        size, digest = offer['size'], hashlib.sha256()
        try:
            fd = os.open(part, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError:
            raise StreamError('storage') from None
        try:
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size < self.offset:
                    raise StreamError('partial-changed')
                os.ftruncate(fd, self.offset)
                position = 0
                while position < self.offset:
                    if self._stop.is_set():
                        raise StreamError('cancelled')
                    data = os.pread(fd, min(IO_CHUNK, self.offset - position), position)
                    if not data:
                        raise StreamError('partial-changed')
                    digest.update(data)
                    position += len(data)
                os.lseek(fd, self.offset, os.SEEK_SET)
                self._receive(stream, fd, digest, size - self.offset)
            finally:
                try:
                    os.fsync(fd)  # a resumed transfer must find what was acknowledged by TCP
                except OSError:
                    pass
        finally:
            os.close(fd)
        if not hmac.compare_digest(digest.hexdigest(), offer['sha256']):
            part.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            raise StreamError('digest-mismatch')
        try:
            name = owner.commit(self.peer, part, offer['name'])
        except (OSError, ValueError):
            raise StreamError('storage') from None
        sidecar.unlink(missing_ok=True)
        self.result = {'name': name}
        try:
            self._send(stream, {'state': 'complete', 'name': name})
        except (OSError, ValueError):
            pass  # committed and verified; the sender learns the outcome from the Downloads folder
        self._finish('closed')

    def _receive(self, stream, fd, digest, remaining):
        buffer = bytearray(min(IO_CHUNK, max(remaining, 1)))
        view = memoryview(buffer)
        stream.settimeout(1.0)
        idle = self._owner.idle_timeout
        last = time.monotonic()
        while remaining:
            if self._stop.is_set():
                raise StreamError('cancelled')
            try:
                count = stream.recv_into(view, min(len(buffer), remaining))
            except (TimeoutError, ssl.SSLWantReadError):
                if idle is not None and time.monotonic() - last > idle:
                    raise StreamError('idle-timeout') from None
                continue
            except (OSError, ssl.SSLError):
                raise StreamError('cancelled' if self._stop.is_set() else 'truncated') from None
            if not count:
                raise StreamError('truncated')
            chunk = view[:count]
            written = 0
            while written < count:
                try:
                    written += os.write(fd, chunk[written:])
                except OSError:
                    raise StreamError('storage') from None
            digest.update(chunk)
            remaining -= count
            self.received += count
            last = time.monotonic()


# --------------------------------------------------------------------------- registries

class _Streams(MediaSessions):
    """Session registry sharing `MediaSessions`' get/close/close_peer/close_all bookkeeping.

    `addresses` is a list of explicit local addresses or a callable returning one,
    so the registry follows the companion listener across network changes.
    """

    alpn = None

    def __init__(self, adapters, *, addresses, authorized=None, on_change=None, now=time.monotonic,
                 idle_timeout=IDLE_TIMEOUT):
        # MediaSessions.__init__ is not called: it requires a sink factory and fixed addresses.
        self.adapters = adapters
        self.directory = Path(adapters.directory)
        self._addresses = addresses if callable(addresses) else (lambda fixed=list(addresses): fixed)
        if idle_timeout is not None and (type(idle_timeout) not in (int, float) or idle_timeout <= 0):
            raise ValueError('invalid idle timeout')
        self.sink_factory, self.on_change, self.now = None, on_change, now
        self.authorized = authorized or grant_authorizer(self.directory)
        self.idle_timeout = idle_timeout
        self._sessions = {}
        self._lock = threading.Lock()

    @property
    def addresses(self):
        result = [_unicast(address) for address in self._addresses()][:4]
        if not result:
            raise Denied('a local network address is required')
        return result

    def _context(self, peer):
        tls = super()._context(peer)
        tls.set_alpn_protocols([self.alpn])
        return tls

    def _register(self, session, same):
        """Adds a waiting session, first stopping sessions for which `same(other)` is true."""
        with self._lock:
            replaced = [other for other in self._sessions.values() if same(other)]
            if len(self._sessions) - len(replaced) >= MAX_STREAMS:
                session._close_wake()
                raise Denied('too many streams')
            self._sessions[session.id] = session
        for other in replaced:
            other.close()
        for other in replaced:
            if not other.join(5):
                self._discard(session)
                raise Denied('previous stream did not stop')

    def _discard(self, session):
        with self._lock:
            self._sessions.pop(session.id, None)
        session._close_wake()

    def _listen(self, session):
        try:
            session._start(self.addresses, self._context(session.peer))
        except BaseException:
            self._discard(session)
            raise


class InputStreams(_Streams):
    """`luma-input/1` sessions. Events go through `adapters.input_events`, which validates and injects."""

    alpn = INPUT_ALPN

    def apply(self, peer, payload):
        return self.adapters.input_events(peer, payload)

    def open_session(self, peer_fingerprint, *, authorize=True):
        """Returns `(session_id, port)`. A newer stream from the same phone replaces the older one.

        `authorize=False` is for the journaled request path, where dispatch has already
        checked the grant inside its transaction.
        """
        if not isinstance(peer_fingerprint, str) or not DIGEST.fullmatch(peer_fingerprint):
            raise ValueError('invalid device fingerprint')
        if self.adapters.input is None:
            raise Denied('input is unavailable on this computer')
        if authorize and not self.authorized(peer_fingerprint, InputSession.capability):
            raise Denied('selected device or capability is no longer approved')
        session = InputSession(self, peer_fingerprint)
        self._register(session, lambda other: isinstance(other, InputSession) and other.peer == peer_fingerprint)
        self._listen(session)
        return session.id, session.port


class FileStreams(_Streams):
    """`luma-files/1` receiving side: partial files, resume offsets and atomic commit into Downloads."""

    alpn = FILES_ALPN

    def __init__(self, adapters, *, addresses, clock=time.time, **options):
        super().__init__(adapters, addresses=addresses, **options)
        self.clock = clock
        self.removed_stale = self.cleanup()

    @property
    def transfers(self):
        return self.directory / 'transfers'

    def paths(self, peer, transfer):
        base = f'{peer[:16]}-{transfer}'
        return self.transfers / (base + '.part'), self.transfers / (base + '.json')

    def cleanup(self):
        """Removes partial files and sidecars not modified for seven days. Returns the count removed."""
        removed = 0
        try:
            entries = list(os.scandir(self.transfers))
        except FileNotFoundError:
            return 0
        limit = self.clock() - STALE_PART_AGE
        for entry in entries:
            if not (_PART.fullmatch(entry.name) or entry.name.startswith('.sidecar-')):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)) and info.st_mtime < limit:
                    os.unlink(entry.path)
                    removed += 1
            except OSError:
                pass
        return removed

    @staticmethod
    def validate_offer(offer):
        """Same name and size rules as the chunked `files.write` adapter. Raises ValueError."""
        if not isinstance(offer, dict) or set(offer) != {'transfer', 'name', 'size', 'sha256'}:
            raise ValueError('invalid file stream')
        transfer, name, size, digest = offer['transfer'], offer['name'], offer['size'], offer['sha256']
        if (not isinstance(transfer, str) or not IDENTIFIER.fullmatch(transfer)
                or not isinstance(name, str) or not companion.NAME.fullmatch(name) or '/' in name or name in {'.', '..'}
                or type(size) is not int or not 0 <= size <= companion.DesktopAdapters.MAX_FILE
                or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
            raise ValueError('invalid file stream')
        return dict(offer)

    def _load(self, sidecar):
        try:
            info = sidecar.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return None
            return json.loads(sidecar.read_text())
        except (OSError, ValueError):
            return None

    def _write_sidecar(self, sidecar, record):
        temporary = self.transfers / ('.sidecar-' + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, sidecar)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def offer(self, peer_fingerprint, offer, *, authorize=True):
        """Validates an offer and opens its listener. Returns `{"session", "port", "offset"}`."""
        if not isinstance(peer_fingerprint, str) or not DIGEST.fullmatch(peer_fingerprint):
            raise ValueError('invalid device fingerprint')
        offer = self.validate_offer(offer)
        if authorize and not self.authorized(peer_fingerprint, FileSession.capability):
            raise Denied('selected device or capability is no longer approved')
        self.transfers.mkdir(mode=0o700, exist_ok=True)
        part, sidecar = self.paths(peer_fingerprint, offer['transfer'])
        session = FileSession(self, peer_fingerprint, offer, 0)
        # Registering first stops an older session for the same transfer before its partial file is read.
        self._register(session, lambda other: (isinstance(other, FileSession) and other.peer == peer_fingerprint
                                               and other.offer['transfer'] == offer['transfer']))
        try:
            record = {'peer': peer_fingerprint, **offer}
            offset = 0
            try:
                info = part.lstat()
                if stat.S_ISREG(info.st_mode) and self._load(sidecar) == record:
                    offset = min(info.st_size, offer['size'])
                else:
                    part.unlink()
            except FileNotFoundError:
                pass
            if offset == 0:
                self._write_sidecar(sidecar, record)
            session.offset = offset
        except BaseException:
            self._discard(session)
            raise
        self._listen(session)
        return {'session': session.id, 'port': session.port, 'offset': offset}

    def commit(self, peer, part, name):
        adapters = self.adapters
        # The chunked path holds the same lock while choosing a unique name, so the two never collide.
        with adapters._lock:
            adapters.downloads.mkdir(parents=True, exist_ok=True)
            destination = adapters._unique(adapters.downloads / name)
            os.replace(part, destination)
        if adapters.notify:
            device = adapters.registry.active().get(peer) or {}
            try:
                adapters.notify('file', peer, {'device': device.get('name', 'Phone'), 'path': str(destination),
                                               'name': destination.name})
            except Exception:
                pass
        return destination.name


# --------------------------------------------------------------------------- adapters

def stream_adapters(adapters, *, inputs=None, files=None):
    """Returns a replacement for `adapters.for_peer` that intercepts `stream` payloads.

    Only payloads with a `stream` key are handled here; every other payload,
    including batched `input.control` and chunked `files.write`, reaches the
    original adapter unchanged. A capability the original table lacks stays absent.
    """
    base = adapters.for_peer

    def for_peer(peer):
        table = dict(base(peer))
        if inputs is not None and 'input.control' in table:
            plain_input = table['input.control']

            def input_control(capability, payload):
                if 'stream' not in payload:
                    return plain_input(capability, payload)
                if set(payload) != {'stream'} or not isinstance(payload['stream'], dict) or payload['stream']:
                    raise ValueError('invalid input stream request')
                try:
                    session, port = inputs.open_session(peer, authorize=False)
                except Denied:
                    return {'error': 'busy'}
                return {'session': session, 'port': port}
            table['input.control'] = input_control
        if files is not None and 'files.write' in table:
            plain_file = table['files.write']

            def files_write(capability, payload):
                if 'stream' not in payload:
                    return plain_file(capability, payload)
                if set(payload) != {'stream'}:
                    raise ValueError('invalid file stream request')
                try:
                    return files.offer(peer, payload['stream'], authorize=False)
                except Denied:
                    return {'error': 'busy'}
            table['files.write'] = files_write
        return table
    return for_peer


# --------------------------------------------------------------------------- sending

def _client_context(directory, fingerprint):
    directory = Path(directory)
    tls = transport.context(directory / 'device.pem', directory / 'device.key',
                            directory / 'peers' / (fingerprint + '.pem'), server=False)
    tls.set_alpn_protocols([FILES_ALPN])
    return tls


def _handshake(raw, tls, timeout):
    """Client handshake bounded by `timeout`; unlike `local.bounded_stream`, the bound ends with the handshake."""
    stream = tls.wrap_socket(raw, server_side=False, do_handshake_on_connect=False)
    timer = threading.Timer(timeout, _shutdown, args=(stream,))
    timer.daemon = True
    try:
        stream.settimeout(timeout)
        timer.start()
        stream.do_handshake()
    except BaseException:
        stream.close()
        raise
    finally:
        timer.cancel()
    return stream


def push_file(directory, fingerprint, host, port, session, fd, size, offset, *, progress=lambda sent, size: None,
              cancel=lambda: False, timeout=20):
    """Sends bytes `offset..size` of `fd` on one `luma-files/1` session. Returns the receiver's result frame."""
    from .companion_desktop import Cancelled
    with socket.create_connection((str(host), port), timeout=timeout) as raw:
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with _handshake(raw, _client_context(directory, fingerprint), timeout) as stream:
            actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
            if (stream.version() != 'TLSv1.3' or stream.selected_alpn_protocol() != FILES_ALPN
                    or not hmac.compare_digest(actual, fingerprint)):
                raise PermissionError('unapproved device or protocol')
            stream.settimeout(REPLY_TIMEOUT)  # the receiver hashes a resumed prefix before reading
            transport.send(stream, {'session': session, 'offset': offset})
            position = offset
            while position < size:
                if cancel():
                    raise Cancelled()
                data = os.pread(fd, min(SEND_CHUNK, size - position), position)
                if not data:
                    raise ValueError('file changed while sending')
                stream.sendall(data)
                position += len(data)
                progress(position, size)
            if not stream.pending():
                select.select([stream], [], [], REPLY_TIMEOUT)
            return transport.receive(stream)


def send_file_stream(directory, fingerprint, path, progress=lambda sent, size: None, cancel=lambda: False, *,
                     transfer=None, call=companion.call, attempts=3, retry_delay=1.0):
    """Sends one regular file to a paired phone with `luma-files/1`.

    Hashes the file first, offers it with a journaled `files.write` `{"stream": ...}`
    request, then streams it. A dropped connection is resumed with a new offer for
    the same transfer, up to `attempts` offers. Raises `StreamUnsupported` when the
    phone answers `invalid-request` or `unavailable` (use `companion_desktop.send_file`),
    `companion_desktop.Cancelled` when `cancel()` becomes true, and `ValueError` or
    `PermissionError` when the phone refuses the file.

    `call(directory, fingerprint, capability, payload)` defaults to `companion.call`.
    """
    from .companion_desktop import Cancelled, _file_name
    transfer = transfer or secrets.token_hex(16)
    if not isinstance(transfer, str) or not IDENTIFIER.fullmatch(transfer):
        raise ValueError('invalid transfer')
    if not os.path.isabs(path):
        raise ValueError('absolute path required')
    name = _file_name(path)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > companion.DesktopAdapters.MAX_FILE:
            raise ValueError('a regular file up to 4 GiB is required')
        size, digest, position = info.st_size, hashlib.sha256(), 0
        while position < size:
            if cancel():
                raise Cancelled()
            data = os.pread(fd, min(IO_CHUNK, size - position), position)
            if not data:
                raise ValueError('file changed while sending')
            digest.update(data)
            position += len(data)
        after = os.fstat(fd)
        if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise ValueError('file changed while sending')
        payload = {'stream': {'transfer': transfer, 'name': name, 'size': size, 'sha256': digest.hexdigest()}}
        for attempt in range(attempts):
            if cancel():
                raise Cancelled()
            receipt = call(directory, fingerprint, 'files.write', payload)
            result = receipt.get('result') if isinstance(receipt, dict) else None
            if not isinstance(receipt, dict) or receipt.get('state') != 'complete' or not isinstance(result, dict):
                raise ValueError('the phone did not accept the file')
            error = result.get('error')
            if error in UNSUPPORTED:
                raise StreamUnsupported()
            if error == 'revoked':
                raise PermissionError('the phone no longer accepts files')
            if (error is not None or set(result) != {'session', 'port', 'offset'}
                    or not isinstance(result['session'], str) or not IDENTIFIER.fullmatch(result['session'])
                    or type(result['port']) is not int or not 1 <= result['port'] <= 65535
                    or type(result['offset']) is not int or not 0 <= result['offset'] <= size):
                raise ValueError('the phone did not accept the file')
            device = companion.Registry(directory).active().get(fingerprint)
            if not device or not device['host']:
                raise Denied('device has not been seen on this network')
            progress(result['offset'], size)
            try:
                reply = push_file(directory, fingerprint, device['host'], result['port'], result['session'], fd, size,
                                  result['offset'], progress=progress, cancel=cancel)
            except (OSError, EOFError, ssl.SSLError) as failure:
                if isinstance(failure, PermissionError) or attempt + 1 >= attempts:
                    raise
                time.sleep(retry_delay * (attempt + 1))
                continue
            if reply.get('state') == 'complete' and isinstance(reply.get('name'), str) and set(reply) == {'state', 'name'}:
                return {'transfer': transfer, 'name': reply['name'], 'size': size, 'offset': result['offset']}
            raise ValueError('the phone did not accept the file')
        raise ValueError('the phone did not accept the file')
    finally:
        os.close(fd)
