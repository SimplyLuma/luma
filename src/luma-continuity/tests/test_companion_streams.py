"""Synthetic tests for `luma-input/1` and `luma-files/1`. Real TLS over loopback only."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import tempfile
import threading
import time
import unittest

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity import transport
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, Registry
from luma_continuity.companion_desktop import Cancelled, send_file
from luma_continuity.companion_streams import (FILES_ALPN, INPUT_ALPN, MAX_INPUT_FRAME, STALE_PART_AGE, FileStreams,
                                               InputStreams, StreamUnsupported, push_file, send_file_stream,
                                               stream_adapters)
from luma_continuity.local import PairedExchange
from luma_continuity.policy import Denied, Journal


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return predicate()


class Injector:
    def __init__(self):
        self.batches, self.locked = [], False

    def __call__(self, peer, events):
        if self.locked:
            return False
        self.batches.append(events)
        return True

    @property
    def events(self):
        return [event for batch in self.batches for event in batch]


def pair(desktop, phone, phone_to_desktop, desktop_to_phone, port):
    """Pairs `phone` with `desktop` and mirrors the phone-side approval, as test_companion does."""
    session = PairingSession(desktop, name='Desk', addresses=['127.0.0.1'], listen_port=port,
                             phone_to_desktop=phone_to_desktop, desktop_to_phone=desktop_to_phone)
    request = {'schema': 'org.projectluma.companion-pairing/v1', 'kind': 'request', 'token': session.token,
               'certificate': (phone / 'device.pem').read_text(),
               'pin': transport.fingerprint((phone / 'device.pem').read_text()),
               'name': 'Pixel 9', 'model': 'Google Pixel 9', 'platform': 'android', 'listen_port': 47811,
               'phone_to_desktop': phone_to_desktop, 'desktop_to_phone': desktop_to_phone}
    response = session.handle(request, '127.0.0.1')
    assert response['kind'] == 'accepted', response
    (phone / 'peers').mkdir(mode=0o700, exist_ok=True)
    (phone / 'peers' / (session.pin + '.pem')).write_text((desktop / 'device.pem').read_text())
    journal = Journal(phone / 'continuity.db')
    try:
        journal.approve(session.pin, response['epoch'], set(response['desktop_to_phone']),
                        outgoing_grants=set(response['phone_to_desktop']))
    finally:
        journal.close()
    return session.pin, request['pin'], response['epoch']


class StreamTestCase(unittest.TestCase):
    PHONE_TO_DESKTOP = ['input.control', 'files.write', 'clipboard.write']

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.desktop, self.phone, self.stranger = self.root / 'desktop', self.root / 'phone', self.root / 'stranger'
        for directory in (self.desktop, self.phone, self.stranger):
            create_identity(directory)
        self.injector = Injector()
        self.notified = []
        self.adapters = DesktopAdapters(self.desktop, downloads=self.root / 'Downloads', input=self.injector,
                                        notify=lambda kind, peer, value: self.notified.append((kind, value)),
                                        clipboard_set=lambda text, sensitive: None)
        self.inputs = InputStreams(self.adapters, addresses=['127.0.0.1'])
        self.files = FileStreams(self.adapters, addresses=lambda: ('127.0.0.1',))
        self.addCleanup(self.inputs.close_all)
        self.addCleanup(self.files.close_all)
        self.listener = CompanionListener(self.desktop, addresses=['127.0.0.1'], port=0,
                                          adapter_factory=stream_adapters(self.adapters, inputs=self.inputs, files=self.files))
        port = self.listener.start()
        self.addCleanup(self.listener.stop)
        self.desktop_pin, self.phone_pin, self.epoch = pair(self.desktop, self.phone, self.PHONE_TO_DESKTOP,
                                                            ['files.write'], port)
        self.port = port

    def phone_call(self, capability, payload):
        request = {'version': 1, 'epoch': self.epoch, 'id': secrets.token_hex(16), 'account': None,
                   'capability': capability, 'expires': int(time.time()) + 60, 'payload': payload}
        return PairedExchange(self.phone, self.desktop_pin, self.epoch, '127.0.0.1', self.port)(request)

    def connect(self, port, alpn, identity=None):
        identity = identity or self.phone
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.check_hostname = False
        tls.verify_mode = ssl.CERT_REQUIRED
        tls.minimum_version = ssl.TLSVersion.TLSv1_3
        tls.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        tls.load_cert_chain(identity / 'device.pem', identity / 'device.key')
        tls.load_verify_locations(cafile=str(self.desktop / 'device.pem'))
        tls.set_alpn_protocols([alpn])
        raw = socket.create_connection(('127.0.0.1', port), timeout=5)
        try:
            stream = tls.wrap_socket(raw)
        except BaseException:
            raw.close()
            raise
        return stream

    def closed_by_desktop(self, stream):
        try:
            stream.settimeout(5)
            while stream.recv(4096):
                pass
        except (OSError, ssl.SSLError):
            pass
        return True


class InputStreamTest(StreamTestCase):
    def open_input(self):
        receipt = self.phone_call('input.control', {'stream': {}})
        self.assertEqual(receipt['state'], 'complete')
        result = receipt['result']
        self.assertEqual(set(result), {'session', 'port'})
        return self.inputs.get(result['session']), result['port']

    def test_events_are_validated_and_injected_in_order(self):
        session, port = self.open_input()
        with self.connect(port, INPUT_ALPN) as stream:
            transport.send(stream, {'session': session.id})
            transport.send(stream, {'events': [{'type': 'move', 'dx': 5, 'dy': -3}]})
            self.assertTrue(wait_for(lambda: session.frames == 1))
            self.assertNotEqual(session._stream.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY), 0)
            transport.send(stream, {'events': [{'type': 'button', 'button': 'left', 'pressed': True},
                                               {'type': 'button', 'button': 'left', 'pressed': False},
                                               {'type': 'text', 'text': 'hi'}]})
            self.assertTrue(wait_for(lambda: session.frames == 2))
        self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('closed', None))
        self.assertEqual(self.injector.batches, [[{'type': 'move', 'dx': 5, 'dy': -3}],
                                                 [{'type': 'button', 'button': 'left', 'pressed': True},
                                                  {'type': 'button', 'button': 'left', 'pressed': False},
                                                  {'type': 'text', 'text': 'hi'}]])

    def test_batched_input_control_still_works(self):
        receipt = self.phone_call('input.control', {'events': [{'type': 'scroll', 'dx': 0, 'dy': 10}]})
        self.assertEqual(receipt['result'], {'accepted': True})
        self.assertEqual(self.injector.events, [{'type': 'scroll', 'dx': 0, 'dy': 10}])
        self.assertEqual(self.phone_call('input.control', {'stream': {'extra': 1}})['result'], {'error': 'invalid-request'})
        self.assertEqual(self.phone_call('input.control', {'stream': {}, 'events': []})['result'], {'error': 'invalid-request'})

    def test_input_validation_is_reused_and_closes_stream(self):
        # Out of the input.control bounds (MAX_MOVE), exactly as the batched adapter rejects it.
        self.assertEqual(self.phone_call('input.control', {'events': [{'type': 'move', 'dx': 5000, 'dy': 0}]})['result'],
                         {'error': 'invalid-request'})
        session, port = self.open_input()
        with self.connect(port, INPUT_ALPN) as stream:
            transport.send(stream, {'session': session.id})
            transport.send(stream, {'events': [{'type': 'move', 'dx': 5000, 'dy': 0}]})
            self.closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('failed', 'invalid-input'))
        self.assertEqual(self.injector.batches, [])
        for frame in ({'events': []}, {'events': [{'type': 'key', 'key': 'launch_rockets', 'pressed': True}]},
                      {'events': [{'type': 'text', 'text': 'x' * 257}]}, {'moves': []}):
            session, port = self.open_input()
            with self.connect(port, INPUT_ALPN) as stream:
                transport.send(stream, {'session': session.id})
                transport.send(stream, frame)
                self.closed_by_desktop(stream)
            self.assertTrue(session.join(5))
            self.assertEqual(session.error, 'invalid-input', frame)
        session, port = self.open_input()
        with self.connect(port, INPUT_ALPN) as stream:
            transport.send(stream, {'session': session.id})
            stream.sendall((MAX_INPUT_FRAME + 1).to_bytes(4, 'big'))
            self.closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual(session.error, 'oversized-frame')

    def test_lock_screen_refusal_is_reported_and_held_buttons_are_released(self):
        session, port = self.open_input()
        with self.connect(port, INPUT_ALPN) as stream:
            transport.send(stream, {'session': session.id})
            transport.send(stream, {'events': [{'type': 'button', 'button': 'left', 'pressed': True}]})
            self.assertTrue(wait_for(lambda: session.frames == 1))
            self.injector.locked = True
            transport.send(stream, {'events': [{'type': 'text', 'text': 'password'}]})
            self.assertEqual(transport.receive(stream), {'state': 'needs-user'})
            self.injector.locked = False
            transport.send(stream, {'events': [{'type': 'key', 'key': 'shift', 'pressed': True}]})
            self.assertEqual(transport.receive(stream), {'state': 'accepted'})
        self.assertTrue(session.join(5))
        self.assertNotIn({'type': 'text', 'text': 'password'}, self.injector.events)
        # The button was released by the refusing injector; the shift key is released when the stream ends.
        self.assertEqual(self.injector.batches[-1], [{'type': 'key', 'key': 'shift', 'pressed': False}])

    def test_wrong_pin_and_protocol_do_not_consume_session(self):
        session, port = self.open_input()
        try:
            with self.connect(port, INPUT_ALPN, identity=self.stranger) as stream:
                self.closed_by_desktop(stream)
        except (OSError, ssl.SSLError):
            pass
        with self.connect(port, FILES_ALPN) as stream:
            self.closed_by_desktop(stream)
        self.assertEqual(session.state, 'waiting')
        with self.connect(port, INPUT_ALPN) as stream:
            transport.send(stream, {'session': session.id})
            transport.send(stream, {'events': [{'type': 'move', 'dx': 1, 'dy': 1}]})
            self.assertTrue(wait_for(lambda: session.frames == 1))
        self.assertTrue(session.join(5))
        self.assertEqual(session.state, 'closed')

    def test_wrong_session_consumes_and_reuse_is_refused(self):
        session, port = self.open_input()
        with self.connect(port, INPUT_ALPN) as stream:
            transport.send(stream, {'session': secrets.token_hex(16)})
            self.closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('failed', 'wrong-session'))
        with self.assertRaises(OSError):
            self.connect(port, INPUT_ALPN).close()

    def test_unused_session_expires_and_idle_stream_closes(self):
        clock = [1000.0]
        inputs = InputStreams(self.adapters, addresses=['127.0.0.1'], now=lambda: clock[0], idle_timeout=.5)
        self.addCleanup(inputs.close_all)
        session_id, port = inputs.open_session(self.phone_pin)
        session = inputs.get(session_id)
        clock[0] += 31
        self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('expired', 'expired'))
        session_id, port = inputs.open_session(self.phone_pin)
        session = inputs.get(session_id)
        with self.connect(port, INPUT_ALPN) as stream:
            transport.send(stream, {'session': session_id})
            self.assertTrue(session.join(5))
            self.closed_by_desktop(stream)
        self.assertEqual((session.state, session.error), ('failed', 'idle-timeout'))

    def test_revocation_and_ungranted_phone_are_refused(self):
        session, port = self.open_input()
        Registry(self.desktop).revoke(self.phone_pin)
        with self.assertRaises((OSError, ssl.SSLError, EOFError)):
            with self.connect(port, INPUT_ALPN) as stream:
                transport.send(stream, {'session': session.id})
                transport.send(stream, {'events': [{'type': 'move', 'dx': 1, 'dy': 1}]})
                stream.settimeout(5)
                if stream.recv(1) == b'':
                    raise EOFError()
        self.assertTrue(session.join(5))
        self.assertEqual(session.error, 'revoked')
        self.assertEqual(self.injector.batches, [])
        with self.assertRaises(Denied):
            self.inputs.open_session(self.phone_pin)

    def test_newer_stream_replaces_older_one_from_same_phone(self):
        first, _ = self.open_input()
        second, port = self.open_input()
        self.assertTrue(first.join(5))
        self.assertEqual(first.state, 'cancelled')
        self.assertEqual(second.state, 'waiting')
        self.assertEqual([s.id for s in self.inputs.sessions()], [second.id])

    def test_desktop_without_injector_offers_no_stream(self):
        adapters = DesktopAdapters(self.desktop, downloads=self.root / 'Downloads')
        table = stream_adapters(adapters, inputs=InputStreams(adapters, addresses=['127.0.0.1']))(self.phone_pin)
        self.assertNotIn('input.control', table)


class FileStreamTest(StreamTestCase):
    def offer(self, data, name='movie.mkv', transfer=None, digest=None):
        transfer = transfer or secrets.token_hex(16)
        receipt = self.phone_call('files.write', {'stream': {'transfer': transfer, 'name': name, 'size': len(data),
                                                             'sha256': digest or hashlib.sha256(data).hexdigest()}})
        self.assertEqual(receipt['state'], 'complete')
        return transfer, receipt['result']

    def push(self, result, data, *, stop_after=None):
        with self.connect(result['port'], FILES_ALPN) as stream:
            transport.send(stream, {'session': result['session'], 'offset': result['offset']})
            payload = data[result['offset']:stop_after]
            stream.sendall(payload)
            if stop_after is not None:
                return None
            return transport.receive(stream)

    def test_file_is_verified_and_committed_atomically(self):
        data = secrets.token_bytes(3 * 1024 * 1024 + 17)
        transfer, result = self.offer(data)
        self.assertEqual(result['offset'], 0)
        part = self.desktop / 'transfers' / f'{self.phone_pin[:16]}-{transfer}.part'
        sidecar = part.with_suffix('.json')
        self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(sidecar.read_text())['peer'], self.phone_pin)
        self.assertEqual(self.push(result, data), {'state': 'complete', 'name': 'movie.mkv'})
        self.assertEqual((self.root / 'Downloads' / 'movie.mkv').read_bytes(), data)
        self.assertFalse(part.exists() or sidecar.exists())
        self.assertEqual(self.notified[-1][1]['name'], 'movie.mkv')
        # Same name again gets a unique name, never an overwrite.
        _, result = self.offer(data)
        self.assertEqual(self.push(result, data), {'state': 'complete', 'name': 'movie (2).mkv'})

    def test_interrupted_transfer_resumes_from_persisted_offset(self):
        data = secrets.token_bytes(2 * 1024 * 1024)
        transfer, result = self.offer(data)
        self.push(result, data, stop_after=700_000)
        self.assertTrue(self.files.get(result['session']).join(5))
        # A new daemon (fresh registry) finds the partial file and its sidecar.
        files = FileStreams(self.adapters, addresses=['127.0.0.1'])
        self.addCleanup(files.close_all)
        again = files.offer(self.phone_pin, {'transfer': transfer, 'name': 'movie.mkv', 'size': len(data),
                                             'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual(again['offset'], 700_000)
        self.assertEqual(self.push(again, data), {'state': 'complete', 'name': 'movie.mkv'})
        self.assertEqual((self.root / 'Downloads' / 'movie.mkv').read_bytes(), data)

    def test_reoffer_stops_a_stalled_session_for_the_same_transfer(self):
        data = secrets.token_bytes(400_000)
        transfer, first = self.offer(data)
        with self.connect(first['port'], FILES_ALPN) as stream:
            transport.send(stream, {'session': first['session'], 'offset': 0})
            stream.sendall(data[:100_000])
            self.assertTrue(wait_for(lambda: self.files.get(first['session']).received == 100_000))
            _, second = self.offer(data, transfer=transfer)
            self.assertEqual(second['offset'], 100_000)
        self.assertEqual(self.push(second, data), {'state': 'complete', 'name': 'movie.mkv'})

    def test_changed_offer_discards_partial(self):
        data = secrets.token_bytes(300_000)
        transfer, result = self.offer(data)
        self.push(result, data, stop_after=100_000)
        self.assertTrue(self.files.get(result['session']).join(5))
        other = secrets.token_bytes(300_000)
        _, result = self.offer(other, transfer=transfer)
        self.assertEqual(result['offset'], 0)
        self.assertEqual(self.push(result, other)['state'], 'complete')

    def test_digest_mismatch_discards_partial_and_reports_failure(self):
        data = secrets.token_bytes(100_000)
        transfer, result = self.offer(data, digest='0' * 64)
        self.assertEqual(self.push(result, data), {'state': 'failed', 'error': 'digest-mismatch'})
        self.assertFalse((self.root / 'Downloads' / 'movie.mkv').exists())
        self.assertEqual([p.name for p in (self.desktop / 'transfers').iterdir()], [])

    def test_names_sizes_and_fields_are_validated(self):
        digest = hashlib.sha256(b'a').hexdigest()
        for stream in ({'transfer': secrets.token_hex(16), 'name': '../evil', 'size': 1, 'sha256': digest},
                       {'transfer': secrets.token_hex(16), 'name': '..', 'size': 1, 'sha256': digest},
                       {'transfer': secrets.token_hex(16), 'name': 'a\nb', 'size': 1, 'sha256': digest},
                       {'transfer': secrets.token_hex(16), 'name': 'big', 'size': 4 * 1024 ** 3 + 1, 'sha256': digest},
                       {'transfer': 'nope', 'name': 'a', 'size': 1, 'sha256': digest},
                       {'transfer': secrets.token_hex(16), 'name': 'a', 'size': 1, 'sha256': digest, 'offset': 0},
                       {'transfer': secrets.token_hex(16), 'name': 'a', 'size': 1.0, 'sha256': digest}):
            try:
                receipt = self.phone_call('files.write', {'stream': stream})
            except ValueError:
                continue  # floats never leave the canonical encoder
            self.assertEqual(receipt['result'], {'error': 'invalid-request'}, stream)
        self.assertFalse((self.root / 'Downloads').exists())
        self.assertEqual(self.files.sessions(), [])

    def test_wrong_offset_or_session_consumes_session(self):
        data = b'hello'
        _, result = self.offer(data)
        with self.connect(result['port'], FILES_ALPN) as stream:
            transport.send(stream, {'session': result['session'], 'offset': 3})
            self.assertEqual(transport.receive(stream), {'state': 'failed', 'error': 'wrong-offset'})
        with self.assertRaises(OSError):
            self.connect(result['port'], FILES_ALPN).close()

    def test_wrong_pin_does_not_consume_and_unused_session_expires(self):
        clock = [50.0]
        files = FileStreams(self.adapters, addresses=['127.0.0.1'], now=lambda: clock[0])
        self.addCleanup(files.close_all)
        data = b'x' * 10
        result = files.offer(self.phone_pin, {'transfer': secrets.token_hex(16), 'name': 'x', 'size': 10,
                                              'sha256': hashlib.sha256(data).hexdigest()})
        try:
            with self.connect(result['port'], FILES_ALPN, identity=self.stranger) as stream:
                self.closed_by_desktop(stream)
        except (OSError, ssl.SSLError):
            pass
        session = files.get(result['session'])
        self.assertEqual(session.state, 'waiting')
        clock[0] += 31
        self.assertTrue(session.join(5))
        self.assertEqual(session.state, 'expired')

    def test_stale_partial_files_are_removed_on_start(self):
        transfers = self.desktop / 'transfers'
        transfers.mkdir(exist_ok=True)
        old = transfers / f'{"a" * 16}-{"b" * 32}.part'
        old_sidecar = old.with_suffix('.json')
        fresh = transfers / f'{"c" * 16}-{"d" * 32}.part'
        unrelated = transfers / 'notes.txt'
        for path in (old, old_sidecar, fresh, unrelated):
            path.write_bytes(b'x')
        stale = time.time() - STALE_PART_AGE - 60
        for path in (old, old_sidecar, unrelated):
            os.utime(path, (stale, stale))
        files = FileStreams(self.adapters, addresses=['127.0.0.1'])
        self.assertEqual(files.removed_stale, 2)
        self.assertEqual(sorted(p.name for p in transfers.iterdir()), sorted([fresh.name, unrelated.name]))

    def test_concurrent_streams_complete_independently(self):
        blobs = [secrets.token_bytes(1_500_000 + index) for index in range(3)]
        offers = [self.offer(blob, name=f'part-{index}.bin')[1] for index, blob in enumerate(blobs)]
        results = [None] * 3

        def run(index):
            results[index] = self.push(offers[index], blobs[index])
        threads = [threading.Thread(target=run, args=(index,)) for index in range(3)]
        receipt = self.phone_call('input.control', {'stream': {}})
        input_session = self.inputs.get(receipt['result']['session'])
        for thread in threads:
            thread.start()
        with self.connect(receipt['result']['port'], INPUT_ALPN) as stream:
            transport.send(stream, {'session': input_session.id})
            for _ in range(20):
                transport.send(stream, {'events': [{'type': 'move', 'dx': 1, 'dy': 0}]})
            self.assertTrue(wait_for(lambda: input_session.frames == 20))
        for thread in threads:
            thread.join(20)
        self.assertEqual(results, [{'state': 'complete', 'name': f'part-{index}.bin'} for index in range(3)])
        for index, blob in enumerate(blobs):
            self.assertEqual((self.root / 'Downloads' / f'part-{index}.bin').read_bytes(), blob)
        self.assertEqual(len(self.injector.batches), 20)

    def test_chunked_files_write_still_works(self):
        path = self.root / 'chunked.bin'
        path.write_bytes(secrets.token_bytes(600_000))
        call = lambda fingerprint, capability, payload: self.phone_call(capability, payload)
        self.assertEqual(send_file(call, self.desktop_pin, str(path))['name'], 'chunked.bin')
        self.assertEqual((self.root / 'Downloads' / 'chunked.bin').read_bytes(), path.read_bytes())


class SendFileStreamTest(StreamTestCase):
    """Desktop to phone: the 'phone' here is a second Python receiver using the same FileStreams."""

    def setUp(self):
        super().setUp()
        self.phone_adapters = DesktopAdapters(self.phone, downloads=self.root / 'PhoneDownloads')
        self.phone_files = FileStreams(self.phone_adapters, addresses=['127.0.0.1'],
                                       authorized=lambda peer, capability: capability == 'files.write')
        self.addCleanup(self.phone_files.close_all)
        # The phone knows the desktop as a companion so its listener admits it.
        registry = Registry(self.phone)
        journal = registry._open()
        try:
            registry.record(journal, self.desktop_pin, name='Desk', model='Luma', platform='android',
                            host='127.0.0.1', port=self.port, now=time.time())
        finally:
            journal.close()
        self.phone_listener = CompanionListener(self.phone, addresses=['127.0.0.1'], port=0,
                                                adapter_factory=stream_adapters(self.phone_adapters, files=self.phone_files))
        phone_port = self.phone_listener.start()
        self.addCleanup(self.phone_listener.stop)
        Registry(self.desktop).seen(self.phone_pin, '127.0.0.1', port=phone_port)
        Registry(self.desktop).set_incoming(self.phone_pin, self.PHONE_TO_DESKTOP)
        self.source = self.root / 'holiday.mov'
        self.data = secrets.token_bytes(2 * 1024 * 1024 + 5)
        self.source.write_bytes(self.data)

    def test_desktop_sends_and_resumes_after_interruption(self):
        from luma_continuity.companion import call
        progress = []

        def cancel():
            return bool(progress) and progress[-1][0] >= 600_000

        transfer = secrets.token_hex(16)
        with self.assertRaises(Cancelled):
            send_file_stream(self.desktop, self.phone_pin, str(self.source), lambda sent, size: progress.append((sent, size)),
                             cancel, transfer=transfer)
        self.assertTrue(wait_for(lambda: not self.phone_files.sessions()))
        progress.clear()
        result = send_file_stream(self.desktop, self.phone_pin, str(self.source),
                                  lambda sent, size: progress.append((sent, size)), transfer=transfer, call=call)
        self.assertGreaterEqual(result['offset'], 600_000)
        self.assertEqual(result['name'], 'holiday.mov')
        self.assertEqual(progress[-1], (len(self.data), len(self.data)))
        self.assertEqual((self.root / 'PhoneDownloads' / 'holiday.mov').read_bytes(), self.data)

    def test_unsupported_receiver_raises_for_fallback(self):
        refusing = lambda directory, fingerprint, capability, payload: {'state': 'complete', 'result': {'error': 'invalid-request'}}
        with self.assertRaises(StreamUnsupported):
            send_file_stream(self.desktop, self.phone_pin, str(self.source), call=refusing)
        unavailable = lambda directory, fingerprint, capability, payload: {'state': 'complete', 'result': {'error': 'unavailable'}}
        with self.assertRaises(StreamUnsupported):
            send_file_stream(self.desktop, self.phone_pin, str(self.source), call=unavailable)

    def test_sender_refuses_a_receiver_with_the_wrong_pin(self):
        stranger_pin = transport.fingerprint((self.stranger / 'device.pem').read_text())
        (self.desktop / 'peers' / (stranger_pin + '.pem')).write_text((self.stranger / 'device.pem').read_text())
        result = self.phone_files.offer(self.desktop_pin, {'transfer': secrets.token_hex(16), 'name': 'x', 'size': 1,
                                                           'sha256': hashlib.sha256(b'x').hexdigest()})
        fd = os.open(self.source, os.O_RDONLY)
        try:
            with self.assertRaises((ssl.SSLError, OSError)):
                push_file(self.desktop, stranger_pin, '127.0.0.1', result['port'], result['session'], fd, 1, 0)
        finally:
            os.close(fd)
        self.assertEqual(self.phone_files.get(result['session']).state, 'waiting')


if __name__ == '__main__':
    unittest.main()
