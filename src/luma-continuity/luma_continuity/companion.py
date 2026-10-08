"""Luma Connect companion devices: pairing, receiving and calling (ADR-021).

A companion is a paired phone running another operating system, currently the
Luma Connect Android app. It uses the same `luma-continuity/1` framing, device
certificates, journal and directional grants as Luma-to-Luma pairing; only the
invitation changes. Instead of exchanging offer files, the desktop shows a QR
code carrying its full certificate pin and a single-use secret:

    luma-connect://pair?v=1&host=192.0.2.4&port=40123&pin=<sha256>&token=<32 hex>&name=Desk

Scanning it is the out-of-band full-fingerprint comparison: the phone refuses
any other certificate, and only this desktop can read the secret the phone
returns inside TLS. Both screens then show the same six-digit code.

Nothing here discovers peers automatically, grants a capability the desktop
owner did not select, or forwards arbitrary methods. Effects are supplied by
the owning daemon as narrow adapter callables.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import socket
import ssl
import threading
import time
from urllib.parse import quote, urlparse

from . import transport
from .local import PairedExchange, bounded_stream
from .pairing import _certificate, _persist_certificate
from .policy import CAPABILITIES, COMPANION_CAPABILITIES, DIGEST, IDENTIFIER, Denied, Journal
from .session import Receiver

SCHEMA = 'org.projectluma.companion-pairing/v1'
PAIRING_ALPN = 'luma-companion-pairing/1'
LISTEN_PORT = 47810
PAIRING_TTL = 600
PAIRING_ATTEMPTS = 5
NAME = re.compile(r'[^\x00-\x1f\x7f]{1,128}\Z')
REQUEST_FIELDS = {'schema', 'kind', 'token', 'certificate', 'pin', 'name', 'model', 'platform',
                  'listen_port', 'phone_to_desktop', 'desktop_to_phone'}


def sas(desktop_pin, phone_pin, epoch, token):
    digest = hashlib.sha256(f'luma-companion-sas/1|{desktop_pin}|{phone_pin}|{epoch}|{token}'.encode()).digest()
    return '%06d' % (int.from_bytes(digest[:4], 'big') % 1_000_000)


def _unicast(address):
    value = ipaddress.ip_address(address)
    if value.is_unspecified or value.is_multicast or value.is_loopback and not os.environ.get('LUMA_CONNECT_TEST_LOOPBACK'):
        raise ValueError('explicit unicast interface required')
    return str(value)


def _grants(value):
    if (not isinstance(value, list) or any(not isinstance(item, str) for item in value)
            or len(value) != len(set(value)) or not set(value) <= CAPABILITIES):
        raise ValueError('unsupported or duplicate permissions')
    return set(value)


class Registry:
    """Companion metadata beside the journal's peers. Grants stay in `peers`."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def _open(self):
        journal = Journal(self.directory / 'continuity.db')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS companion_devices(
            fingerprint TEXT PRIMARY KEY, name TEXT NOT NULL, model TEXT NOT NULL,
            platform TEXT NOT NULL, host TEXT, port INTEGER, paired_at INTEGER NOT NULL,
            seen_at INTEGER, status TEXT)''')
        return journal

    def record(self, journal, fingerprint, *, name, model, platform, host, port, now):
        journal.db.execute('''INSERT INTO companion_devices(fingerprint,name,model,platform,host,port,paired_at,seen_at,status)
            VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(fingerprint) DO UPDATE SET name=excluded.name,
            model=excluded.model, platform=excluded.platform, host=excluded.host, port=excluded.port,
            paired_at=excluded.paired_at, seen_at=excluded.seen_at, status=NULL''',
            (fingerprint, name, model, platform, host, port, int(now), int(now)))

    def devices(self):
        journal = self._open()
        try:
            rows = journal.db.execute('''SELECT c.fingerprint,c.name,c.model,c.platform,c.host,c.port,c.paired_at,
                c.seen_at,c.status,p.epoch,p.grants,p.outgoing_grants,p.revoked FROM companion_devices c
                JOIN peers p ON p.fingerprint=c.fingerprint ORDER BY c.paired_at''').fetchall()
        finally:
            journal.close()
        return [{'fingerprint': r[0], 'name': r[1], 'model': r[2], 'platform': r[3], 'host': r[4], 'port': r[5],
                 'paired_at': r[6], 'seen_at': r[7], 'status': json.loads(r[8]) if r[8] else None, 'epoch': r[9],
                 'incoming': json.loads(r[10]), 'outgoing': json.loads(r[11]), 'revoked': bool(r[12])} for r in rows]

    def active(self):
        return {row['fingerprint']: row for row in self.devices() if not row['revoked']}

    def seen(self, fingerprint, host, *, port=None, status=None, now=None):
        journal = self._open()
        try:
            journal.db.execute('BEGIN IMMEDIATE')
            fields, values = ['host=?', 'seen_at=?'], [_unicast(host), int(time.time() if now is None else now)]
            if port is not None:
                fields.append('port=?'); values.append(port)
            if status is not None:
                fields.append('status=?'); values.append(transport.encode(status).decode())
            journal.db.execute(f'UPDATE companion_devices SET {",".join(fields)} WHERE fingerprint=?', (*values, fingerprint))
            journal.db.execute('COMMIT')
        finally:
            if journal.db.in_transaction: journal.db.execute('ROLLBACK')
            journal.close()

    def revoke(self, fingerprint):
        journal = self._open()
        try: journal.revoke(fingerprint)
        finally: journal.close()

    def set_incoming(self, fingerprint, grants):
        journal = self._open()
        try:
            row = journal.db.execute('SELECT outgoing_grants FROM peers WHERE fingerprint=?', (fingerprint,)).fetchone()
            if row: journal.set_grants(fingerprint, set(grants), outgoing_grants=set(json.loads(row[0])))
        finally: journal.close()


class PairingSession:
    """One QR invitation. Single use, ten minutes, five wrong secrets at most."""

    def __init__(self, directory, *, name, addresses, phone_to_desktop, desktop_to_phone,
                 listen_port=LISTEN_PORT, now=time.time, on_paired=None):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('invalid desktop name')
        self.directory, self.name, self.now = Path(directory), name, now
        self.addresses = [_unicast(address) for address in addresses][:4]
        if not self.addresses: raise ValueError('a local network address is required')
        # Certificate rotation is part of every companion pairing (companion_rotation.py).
        self.phone_to_desktop = _grants(sorted(set(phone_to_desktop) | {'device.rotate'}))
        self.desktop_to_phone = _grants(sorted(set(desktop_to_phone) | {'device.rotate'}))
        if not (self.phone_to_desktop or self.desktop_to_phone): raise ValueError('no selected permissions')
        if type(listen_port) is not int or not 1024 <= listen_port <= 65535: raise ValueError('invalid listener port')
        self.listen_port, self.on_paired = listen_port, on_paired
        self.token = secrets.token_hex(16)
        self.pin = transport.fingerprint((self.directory / 'device.pem').read_text())
        self.created = int(now())
        self.attempts = 0
        self.state, self.result = 'waiting', None
        self.port = None
        self._stop = threading.Event()
        self._sockets, self._thread = [], None
        self._lock = threading.Lock()

    @property
    def expired(self):
        return self.now() >= self.created + PAIRING_TTL

    def uri(self):
        if self.port is None: raise RuntimeError('pairing session not started')
        hosts = ''.join('&host=' + quote(address, safe='') for address in self.addresses)
        return (f'luma-connect://pair?v=1{hosts}&port={self.port}&pin={self.pin}'
                f'&token={self.token}&name={quote(self.name, safe="")}')

    def _context(self):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = tls.maximum_version = ssl.TLSVersion.TLSv1_3
        tls.verify_mode = ssl.CERT_NONE  # the phone proves itself with the secret and later by mutual TLS
        tls.load_cert_chain(self.directory / 'device.pem', self.directory / 'device.key')
        tls.set_alpn_protocols([PAIRING_ALPN])
        tls.num_tickets = 0
        return tls

    def start(self):
        if self._thread is not None: raise RuntimeError('pairing session already started')
        port = 0
        try:
            for address in self.addresses:
                listener = socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET)
                self._sockets.append(listener)
                listener.bind((address, port)); listener.listen(2)
                port = listener.getsockname()[1]
        except BaseException:
            self.cancel(); raise
        self.port = port
        tls = self._context()
        def run():
            try:
                with selectors.DefaultSelector() as selector:
                    for listener in self._sockets: selector.register(listener, selectors.EVENT_READ)
                    while not self._stop.is_set() and self.state == 'waiting':
                        if self.expired:
                            self._finish('expired'); break
                        for key, _ in selector.select(timeout=.25):
                            try: raw, remote = key.fileobj.accept()
                            except OSError: continue
                            self._serve(raw, remote[0], tls)
                            if self.state != 'waiting': break
            except (OSError, ValueError):
                pass  # sockets closed by cancel()
            finally:
                self._close_sockets()
        self._thread = threading.Thread(target=run, name='connect-companion-pairing', daemon=True)
        self._thread.start()
        return self.uri()

    def _serve(self, raw, remote, tls):
        try:
            with raw, bounded_stream(raw, tls, server=True, timeout=10) as stream:
                if stream.version() != 'TLSv1.3' or stream.selected_alpn_protocol() != PAIRING_ALPN:
                    return
                request = transport.receive(stream)
                response = self.handle(request, remote)
                transport.send(stream, response)
        except (OSError, ValueError, EOFError, ssl.SSLError):
            pass

    def handle(self, request, remote):
        with self._lock:
            if self.state != 'waiting' or self.expired:
                return {'schema': SCHEMA, 'kind': 'rejected'}
            if set(request) != REQUEST_FIELDS or request.get('schema') != SCHEMA or request.get('kind') != 'request':
                return self._wrong()
            if not isinstance(request['token'], str) or not hmac.compare_digest(request['token'], self.token):
                return self._wrong()
            now = self.now()
            try:
                _certificate(request['certificate'], request['pin'], now)
                if hmac.compare_digest(request['pin'], self.pin): raise PermissionError('cannot pair a device with itself')
                for field in ('name', 'model'):
                    if not isinstance(request[field], str) or not NAME.fullmatch(request[field]): raise ValueError('invalid device name')
                if request['platform'] != 'android': raise ValueError('unsupported companion platform')
                port = request['listen_port']
                if type(port) is not int or not 1024 <= port <= 65535: raise ValueError('invalid device port')
                phone_to_desktop = _grants(request['phone_to_desktop']) & self.phone_to_desktop
                desktop_to_phone = _grants(request['desktop_to_phone']) & self.desktop_to_phone
                if not (phone_to_desktop or desktop_to_phone): raise PermissionError('no permissions in common')
                host = _unicast(remote)
            except (ValueError, PermissionError, KeyError, TypeError):
                self._finish('failed')
                return {'schema': SCHEMA, 'kind': 'rejected'}
            epoch = secrets.token_hex(16)
            registry = Registry(self.directory)
            journal = registry._open()
            try:
                journal.db.execute('BEGIN IMMEDIATE')
                journal._approve_locked(request['pin'], epoch, phone_to_desktop, account=None, outgoing_grants=desktop_to_phone)
                _persist_certificate(self.directory, request['pin'], request['certificate'])
                registry.record(journal, request['pin'], name=request['name'], model=request['model'],
                                platform='android', host=host, port=port, now=now)
                journal.db.execute('COMMIT')
            except BaseException:
                if journal.db.in_transaction: journal.db.execute('ROLLBACK')
                self._finish('failed')
                raise
            finally:
                journal.close()
            code = sas(self.pin, request['pin'], epoch, self.token)
            self.result = {'fingerprint': request['pin'], 'name': request['name'], 'model': request['model'],
                           'sas': code, 'incoming': sorted(phone_to_desktop), 'outgoing': sorted(desktop_to_phone)}
            self._finish('paired')
            return {'schema': SCHEMA, 'kind': 'accepted', 'epoch': epoch, 'name': self.name,
                    'listen_port': self.listen_port, 'phone_to_desktop': sorted(phone_to_desktop),
                    'desktop_to_phone': sorted(desktop_to_phone), 'sas': code}

    def _wrong(self):
        self.attempts += 1
        if self.attempts >= PAIRING_ATTEMPTS: self._finish('failed')
        return {'schema': SCHEMA, 'kind': 'rejected'}

    def _finish(self, state):
        self.state = state
        self._stop.set()
        if self.on_paired and state == 'paired':
            try: self.on_paired(self.result)
            except Exception: pass

    def _close_sockets(self):
        for listener in self._sockets:
            try: listener.close()
            except OSError: pass

    def cancel(self):
        if self.state == 'waiting': self.state = 'cancelled'
        self._stop.set()
        if self._thread: self._thread.join(timeout=11)
        else: self._close_sockets()

    def join(self, timeout=None):
        if self._thread: self._thread.join(timeout)


class CompanionListener:
    """Receives requests from every active companion on explicit local addresses."""

    def __init__(self, directory, *, addresses, adapter_factory, port=LISTEN_PORT, enabled=lambda: True, after=None):
        # `after(peer)` runs once the receipt is sent, outside the journal
        # transaction: adapters must not write the database while dispatch holds it.
        self.directory, self.adapter_factory, self.enabled = Path(directory), adapter_factory, enabled
        self.after = after
        self.addresses = [_unicast(address) for address in addresses]
        if type(port) is not int or not 0 <= port <= 65535: raise ValueError('invalid listener port')
        self.port = port
        self.registry = Registry(directory)
        self._stop = threading.Event()
        self._sockets, self._thread = [], None

    def _bundle(self, peers):
        path = self.directory / 'companion-peers.pem'
        temporary = self.directory / ('.companion-peers-' + secrets.token_hex(8))
        data = ''.join((self.directory / 'peers' / (pin + '.pem')).read_text() for pin in sorted(peers))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream: stream.write(data)
        os.replace(temporary, path)
        return path

    def start(self):
        if self._thread is not None: raise RuntimeError('listener already started')
        port = self.port
        try:
            for address in self.addresses:
                listener = socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET)
                self._sockets.append(listener)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((address, port)); listener.listen(4)
                port = listener.getsockname()[1]
        except BaseException:
            self.stop(); raise
        self.port = port
        def run():
            try:
                with selectors.DefaultSelector() as selector:
                    for listener in self._sockets: selector.register(listener, selectors.EVENT_READ)
                    while not self._stop.is_set():
                        for key, _ in selector.select(timeout=.25):
                            try: raw, remote = key.fileobj.accept()
                            except OSError: continue
                            self._serve(raw, remote[0])
            finally:
                self._close_sockets()
        self._thread = threading.Thread(target=run, name='connect-companion-listener', daemon=True)
        self._thread.start()
        return self.port

    def _serve(self, raw, remote):
        with raw:
            if not self.enabled():
                return
            peers = self.registry.active()
            if not peers:
                return
            try:
                tls = transport.context(self.directory / 'device.pem', self.directory / 'device.key',
                                        self._bundle(peers), server=True)
                with bounded_stream(raw, tls, server=True, timeout=5) as stream:
                    actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
                    if actual not in peers:
                        return
                    peer = transport.authenticate(stream, actual)
                    self.registry.seen(peer, remote)
                    journal = Journal(self.directory / 'continuity.db')
                    try: Receiver(journal, self.adapter_factory(peer)).serve_one(stream, peer)
                    finally: journal.close()
                    if self.after: self.after(peer)
            except (OSError, ValueError, EOFError, PermissionError, ssl.SSLError):
                pass

    def _close_sockets(self):
        for listener in self._sockets:
            try: listener.close()
            except OSError: pass

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=6)
        else: self._close_sockets()


def call(directory, fingerprint, capability, payload, *, lifetime=120, timeout=10, now=None):
    """Invoke `capability` on a paired companion. Never retries."""
    if capability not in CAPABILITIES: raise ValueError('unknown capability')
    device = Registry(directory).active().get(fingerprint)
    if not device: raise Denied('selected device is not paired')
    if not device['host'] or not device['port']: raise Denied('device has not been seen on this network')
    moment = int(time.time() if now is None else now)
    request = {'version': 1, 'epoch': device['epoch'], 'id': secrets.token_hex(16), 'account': None,
               'capability': capability, 'expires': moment + lifetime, 'payload': payload}
    return PairedExchange(directory, fingerprint, device['epoch'], device['host'], device['port'], timeout=timeout)(request)


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value): raise ValueError('invalid text')
    return value


class DesktopAdapters:
    """Validates companion payloads and hands them to narrow desktop effects.

    Every effect is injected so the owning daemon supplies the real GNOME
    Shell, notification and PipeWire owners, and tests supply recorders.
    """

    MAX_CLIPBOARD = 256 * 1024
    MAX_FILE = 4 * 1024 ** 3
    MAX_CHUNK = 512 * 1024

    MAX_INPUT_EVENTS = 64
    MAX_MOVE = 1024  # pixels of a standardized pointer per event
    MAX_SCROLL = 600  # Mutter smooth-axis units per event; 10 is one wheel step
    MAX_TEXT = 256
    MAX_BATCH_TEXT = 1024

    def __init__(self, directory, *, registry=None, downloads, notify=None, clipboard_set=None, clipboard_get=None,
                 media=None, ring=None, changed=None, input=None, dnd=None, approval=None):
        self.directory, self.downloads = Path(directory), Path(downloads)
        self.registry = registry or Registry(directory)
        self.notify, self.clipboard_set, self.clipboard_get = notify, clipboard_set, clipboard_get
        self.media, self.ring, self.changed = media, ring, changed or (lambda *_: None)
        self.input = input
        # dnd(peer, on) -> bool | None applies Do Not Disturb and returns the resulting state;
        # approval is a companion_approval.ApprovalBroker.
        self.dnd, self.approval = dnd, approval
        self._transfers = {}
        self._pending = {}
        self._lock = threading.Lock()

    def for_peer(self, peer):
        adapters = {
            'device.status': lambda capability, payload: self.status(peer, payload),
            'links.open': lambda capability, payload: self.link(peer, payload),
            'files.write': lambda capability, payload: self.file(peer, payload),
            'notifications.mirror': lambda capability, payload: self.notification(peer, payload),
        }
        if self.clipboard_set: adapters['clipboard.write'] = lambda capability, payload: self.clipboard(peer, payload)
        if self.clipboard_get: adapters['clipboard.read'] = lambda capability, payload: self.clipboard_read(payload)
        if self.media: adapters['media.mirror'] = lambda capability, payload: self.media_state(peer, payload)
        if self.ring: adapters['device.ring'] = lambda capability, payload: self.ring_desktop(payload)
        if self.input: adapters['input.control'] = lambda capability, payload: self.input_events(peer, payload)
        if self.dnd: adapters['dnd.set'] = lambda capability, payload: self.dnd_set(peer, payload)
        if self.approval: adapters['auth.response'] = lambda capability, payload: self.approval.respond(peer, payload)
        return adapters

    def dnd_set(self, peer, payload):
        """`dnd.set` {on: bool}. The owner suppresses the echo of a state it was just given."""
        if set(payload) != {'on'} or type(payload['on']) is not bool: raise ValueError('invalid do not disturb')
        state = self.dnd(peer, payload['on'])
        return {'on': payload['on'] if state is None else bool(state)}

    def status(self, peer, payload):
        allowed = {'battery', 'charging', 'network', 'listen_port', 'name'}
        if not set(payload) <= allowed: raise ValueError('invalid status')
        battery = payload.get('battery')
        if battery is not None and (type(battery) is not int or not 0 <= battery <= 100): raise ValueError('invalid battery')
        if 'charging' in payload and type(payload['charging']) is not bool: raise ValueError('invalid charging')
        if 'network' in payload and payload['network'] not in {'wifi', 'cellular', 'none', 'other'}: raise ValueError('invalid network')
        port = payload.get('listen_port')
        if port is not None and (type(port) is not int or not 1024 <= port <= 65535): raise ValueError('invalid port')
        if 'name' in payload and (not isinstance(payload['name'], str) or not NAME.fullmatch(payload['name'])): raise ValueError('invalid name')
        status = {key: payload[key] for key in ('battery', 'charging', 'network') if key in payload}
        # Dispatch holds the journal's write transaction; persist in after_request.
        with self._lock: self._pending[peer] = (port, status)
        return {'accepted': True}

    def after_request(self, peer):
        with self._lock: pending = self._pending.pop(peer, None)
        if pending is None: return
        device = self.registry.active().get(peer)
        if not device or not device['host']: return
        port, status = pending
        self.registry.seen(peer, device['host'], port=port, status=status)
        self.changed('status', peer)

    def clipboard(self, peer, payload):
        if set(payload) - {'text', 'sensitive'} or 'text' not in payload: raise ValueError('invalid clipboard')
        text = _text(payload['text'], self.MAX_CLIPBOARD, empty=True)
        sensitive = payload.get('sensitive', False)
        if type(sensitive) is not bool: raise ValueError('invalid clipboard')
        self.clipboard_set(text, sensitive)
        return {'accepted': True}

    def clipboard_read(self, payload):
        if payload: raise ValueError('invalid clipboard request')
        text = self.clipboard_get()
        if text is None: return {'text': None}
        return {'text': text[:self.MAX_CLIPBOARD]}

    def link(self, peer, payload):
        if set(payload) - {'url', 'title'} or 'url' not in payload: raise ValueError('invalid link')
        url = _text(payload['url'], 4096)
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc: raise ValueError('only web links are accepted')
        title = _text(payload.get('title', ''), 256, empty=True)
        device = self.registry.active().get(peer) or {}
        if self.notify:
            self.notify('link', peer, {'device': device.get('name', 'Phone'), 'url': url, 'title': title})
        return {'accepted': True}

    def file(self, peer, payload):
        fields = {'transfer', 'name', 'size', 'offset', 'data', 'final', 'sha256'}
        if set(payload) != fields: raise ValueError('invalid file chunk')
        transfer, name, size, offset = payload['transfer'], payload['name'], payload['size'], payload['offset']
        if (not isinstance(transfer, str) or not IDENTIFIER.fullmatch(transfer)
                or not isinstance(name, str) or not NAME.fullmatch(name) or '/' in name or name in {'.', '..'}
                or type(size) is not int or not 0 <= size <= self.MAX_FILE
                or type(offset) is not int or not 0 <= offset <= size
                or type(payload['final']) is not bool
                or not isinstance(payload['data'], str)
                or payload['sha256'] is not None and (not isinstance(payload['sha256'], str) or not DIGEST.fullmatch(payload['sha256']))):
            raise ValueError('invalid file chunk')
        chunk = base64.b64decode(payload['data'], validate=True)
        if len(chunk) > self.MAX_CHUNK or offset + len(chunk) > size: raise ValueError('invalid file chunk')
        partial = self.directory / 'transfers'
        partial.mkdir(mode=0o700, exist_ok=True)
        target = partial / f'{peer[:16]}-{transfer}.part'
        with self._lock:
            state = self._transfers.get((peer, transfer))
            if state is None:
                if offset != 0: raise ValueError('transfer must start at zero')
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                state = self._transfers[(peer, transfer)] = {'fd': fd, 'length': 0, 'digest': hashlib.sha256(), 'name': name, 'size': size}
            if state['name'] != name or state['size'] != size or offset != state['length']:
                raise ValueError('out-of-order file chunk')
            os.write(state['fd'], chunk)
            state['length'] += len(chunk); state['digest'].update(chunk)
            if not payload['final']:
                return {'received': state['length']}
            del self._transfers[(peer, transfer)]
            os.fsync(state['fd']); os.close(state['fd'])
            if state['length'] != size or payload['sha256'] is None or not hmac.compare_digest(state['digest'].hexdigest(), payload['sha256']):
                target.unlink(missing_ok=True)
                raise ValueError('file failed verification')
            self.downloads.mkdir(parents=True, exist_ok=True)
            destination = self._unique(self.downloads / name)
            os.replace(target, destination)
        device = self.registry.active().get(peer) or {}
        if self.notify:
            self.notify('file', peer, {'device': device.get('name', 'Phone'), 'path': str(destination), 'name': destination.name})
        return {'received': size, 'name': destination.name}

    @staticmethod
    def _unique(path):
        if not path.exists(): return path
        stem, suffix = path.stem, path.suffix
        for index in range(2, 10000):
            candidate = path.with_name(f'{stem} ({index}){suffix}')
            if not candidate.exists(): return candidate
        raise ValueError('too many files with the same name')

    def notification(self, peer, payload):
        op = payload.get('op')
        if op == 'clear':
            if set(payload) != {'op'}: raise ValueError('invalid notification')
        elif op == 'remove':
            if set(payload) != {'op', 'key'} or not isinstance(payload['key'], str) or not IDENTIFIER.fullmatch(payload['key']):
                raise ValueError('invalid notification')
        elif op == 'post':
            fields = {'op', 'key', 'app', 'package', 'title', 'text', 'when', 'actions', 'conversation', 'silent'}
            if set(payload) != fields or not isinstance(payload['key'], str) or not IDENTIFIER.fullmatch(payload['key']):
                raise ValueError('invalid notification')
            _text(payload['app'], 128); _text(payload['package'], 256)
            _text(payload['title'], 512, empty=True); _text(payload['text'], 4096, empty=True)
            if type(payload['when']) is not int or type(payload['silent']) is not bool: raise ValueError('invalid notification')
            actions = payload['actions']
            if not isinstance(actions, list) or len(actions) > 4: raise ValueError('invalid notification actions')
            for action in actions:
                if (not isinstance(action, dict) or set(action) != {'id', 'label', 'reply'}
                        or not isinstance(action['id'], str) or not IDENTIFIER.fullmatch(action['id'])
                        or not isinstance(action['label'], str) or not 0 < len(action['label']) <= 128
                        or type(action['reply']) is not bool):
                    raise ValueError('invalid notification action')
            conversation = payload['conversation']
            if conversation is not None:
                if not isinstance(conversation, list) or len(conversation) > 10: raise ValueError('invalid conversation')
                for message in conversation:
                    if (not isinstance(message, dict) or set(message) != {'sender', 'text', 'when'}
                            or not isinstance(message['sender'], str) or len(message['sender']) > 128
                            or not isinstance(message['text'], str) or len(message['text']) > 4096
                            or type(message['when']) is not int):
                        raise ValueError('invalid conversation')
        else:
            raise ValueError('invalid notification')
        device = self.registry.active().get(peer) or {}
        if self.notify:
            self.notify('notification', peer, {'device': device.get('name', 'Phone'), **payload})
        return {'accepted': True}

    def media_state(self, peer, payload):
        fields = {'state', 'title', 'artist', 'album', 'app', 'duration_ms', 'position_ms', 'actions'}
        if set(payload) != fields or payload['state'] not in {'playing', 'paused', 'stopped', 'none'}: raise ValueError('invalid media')
        for key, limit in (('title', 512), ('artist', 512), ('album', 512), ('app', 128)):
            _text(payload[key], limit, empty=True)
        for key in ('duration_ms', 'position_ms'):
            if payload[key] is not None and (type(payload[key]) is not int or payload[key] < 0): raise ValueError('invalid media')
        if (not isinstance(payload['actions'], list)
                or not set(payload['actions']) <= {'play', 'pause', 'next', 'previous', 'seek'}):
            raise ValueError('invalid media')
        self.media(peer, payload)
        return {'accepted': True}

    def ring_desktop(self, payload):
        if set(payload) - {'ring'} or type(payload.get('ring', True)) is not bool: raise ValueError('invalid ring')
        self.ring(payload.get('ring', True))
        return {'accepted': True}

    def input_events(self, peer, payload):
        """Validates a trackpad/keyboard batch; the injector owns the compositor session."""
        if set(payload) != {'events'} or not isinstance(payload['events'], list): raise ValueError('invalid input')
        events = payload['events']
        if not 0 < len(events) <= self.MAX_INPUT_EVENTS: raise ValueError('invalid input')
        text_total, checked = 0, []
        for event in events:
            if not isinstance(event, dict): raise ValueError('invalid input event')
            kind = event.get('type')
            if kind in {'move', 'scroll'}:
                bound = self.MAX_MOVE if kind == 'move' else self.MAX_SCROLL
                if (set(event) != {'type', 'dx', 'dy'} or any(type(event[axis]) is not int or abs(event[axis]) > bound
                                                              for axis in ('dx', 'dy'))):
                    raise ValueError('invalid pointer event')
            elif kind == 'button':
                if (set(event) != {'type', 'button', 'pressed'} or event['button'] not in {'left', 'right', 'middle'}
                        or type(event['pressed']) is not bool):
                    raise ValueError('invalid button event')
            elif kind == 'key':
                if set(event) != {'type', 'key', 'pressed'} or type(event['pressed']) is not bool or not input_key(event['key']):
                    raise ValueError('invalid key event')
            elif kind == 'text':
                if set(event) != {'type', 'text'} or not isinstance(event['text'], str) or not 0 < len(event['text']) <= self.MAX_TEXT:
                    raise ValueError('invalid text event')
                if any(not _printable(character) and character not in '\n\t' for character in event['text']):
                    raise ValueError('invalid text event')
                text_total += len(event['text'])
                if text_total > self.MAX_BATCH_TEXT: raise ValueError('invalid text event')
            else:
                raise ValueError('invalid input event')
            checked.append(dict(event))
        # An injector returns False when it will not act now (for example, the
        # screen is locked); the person has to be at the computer first.
        if self.input(peer, checked) is False: return {'error': 'needs-user'}
        return {'accepted': True}


# Named keys accepted by `input.control`, besides one printable character.
INPUT_KEYS = frozenset({'enter', 'backspace', 'tab', 'escape', 'delete', 'insert', 'space', 'left', 'right', 'up',
                        'down', 'home', 'end', 'page_up', 'page_down', 'shift', 'control', 'alt', 'super',
                        'caps_lock', 'menu', *(f'f{number}' for number in range(1, 13))})


def _printable(character):
    import unicodedata
    return unicodedata.category(character)[0] not in {'C', 'Z'} or character == ' '


def input_key(value):
    return isinstance(value, str) and (value in INPUT_KEYS or len(value) == 1 and _printable(value) and value != ' ')
