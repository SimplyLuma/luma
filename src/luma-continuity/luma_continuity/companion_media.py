"""Luma Connect media receiver: phone camera and screen video (`luma-media/1`, ADR-021).

The desktop opens a single-use listener, then asks the phone (through a journaled
`camera.stream` or `screen.view` request made by the daemon) to connect to it. The
listener accepts only the paired phone's exact certificate over TLS 1.3 with ALPN
`luma-media/1`. The first authenticated connection consumes the session whether or
not its header is valid; unused sessions expire 30 seconds after opening.

Wire format (docs/research/luma-connect-android-protocol.md section 5):

1. One JSON header frame (section 1 framing):
   `{"session","kind","codec":"h264","width","height","fps","rotation","audio"}`.
2. Binary packets: type (1 byte), pts_us (8 bytes BE), length (4 bytes BE), payload.
   Unknown types, oversized or truncated packets close the stream.
3. For screen sessions with control allowed, JSON control frames travel back to the
   phone on the same stream.

One thread owns each TLS stream: it reads packets and writes queued control frames,
because an OpenSSL connection must not be read and written from two threads at once.

Decoding is delegated to a sink with `configure(header)`, `codec_config(data)`,
`video(pts_us, data, key)`, `audio_config(data)`, `audio(pts_us, data)` and
`close()`. `GStreamerSink` is the production sink; tests supply recorders.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import queue
import re
import secrets
import select
import selectors
import socket
import ssl
import struct
import threading
import time

from . import transport
from .companion import _unicast
from .policy import DIGEST, Denied, Journal

MEDIA_ALPN = 'luma-media/1'
SESSION_TTL = 30
MAX_PACKET = 8 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_SESSIONS = 4
MAX_FAILED_HANDSHAKES = 8
HANDSHAKE_TIMEOUT = 20      # TLS handshake plus header frame, whole-phase bound
PACKET_DEADLINE = 20        # a started packet must complete within this time
IDLE_TIMEOUT = 120          # no packet at all for this long closes the session
CONTROL_QUEUE = 256
TOUCH_MAX = 10000
MAX_CONTROL_TEXT = 4096

KINDS = frozenset({'camera', 'screen'})
GRANTS = {'camera': 'camera.stream', 'screen': 'screen.view'}
CONTROL_GRANT = 'screen.control'
HEADER_FIELDS = frozenset({'session', 'kind', 'codec', 'width', 'height', 'fps', 'rotation', 'audio'})

PACKET = struct.Struct('!BQI')
CODEC_CONFIG, VIDEO_FRAME, VIDEO_KEY_FRAME, AUDIO_CONFIG, AUDIO_FRAME, END_OF_STREAM = 0x01, 0x02, 0x03, 0x04, 0x05, 0x7f
PACKET_TYPES = frozenset({CODEC_CONFIG, VIDEO_FRAME, VIDEO_KEY_FRAME, AUDIO_CONFIG, AUDIO_FRAME, END_OF_STREAM})


class StreamError(Exception):
    """Protocol violation on an authenticated stream; `code` is a short, user-safe reason."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


# Video codecs in the order the desktop prefers them when it can decode them. Fedora and
# therefore Luma ship no H.264 or H.265 decoder by default (noopenh264, ffmpeg-free), while
# VP8, VP9 and AV1 decode in software and usually through VA-API. Android's CDD requires a
# VP8 encoder, so VP8 is the codec every phone can send.
CODECS = ('av1', 'vp9', 'vp8', 'h264', 'h265')
CODEC_CAPS = {
    'av1': 'video/x-av1,stream-format=obu-stream,alignment=tu',
    'vp9': 'video/x-vp9',
    'vp8': 'video/x-vp8',
    'h264': 'video/x-h264,stream-format=byte-stream,alignment=au',
    'h265': 'video/x-h265,stream-format=byte-stream,alignment=au',
}
CODEC_PARSERS = {'av1': 'av1parse', 'vp9': 'vp9parse', 'vp8': None, 'h264': 'h264parse', 'h265': 'h265parse'}
CODEC_DECODERS = {
    'av1': ('vaav1dec', 'dav1ddec', 'av1dec'),
    'vp9': ('vavp9dec', 'vp9dec'),
    'vp8': ('vavp8dec', 'vp8dec'),
    'h264': ('vah264dec', 'openh264dec', 'avdec_h264'),
    'h265': ('vah265dec', 'avdec_h265'),
}


def decodable_codecs(available):
    """Codecs this desktop can decode, most preferred first. `available(factory)` checks an element."""
    return [codec for codec in CODECS if any(available(factory) for factory in CODEC_DECODERS[codec])
            and (CODEC_PARSERS[codec] is None or available(CODEC_PARSERS[codec]))]


def installed_codecs():
    """`decodable_codecs` against the installed GStreamer, or VP8 alone when GStreamer is unavailable."""
    try:
        Gst, _GLib = _gst()
    except Exception:
        return ['vp8']
    return decodable_codecs(lambda factory: Gst.ElementFactory.find(factory) is not None) or ['vp8']


def validate_header(header, session_id, kind, codecs=('h264',)):
    """Return the header unchanged or raise StreamError with a short code."""
    if not isinstance(header, dict) or set(header) != HEADER_FIELDS:
        raise StreamError('invalid-header')
    if not isinstance(header['session'], str) or not hmac.compare_digest(header['session'], session_id):
        raise StreamError('wrong-session')
    if header['kind'] != kind:
        raise StreamError('wrong-kind')
    if (header['codec'] not in codecs
            or not _integer(header['width'], 1, MAX_DIMENSION)
            or not _integer(header['height'], 1, MAX_DIMENSION)
            or not _integer(header['fps'], 1, 120)
            or type(header['rotation']) is not int or header['rotation'] not in (0, 90, 180, 270)
            or header['audio'] not in ('opus', None)):
        raise StreamError('invalid-header')
    return header


def validate_control(event):
    """Return a canonical control frame (contract section 5.5) or raise ValueError."""
    if not isinstance(event, dict):
        raise ValueError('invalid control event')
    kind = event.get('type')
    if kind == 'touch':
        if (set(event) != {'type', 'action', 'x', 'y'} or event['action'] not in ('down', 'move', 'up')
                or not _integer(event['x'], 0, TOUCH_MAX) or not _integer(event['y'], 0, TOUCH_MAX)):
            raise ValueError('invalid touch event')
        return {'type': 'touch', 'action': event['action'], 'x': event['x'], 'y': event['y']}
    if kind == 'text':
        if set(event) != {'type', 'text'} or not isinstance(event['text'], str) or not 0 < len(event['text']) <= MAX_CONTROL_TEXT:
            raise ValueError('invalid text event')
        return {'type': 'text', 'text': event['text']}
    if kind == 'key':
        if set(event) != {'type', 'key'} or event['key'] not in ('back', 'home', 'recents'):
            raise ValueError('invalid key event')
        return {'type': 'key', 'key': event['key']}
    raise ValueError('invalid control event')


def journal_authorizer(directory):
    """Default check: continuity enabled, peer not revoked, and the outgoing grant present."""
    directory = Path(directory)

    def authorized(peer, kind, control):
        journal = Journal(directory / 'continuity.db')
        try:
            if not journal.enabled():
                return False
            row = journal.db.execute('SELECT outgoing_grants,revoked FROM peers WHERE fingerprint=?', (peer,)).fetchone()
            if not row or row[1]:
                return False
            grants = set(json.loads(row[0]))
            return GRANTS[kind] in grants and (not control or CONTROL_GRANT in grants)
        finally:
            journal.close()
    return authorized


def _shutdown(sock):
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


class MediaSession:
    """One single-use media stream. Created by `MediaSessions.open_session`.

    `state` moves from `waiting` to `streaming`, then to `closed` (normal end),
    `failed`, `expired` or `cancelled`. `error` holds a short code when not closed.
    """

    def __init__(self, owner, peer, kind, control_allowed, codecs=('h264',)):
        self.id = secrets.token_hex(16)
        self.peer, self.kind, self.control_allowed = peer, kind, control_allowed
        self.codecs = tuple(codecs)
        self.created = owner.now()
        self.state, self.error, self.header, self.port = 'waiting', None, None, None
        self._owner = owner
        self._sockets = []
        self._stop = threading.Event()
        self._done = threading.Event()
        self._control = queue.Queue(CONTROL_QUEUE)
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._stream = None
        self._thread = None
        self._lock = threading.Lock()

    # -- public API -------------------------------------------------------

    def send_control(self, event):
        """Queue a control frame for the phone. Returns False if not streaming or the queue is full."""
        if self.kind != 'screen' or not self.control_allowed:
            raise Denied('control is not allowed for this session')
        frame = validate_control(event)
        if self.state != 'streaming':
            return False
        try:
            self._control.put_nowait(frame)
        except queue.Full:
            return False
        self._wake()
        return True

    def close(self):
        """End the session from the desktop. Safe to call from any thread, repeatedly."""
        self._stop.set()
        self._wake()
        stream = self._stream
        if stream is not None:
            _shutdown(stream)

    def join(self, timeout=None):
        return self._done.wait(timeout)

    @property
    def finished(self):
        return self._done.is_set()

    # -- listener ---------------------------------------------------------

    def _start(self, addresses, tls):
        port = 0
        try:
            for address in addresses:
                listener = socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET)
                self._sockets.append(listener)
                listener.bind((address, port))
                listener.listen(2)
                port = listener.getsockname()[1]
        except BaseException:
            self._close_listeners()
            self._close_wake()
            raise
        self.port = port
        self._thread = threading.Thread(target=self._run, args=(tls,), name='connect-media-' + self.kind, daemon=True)
        self._thread.start()

    def _wake(self):
        try:
            self._wake_w.send(b'\0')
        except OSError:
            pass  # buffer full (a wake is already pending) or session finished

    def _drain_wake(self):
        try:
            while self._wake_r.recv(256):
                pass
        except OSError:
            pass

    def _close_listeners(self):
        for listener in self._sockets:
            try:
                listener.close()
            except OSError:
                pass
        self._sockets = []

    def _close_wake(self):
        for sock in (self._wake_r, self._wake_w):
            try:
                sock.close()
            except OSError:
                pass

    def _finish(self, state, error=None):
        with self._lock:
            if self.state in ('waiting', 'streaming'):
                self.state, self.error = state, error

    def _run(self, tls):
        sink = None
        try:
            stream = self._accept(tls)
            if stream is None:
                return
            self._stream = stream
            try:
                sink = self._serve(stream)
            finally:
                self._stream = None
                try:
                    stream.close()
                except OSError:
                    pass
        except Exception:
            self._finish('failed', 'internal')
        finally:
            self._close_listeners()
            if sink is not None:
                try:
                    sink.close()
                except Exception:
                    pass
            self._close_wake()
            self._done.set()
            self._owner._finished(self)

    def _accept(self, tls):
        failures = 0
        with selectors.DefaultSelector() as selector:
            for listener in self._sockets:
                selector.register(listener, selectors.EVENT_READ)
            selector.register(self._wake_r, selectors.EVENT_READ)
            while True:
                if self._stop.is_set():
                    self._finish('cancelled', 'cancelled')
                    return None
                remaining = self.created + SESSION_TTL - self._owner.now()
                if remaining <= 0:
                    self._finish('expired', 'expired')
                    return None
                for key, _ in selector.select(timeout=min(.25, remaining)):
                    if key.fileobj is self._wake_r:
                        self._drain_wake()
                        continue
                    try:
                        raw, _remote = key.fileobj.accept()
                    except OSError:
                        continue
                    stream = self._handshake(raw, tls)
                    if stream is None:
                        failures += 1
                        if failures >= MAX_FAILED_HANDSHAKES:
                            self._finish('failed', 'too-many-attempts')
                            return None
                        continue
                    # The exact peer authenticated: the session is consumed now.
                    self._close_listeners()
                    return stream

    def _handshake(self, raw, tls):
        stream = None
        timer = threading.Timer(HANDSHAKE_TIMEOUT, _shutdown, args=(raw,))
        timer.daemon = True
        try:
            raw.settimeout(HANDSHAKE_TIMEOUT)
            timer.start()
            stream = tls.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
            stream.do_handshake()
            actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
            if (stream.version() != 'TLSv1.3' or stream.selected_alpn_protocol() != MEDIA_ALPN
                    or not hmac.compare_digest(actual, self.peer)):
                raise PermissionError('unapproved device or protocol')
            return stream
        except (OSError, ValueError, PermissionError):
            (stream or raw).close()
            return None
        finally:
            timer.cancel()

    # -- stream -----------------------------------------------------------

    def _serve(self, stream):
        """Validate the header, then pump packets. Returns the configured sink, if any."""
        timer = threading.Timer(HANDSHAKE_TIMEOUT, _shutdown, args=(stream,))
        timer.daemon = True
        try:
            if not self._owner.authorized(self.peer, self.kind, self.control_allowed):
                raise StreamError('revoked')
            stream.settimeout(HANDSHAKE_TIMEOUT)
            timer.start()
            try:
                header = transport.receive(stream)
            except (ValueError, EOFError, OSError):
                raise StreamError('invalid-header') from None
            finally:
                timer.cancel()
            self.header = validate_header(header, self.id, self.kind, self.codecs)
        except StreamError as error:
            self._fail(error.code)
            return None
        sink = None
        try:
            sink = self._owner.sink_factory(self)
            sink.configure(dict(self.header))
        except MediaUnavailable as error:
            self._fail(error.code)
            return sink
        except Exception:
            self._fail('sink-error')
            return sink
        with self._lock:
            if self.state == 'waiting' and not self._stop.is_set():
                self.state = 'streaming'
        self._owner._changed(self)
        try:
            self._pump(stream, sink)
        except StreamError as error:
            self._fail(error.code)
        return sink

    def _fail(self, code):
        if code == 'cancelled' or self._stop.is_set():
            self._finish('cancelled', 'cancelled')
        else:
            self._finish('failed', code)

    def _pump(self, stream, sink):
        stream.settimeout(1.0)
        idle = self._owner.idle_timeout
        last = time.monotonic()
        while not self._stop.is_set():
            self._flush(stream)
            if not stream.pending():
                ready = select.select([stream, self._wake_r], [], [], 1.0)[0]
                if self._wake_r in ready:
                    self._drain_wake()
                if stream not in ready:
                    if idle is not None and time.monotonic() - last > idle:
                        raise StreamError('idle-timeout')
                    continue
            prefix = self._read(stream, PACKET.size, first=True)
            if prefix is None:
                continue        # TLS record without application data yet
            if prefix == b'':
                self._finish('closed')
                return          # phone closed the stream at a packet boundary
            last = time.monotonic()
            kind, pts, length = PACKET.unpack(prefix)
            if kind not in PACKET_TYPES:
                raise StreamError('unknown-packet')
            if length > MAX_PACKET:
                raise StreamError('oversized-packet')
            if kind == END_OF_STREAM:
                if length:
                    raise StreamError('invalid-packet')
                self._finish('closed')
                return
            if not length:
                raise StreamError('invalid-packet')
            if kind in (AUDIO_CONFIG, AUDIO_FRAME) and self.header['audio'] is None:
                raise StreamError('unexpected-audio')
            payload = self._read(stream, length)
            try:
                if kind == CODEC_CONFIG:
                    sink.codec_config(payload)
                elif kind in (VIDEO_FRAME, VIDEO_KEY_FRAME):
                    sink.video(pts, payload, kind == VIDEO_KEY_FRAME)
                elif kind == AUDIO_CONFIG:
                    sink.audio_config(payload)
                else:
                    sink.audio(pts, payload)
            except Exception:
                raise StreamError('sink-error') from None
        self._finish('cancelled', 'cancelled')

    def _read(self, stream, count, *, first=False):
        """Read exactly `count` bytes. With `first`, a timeout before any byte returns None and EOF returns b''."""
        buffer = bytearray(count)
        view = memoryview(buffer)
        received = 0
        deadline = time.monotonic() + PACKET_DEADLINE
        while received < count:
            if self._stop.is_set():
                raise StreamError('cancelled')
            try:
                part = stream.recv_into(view[received:], count - received)
            except TimeoutError:
                if first and received == 0:
                    return None
                if time.monotonic() >= deadline:
                    raise StreamError('timeout') from None
                continue
            except ssl.SSLWantReadError:
                if first and received == 0:
                    return None
                continue
            except (OSError, ssl.SSLError):
                if self._stop.is_set():
                    raise StreamError('cancelled') from None
                raise StreamError('truncated') from None
            if not part:
                if first and received == 0:
                    return b''
                raise StreamError('truncated')
            received += part
        return bytes(buffer)

    def _flush(self, stream):
        if self._control.empty():
            return
        stream.settimeout(PACKET_DEADLINE)
        try:
            while True:
                try:
                    frame = self._control.get_nowait()
                except queue.Empty:
                    break
                transport.send(stream, frame)
        except (OSError, ValueError):
            raise StreamError('control-write-failed') from None
        finally:
            stream.settimeout(1.0)


class MediaSessions:
    """Registry of single-use media sessions on explicit local addresses.

    `sink_factory(session)` is called on the stream thread once the header is valid
    and must return a sink. `authorized(peer, kind, control)` defaults to the
    journal's outgoing grants and is checked when opening and again when the phone
    connects. `on_change(session)` is called when a session starts streaming or ends.
    """

    def __init__(self, directory, *, addresses, sink_factory, authorized=None, on_change=None,
                 now=time.monotonic, idle_timeout=IDLE_TIMEOUT):
        self.directory = Path(directory)
        self.addresses = [_unicast(address) for address in addresses][:4]
        if not self.addresses:
            raise ValueError('a local network address is required')
        if idle_timeout is not None and (type(idle_timeout) not in (int, float) or idle_timeout <= 0):
            raise ValueError('invalid idle timeout')
        self.sink_factory, self.on_change, self.now = sink_factory, on_change, now
        self.authorized = authorized or journal_authorizer(self.directory)
        self.idle_timeout = idle_timeout
        self._sessions = {}
        self._lock = threading.Lock()

    def _context(self, peer):
        certificate = self.directory / 'peers' / (peer + '.pem')
        if certificate.is_symlink() or not certificate.is_file():
            raise Denied('selected device is not paired')
        if not hmac.compare_digest(transport.fingerprint(certificate.read_text()), peer):
            raise Denied('stored device certificate does not match')
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.verify_mode = ssl.CERT_REQUIRED
        tls.minimum_version = tls.maximum_version = ssl.TLSVersion.TLSv1_3
        tls.verify_flags |= ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
        tls.load_cert_chain(self.directory / 'device.pem', self.directory / 'device.key')
        tls.load_verify_locations(cafile=str(certificate))
        tls.set_alpn_protocols([MEDIA_ALPN])
        tls.num_tickets = 0
        return tls

    def open_session(self, peer_fingerprint, kind, control_allowed, codecs=('h264',)):
        """Open a listener for one stream. Returns `(session_id, port)`. The phone's header must name one of `codecs`."""
        if (not isinstance(codecs, (list, tuple)) or not codecs or len(set(codecs)) != len(codecs)
                or not set(codecs) <= set(CODECS)):
            raise ValueError('invalid codec list')
        if not isinstance(peer_fingerprint, str) or not DIGEST.fullmatch(peer_fingerprint):
            raise ValueError('invalid device fingerprint')
        if kind not in KINDS:
            raise ValueError('invalid media kind')
        if type(control_allowed) is not bool or (control_allowed and kind != 'screen'):
            raise ValueError('control is only available for screen sessions')
        if not self.authorized(peer_fingerprint, kind, control_allowed):
            raise Denied('selected device or capability is no longer approved')
        tls = self._context(peer_fingerprint)
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise Denied('too many media sessions')
            session = MediaSession(self, peer_fingerprint, kind, control_allowed, codecs)
            self._sessions[session.id] = session
        try:
            session._start(self.addresses, tls)
        except BaseException:
            with self._lock:
                self._sessions.pop(session.id, None)
            raise
        return session.id, session.port

    def get(self, session_id):
        with self._lock:
            return self._sessions.get(session_id)

    def sessions(self):
        with self._lock:
            return list(self._sessions.values())

    def close(self, session_id):
        session = self.get(session_id)
        if session:
            session.close()

    def close_peer(self, peer_fingerprint):
        """Call when a companion is revoked or unpaired."""
        for session in self.sessions():
            if session.peer == peer_fingerprint:
                session.close()

    def close_all(self, timeout=5):
        sessions = self.sessions()
        for session in sessions:
            session.close()
        for session in sessions:
            session.join(timeout)

    def _changed(self, session):
        if self.on_change:
            try:
                self.on_change(session)
            except Exception:
                pass

    def _finished(self, session):
        with self._lock:
            self._sessions.pop(session.id, None)
        self._changed(session)


# ---------------------------------------------------------------------------
# GStreamer / PipeWire / GTK presentation. Not executable in the macOS test
# environment; every element and property below is UNVERIFIED on Luma until a
# packaged runtime test observes it.
#
# - pipewiresink `mode` (default/render/provide) and `stream-properties`
#   (GstStructure of PipeWire stream properties). In `provide` mode the stream is
#   a driver and does not autoconnect, so it appears as a source node for other
#   clients. Source: pipewire src/gst/gstpipewiresink.c (master 1cd56b06,
#   2026-09-13) https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/src/gst/gstpipewiresink.c
#   and https://gstreamer.freedesktop.org/documentation/pipewire/pipewiresink.html
# - `media.class=Video/Source` + `media.role=Camera` is what the camera portal and
#   PipeWire-aware applications enumerate as a camera.
#   https://docs.pipewire.org/page_man_pipewire-props_7.html
# - gtk4paintablesink (gst-plugins-rs video/gtk4, main 91640488): the `paintable`
#   property must be read on the GTK main thread; it returns NULL otherwise.
#   https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs/-/blob/main/video/gtk4/src/sink/imp.rs
#   https://gstreamer.freedesktop.org/documentation/gtk4/index.html
# - appsrc, h264parse, decodebin, openh264dec, avdec_h264, videoconvert, videoflip
#   (`video-direction`), opusparse, opusdec, audioconvert, audioresample, v4l2sink
#   (`device`): https://gstreamer.freedesktop.org/documentation/plugins_doc.html
# ---------------------------------------------------------------------------

H264_CAPS = 'video/x-h264,stream-format=byte-stream,alignment=au'
OPUS_CAPS = 'audio/x-opus'
ROTATION_DIRECTION = {0: None, 90: '90r', 180: '180', 270: '90l'}
V4L2_DEVICE = re.compile(r'/dev/video[0-9]{1,3}\Z')
_NAME_UNSAFE = re.compile(r'[\x00-\x1f\x7f]')


class MediaUnavailable(RuntimeError):
    """A required GStreamer element is not installed; `code` is user-safe."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def node_suffix(peer_fingerprint):
    """Stable 12-hex node suffix per phone that does not reveal the certificate pin."""
    return hashlib.sha256(b'luma-connect-media-node|' + peer_fingerprint.encode()).hexdigest()[:12]


def display_name(name):
    name = _NAME_UNSAFE.sub('', name if isinstance(name, str) else '').strip()[:64]
    return name or 'Phone'


def pipeline_plan(kind, header, *, device_name, peer, available, v4l2_device=None):
    """Describe the video and audio chains as `[(factory, {property: value}), ...]`.

    `available(factory)` reports whether an element exists. The plan is pure so its
    element choices and PipeWire properties are tested without GStreamer.
    """
    if kind not in KINDS:
        raise ValueError('invalid media kind')
    name, suffix = display_name(device_name), node_suffix(peer)
    codec = header['codec']
    if codec not in CODECS:
        raise ValueError('invalid codec')
    video = [('appsrc', {'name': 'video', 'caps': CODEC_CAPS[codec], 'format': 'time', 'is-live': True, 'do-timestamp': False})]
    parser = CODEC_PARSERS[codec]
    decoder = next((factory for factory in CODEC_DECODERS[codec] if available(factory)), None)
    # A missing parser surfaces as '<parser>-unavailable' when the element is created.
    if decoder is None and not available('decodebin'):
        raise MediaUnavailable(codec + '-decoder-unavailable')
    if parser:
        video.append((parser, {}))
    # decodebin picks hardware decoders by rank; the explicit decoder is the fallback when it is missing.
    video.append(('decodebin', {}) if available('decodebin') else (decoder, {}))
    video.append(('videoconvert', {}))
    direction = ROTATION_DIRECTION[header['rotation']]
    if direction:
        video.append(('videoflip', {'video-direction': direction}))
    if kind == 'camera':
        if available('pipewiresink'):
            video.append(('pipewiresink', {'mode': 'provide', 'client-name': 'Luma Connect', 'sync': False,
                                           'stream-properties': {'media.class': 'Video/Source', 'media.role': 'Camera',
                                                                 'node.name': 'luma-connect-camera-' + suffix,
                                                                 'node.description': name + ' Camera'}}))
        elif v4l2_device is not None and available('v4l2sink'):
            if not isinstance(v4l2_device, str) or not V4L2_DEVICE.fullmatch(v4l2_device):
                raise ValueError('invalid v4l2loopback device')
            video.append(('v4l2sink', {'device': v4l2_device, 'sync': False}))
        else:
            raise MediaUnavailable('camera-sink-unavailable')
    else:
        if not available('gtk4paintablesink'):
            raise MediaUnavailable('screen-sink-unavailable')
        video.append(('gtk4paintablesink', {'sync': False}))
    audio = None
    if header['audio'] == 'opus':
        missing = [factory for factory in ('opusparse', 'opusdec', 'audioconvert', 'audioresample', 'pipewiresink')
                   if not available(factory)]
        if missing:
            raise MediaUnavailable('audio-unavailable')
        if kind == 'camera':
            output = {'mode': 'provide', 'client-name': 'Luma Connect',
                      'stream-properties': {'media.class': 'Audio/Source',
                                            'node.name': 'luma-connect-microphone-' + suffix,
                                            'node.description': name + ' Microphone'}}
        else:
            output = {'client-name': 'Luma Connect',
                      'stream-properties': {'node.name': 'luma-connect-screen-audio-' + suffix,
                                            'node.description': name + ' Audio'}}
        audio = [('appsrc', {'name': 'audio', 'caps': OPUS_CAPS, 'format': 'time', 'is-live': True, 'do-timestamp': False}),
                 ('opusparse', {}), ('opusdec', {}), ('audioconvert', {}), ('audioresample', {}), ('pipewiresink', output)]
    return {'video': video, 'audio': audio}


def _gst():
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import GLib, Gst
    if not Gst.is_initialized():
        Gst.init(None)
    return Gst, GLib


def _on_main_thread(GLib, function, timeout=10):
    """Run `function` on the default GLib main context and wait for its result."""
    context = GLib.MainContext.default()
    if context.is_owner():
        return function()
    done, box = threading.Event(), {}

    def run():
        try:
            box['value'] = function()
        except BaseException as error:  # re-raised on the calling thread
            box['error'] = error
        finally:
            done.set()
        return False
    GLib.idle_add(run)
    if not done.wait(timeout):
        raise TimeoutError('GTK main loop did not respond')
    if 'error' in box:
        raise box['error']
    return box.get('value')


class GStreamerSink:
    """Decodes one media session into PipeWire (camera) or a GTK paintable (screen).

    For screen sessions `on_paintable(paintable)` is invoked on the GTK main thread;
    hand it to `ScreenViewer.set_paintable`. `on_error(code)` is invoked on the main
    context for pipeline errors; the owner should close the session.
    """

    def __init__(self, session, *, device_name, v4l2_device=None, on_paintable=None, on_error=None):
        self.session, self.device_name, self.v4l2_device = session, device_name, v4l2_device
        self.on_paintable, self.on_error = on_paintable, on_error
        self.pipeline = self._video = self._audio = None
        self._config = None
        self._base = {}
        self._Gst = None

    def configure(self, header):
        Gst, GLib = _gst()
        self._Gst = Gst
        plan = pipeline_plan(self.session.kind, header, device_name=self.device_name, peer=self.session.peer,
                             available=lambda factory: Gst.ElementFactory.find(factory) is not None,
                             v4l2_device=self.v4l2_device)
        if self.session.kind == 'screen':
            # gtk4paintablesink creates and hands out its paintable only on the main thread.
            _on_main_thread(GLib, lambda: self._build(Gst, GLib, plan))
        else:
            self._build(Gst, GLib, plan)

    def _element(self, Gst, factory, properties):
        element = Gst.ElementFactory.make(factory, properties.get('name'))
        if element is None:
            raise MediaUnavailable(factory + '-unavailable')
        for key, value in properties.items():
            if key == 'name':
                continue
            if key == 'caps':
                value = Gst.Caps.from_string(value)
            elif key == 'stream-properties':
                structure = Gst.Structure.new_empty('props')
                for field, text in value.items():
                    # Observed on Fedora 44 PyGObject: Gst.Structure has no item assignment; set_value takes a str as a string GValue.
                    structure.set_value(field, text)
                value = structure
            if key in ('format', 'mode', 'video-direction'):
                Gst.util_set_object_arg(element, key, value)
            else:
                element.set_property(key, value)
        return element

    def _chain(self, Gst, pipeline, steps):
        elements = [self._element(Gst, factory, properties) for factory, properties in steps]
        for element in elements:
            pipeline.add(element)
        for upstream, downstream in zip(elements, elements[1:]):
            if upstream.get_factory().get_name() == 'decodebin':
                def linked(_decodebin, pad, target=downstream):
                    sink_pad = target.get_static_pad('sink')
                    if not sink_pad.is_linked() and pad.query_caps(None).to_string().startswith('video/'):
                        pad.link(sink_pad)
                upstream.connect('pad-added', linked)
            elif not upstream.link(downstream):
                raise MediaUnavailable('pipeline-link-failed')
        return elements

    def _build(self, Gst, GLib, plan):
        pipeline = Gst.Pipeline.new('luma-connect-' + self.session.kind)
        video = self._chain(Gst, pipeline, plan['video'])
        self._video = video[0]
        if plan['audio']:
            self._audio = self._chain(Gst, pipeline, plan['audio'])[0]
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', lambda _bus, _message: self.on_error and self.on_error('pipeline-error'))
        self.pipeline = pipeline
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise MediaUnavailable('pipeline-start-failed')
        if self.session.kind == 'screen' and self.on_paintable:
            self.on_paintable(video[-1].get_property('paintable'))

    def _push(self, source, pts_us, data, *, delta=False, header=False):
        Gst = self._Gst
        if source is None:
            return
        buffer = Gst.Buffer.new_wrapped(data)
        if pts_us is not None:
            # Running time starts at each stream's first timestamp.
            base = self._base.setdefault(source.get_name(), pts_us)
            buffer.pts = max(0, pts_us - base) * 1000
        if delta:
            buffer.set_flags(Gst.BufferFlags.DELTA_UNIT)
        if header:
            buffer.set_flags(Gst.BufferFlags.HEADER)
        if source.emit('push-buffer', buffer) != Gst.FlowReturn.OK:
            raise RuntimeError('pipeline refused data')

    def codec_config(self, data):
        # Prepended to the next key frame so each access unit is decodable. Android's AV1
        # encoders hand out an AV1CodecConfigurationRecord (marker 1, version 1: first byte
        # 0x81); its OBUs follow the 4-byte header and are what an OBU stream expects.
        codec = (self.session.header or {}).get('codec')
        if codec in ('vp8', 'vp9'):
            # VP8/VP9 frames are self-describing; Android may still emit a vpcC record, which must not reach the decoder.
            return
        if codec == 'av1' and data and data[0] == 0x81:
            # Observed on Android 16: a bare 4-byte av1C with no config OBUs; every key frame
            # already carries its own sequence header, so there is nothing to prepend.
            data = data[4:]
            if not data:
                return
        self._config = data

    def video(self, pts_us, data, key):
        if key and self._config:
            data = self._config + data
            self._config = None
        self._push(self._video, pts_us, data, delta=not key)

    def audio_config(self, data):
        # Expected to be an RFC 7845 OpusHead packet; opusparse/opusdec read it in-band.
        self._push(self._audio, None, data, header=True)

    def audio(self, pts_us, data):
        self._push(self._audio, pts_us, data)

    def close(self):
        pipeline, Gst = self.pipeline, self._Gst
        self.pipeline = None
        if pipeline is None:
            return
        for source in (self._video, self._audio):
            if source is not None:
                source.emit('end-of-stream')
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_bus().remove_signal_watch()


def normalize_point(x, y, widget_width, widget_height, content_width, content_height, *, clamp=False):
    """Map a widget point to 0..10000 phone coordinates for contain-fit content.

    Returns None outside the letterboxed picture unless `clamp` (used while dragging).
    """
    if min(widget_width, widget_height, content_width, content_height) <= 0:
        return None
    scale = min(widget_width / content_width, widget_height / content_height)
    shown_width, shown_height = content_width * scale, content_height * scale
    left, top = (widget_width - shown_width) / 2, (widget_height - shown_height) / 2
    u, v = (x - left) / shown_width, (y - top) / shown_height
    if not clamp and not (0 <= u <= 1 and 0 <= v <= 1):
        return None
    u, v = min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
    return round(u * TOUCH_MAX), round(v * TOUCH_MAX)


class ScreenViewer:
    """Minimal GTK window showing a screen session and forwarding pointer input.

    Construct on the GTK main thread. Primary-button drags become touch down/move/up;
    Escape sends `back`. The Luma Connect application owns the surrounding UI.
    """

    def __init__(self, session, *, title, application=None):
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gdk, Gtk
        try:
            gi.require_version('Adw', '1')
            from gi.repository import Adw
        except (ImportError, ValueError):
            Adw = None
        self.session, self._Gdk = session, Gdk
        self._start = None
        if Adw is not None:
            self.window = Adw.ApplicationWindow(application=application) if application else Adw.Window()
        else:
            self.window = Gtk.ApplicationWindow(application=application) if application else Gtk.Window()
        self.window.set_title(display_name(title))
        self.window.set_default_size(420, 860)
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        if hasattr(self.picture, 'set_content_fit'):
            self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.update_property([Gtk.AccessibleProperty.LABEL], [display_name(title) + ' screen'])
        if Adw is not None:
            self.window.set_content(self.picture)
        else:
            self.window.set_child(self.picture)
        if session.control_allowed:
            drag = Gtk.GestureDrag()
            drag.set_button(Gdk.BUTTON_PRIMARY)
            drag.connect('drag-begin', self._begin)
            drag.connect('drag-update', self._update)
            drag.connect('drag-end', self._end)
            self.picture.add_controller(drag)
            keys = Gtk.EventControllerKey()
            keys.connect('key-pressed', self._key)
            self.window.add_controller(keys)
        self.window.connect('close-request', self._closed)

    def set_paintable(self, paintable):
        self.picture.set_paintable(paintable)

    def present(self):
        self.window.present()

    def _content_size(self):
        paintable = self.picture.get_paintable()
        if paintable is not None and paintable.get_intrinsic_width() > 0:
            return paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        header = self.session.header or {}
        width, height = header.get('width', 0), header.get('height', 0)
        return (height, width) if header.get('rotation') in (90, 270) else (width, height)

    def _touch(self, action, x, y, clamp):
        point = normalize_point(x, y, self.picture.get_width(), self.picture.get_height(), *self._content_size(), clamp=clamp)
        if point is None:
            return False
        try:
            self.session.send_control({'type': 'touch', 'action': action, 'x': point[0], 'y': point[1]})
        except (Denied, ValueError):
            return False
        return True

    def _begin(self, _gesture, x, y):
        self._start = (x, y) if self._touch('down', x, y, False) else None

    def _update(self, _gesture, dx, dy):
        if self._start:
            self._touch('move', self._start[0] + dx, self._start[1] + dy, True)

    def _end(self, _gesture, dx, dy):
        if self._start:
            self._touch('up', self._start[0] + dx, self._start[1] + dy, True)
            self._start = None

    def _key(self, _controller, keyval, _keycode, _state):
        if keyval == self._Gdk.KEY_Escape:
            try:
                self.session.send_control({'type': 'key', 'key': 'back'})
            except (Denied, ValueError):
                pass
            return True
        return False

    def _closed(self, _window):
        self.session.close()
        return False


def gtk_sink_factory(device_name, *, application=None, v4l2_device=None, on_viewer=None):
    """`MediaSessions` sink factory: PipeWire camera, or a `ScreenViewer` window for screens."""
    def factory(session):
        if session.kind == 'camera':
            return GStreamerSink(session, device_name=device_name, v4l2_device=v4l2_device)
        _Gst, GLib = _gst()

        def create():
            viewer = ScreenViewer(session, title=device_name, application=application)
            viewer.present()
            if on_viewer:
                on_viewer(viewer)
            return viewer
        viewer = _on_main_thread(GLib, create)

        def failed(_code):
            session.close()
        return GStreamerSink(session, device_name=device_name, on_paintable=viewer.set_paintable, on_error=failed)
    return factory
