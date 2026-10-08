"""Synthetic tests for the `luma-media/1` receiver. Real TLS over loopback only."""
import hashlib
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
from luma_continuity.bootstrap import approve_peer, create_identity
from luma_continuity.companion_media import (MAX_PACKET, MEDIA_ALPN, PACKET, MediaSessions, MediaUnavailable,
                                             StreamError, node_suffix, normalize_point, pipeline_plan,
                                             validate_control, validate_header)
from luma_continuity.policy import Denied, Journal


class RecordingSink:
    def __init__(self):
        self.events = []
        self.closed = threading.Event()

    def configure(self, header): self.events.append(('configure', header))
    def codec_config(self, data): self.events.append(('codec_config', data))
    def video(self, pts_us, data, key): self.events.append(('video', pts_us, data, key))
    def audio_config(self, data): self.events.append(('audio_config', data))
    def audio(self, pts_us, data): self.events.append(('audio', pts_us, data))

    def close(self):
        self.events.append(('close',))
        self.closed.set()


def packet(kind, pts, payload=b''):
    return PACKET.pack(kind, pts, len(payload)) + payload


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return predicate()


class MediaStreamTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.desktop, self.phone, self.stranger = root / 'desktop', root / 'phone', root / 'stranger'
        self.desktop_pin = create_identity(self.desktop)
        self.phone_pin = create_identity(self.phone)
        create_identity(self.stranger)
        approve_peer(self.desktop, self.phone / 'device.pem', self.phone_pin, secrets.token_hex(16), {'device.status'},
                     outgoing_grants={'camera.stream', 'screen.view', 'screen.control'})
        self.sinks = []
        self.clock = None
        self.media = self.registry()

    def registry(self, **options):
        def factory(session):
            sink = RecordingSink()
            self.sinks.append(sink)
            return sink
        media = MediaSessions(self.desktop, addresses=['127.0.0.1'], sink_factory=factory, **options)
        self.addCleanup(media.close_all)
        return media

    def connect(self, port, identity=None, alpn=MEDIA_ALPN):
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
        assert hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() == self.desktop_pin
        return stream

    def header(self, session_id, kind='camera', **changes):
        value = {'session': session_id, 'kind': kind, 'codec': 'h264', 'width': 1280, 'height': 720,
                 'fps': 30, 'rotation': 90, 'audio': 'opus'}
        value.update(changes)
        return value

    def assert_closed_by_desktop(self, stream):
        try:
            stream.settimeout(5)
            self.assertEqual(stream.recv(1), b'')
        except (OSError, ssl.SSLError):
            pass

    def open(self, kind='camera', control=False):
        session_id, port = self.media.open_session(self.phone_pin, kind, control)
        return self.media.get(session_id), port

    def test_camera_stream_reaches_sink_in_order(self):
        session, port = self.open('camera')
        self.assertEqual(session.state, 'waiting')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id))
            stream.sendall(packet(0x01, 0, b'\x00\x00\x00\x01sps') + packet(0x03, 1000, b'key')
                           + packet(0x02, 34_000, b'delta') + packet(0x04, 0, b'OpusHead')
                           + packet(0x05, 20_000, b'opus') + packet(0x7f, 40_000))
            self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('closed', None))
        self.assertEqual(self.sinks[0].events, [
            ('configure', self.header(session.id)), ('codec_config', b'\x00\x00\x00\x01sps'),
            ('video', 1000, b'key', True), ('video', 34_000, b'delta', False), ('audio_config', b'OpusHead'),
            ('audio', 20_000, b'opus'), ('close',)])
        self.assertIsNone(self.media.get(session.id))

    def test_phone_closing_at_packet_boundary_ends_session(self):
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id, audio=None))
            stream.sendall(packet(0x03, 5, b'frame'))
        self.assertTrue(session.join(5))
        self.assertEqual(session.state, 'closed')
        self.assertTrue(self.sinks[0].closed.is_set())

    def test_screen_control_frames_reach_phone(self):
        session, port = self.open('screen', control=True)
        self.assertFalse(session.send_control({'type': 'key', 'key': 'home'}))  # not streaming yet
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id, 'screen', audio=None, rotation=0))
            stream.sendall(packet(0x03, 0, b'key'))
            self.assertTrue(wait_for(lambda: session.state == 'streaming'))
            self.assertTrue(session.send_control({'type': 'touch', 'action': 'down', 'x': 0, 'y': 10000}))
            self.assertTrue(session.send_control({'type': 'touch', 'action': 'up', 'x': 5000, 'y': 5000}))
            self.assertTrue(session.send_control({'type': 'key', 'key': 'back'}))
            self.assertTrue(session.send_control({'type': 'text', 'text': 'héllo'}))
            stream.settimeout(5)
            received = [transport.receive(stream) for _ in range(4)]
            self.assertEqual(received, [{'type': 'touch', 'action': 'down', 'x': 0, 'y': 10000},
                                        {'type': 'touch', 'action': 'up', 'x': 5000, 'y': 5000},
                                        {'type': 'key', 'key': 'back'}, {'type': 'text', 'text': 'héllo'}])
            with self.assertRaises(ValueError):
                session.send_control({'type': 'touch', 'action': 'down', 'x': 10001, 'y': 0})
            stream.sendall(packet(0x7f, 0))
            self.assertTrue(session.join(5))
        self.assertEqual(session.state, 'closed')

    def test_control_not_allowed_is_refused(self):
        session, port = self.open('screen', control=False)
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id, 'screen', audio=None))
            self.assertTrue(wait_for(lambda: session.state == 'streaming'))
            with self.assertRaises(Denied):
                session.send_control({'type': 'key', 'key': 'back'})
        with self.assertRaises(ValueError):
            self.media.open_session(self.phone_pin, 'camera', True)

    def test_control_requires_the_screen_control_grant(self):
        journal = Journal(self.desktop / 'continuity.db')
        try: journal.set_grants(self.phone_pin, {'device.status'}, outgoing_grants={'screen.view'})
        finally: journal.close()
        with self.assertRaises(Denied):
            self.media.open_session(self.phone_pin, 'screen', True)
        self.media.open_session(self.phone_pin, 'screen', False)
        with self.assertRaises(Denied):
            self.media.open_session(self.phone_pin, 'camera', False)

    def test_wrong_session_consumes_listener_without_sink(self):
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(secrets.token_hex(16)))
            self.assert_closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('failed', 'wrong-session'))
        self.assertEqual(self.sinks, [])
        with self.assertRaises(OSError):
            self.connect(port).close()

    def test_wrong_kind_is_rejected(self):
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id, 'screen'))
            self.assert_closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual(session.error, 'wrong-kind')

    def test_reused_session_is_refused(self):
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id))
            stream.sendall(packet(0x7f, 0))
        self.assertTrue(session.join(5))
        with self.assertRaises(OSError):
            with self.connect(port) as again:
                transport.send(again, self.header(session.id))
                again.settimeout(5)
                if again.recv(1) == b'':
                    raise ConnectionResetError('closed')
        self.assertEqual(len(self.sinks), 1)

    def test_unused_session_expires(self):
        clock = [1000.0]
        media = self.registry(now=lambda: clock[0])
        session_id, port = media.open_session(self.phone_pin, 'camera', False)
        session = media.get(session_id)
        clock[0] += 31
        self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('expired', 'expired'))
        with self.assertRaises(OSError):
            self.connect(port).close()
        self.assertEqual(self.sinks, [])

    def test_wrong_pin_does_not_consume_session(self):
        session, port = self.open('camera')
        try:
            with self.connect(port, identity=self.stranger) as stream:
                stream.settimeout(5)
                self.assertEqual(stream.recv(1), b'')
        except (OSError, ssl.SSLError):
            pass
        with self.connect(port, alpn='luma-continuity/1') as stream:
            self.assert_closed_by_desktop(stream)
        self.assertEqual(session.state, 'waiting')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id))
            stream.sendall(packet(0x7f, 0))
        self.assertTrue(session.join(5))
        self.assertEqual(session.state, 'closed')

    def test_oversized_packet_closes_stream(self):
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id))
            stream.sendall(PACKET.pack(0x02, 0, MAX_PACKET + 1))
            self.assert_closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual((session.state, session.error), ('failed', 'oversized-packet'))
        self.assertEqual(self.sinks[0].events[-1], ('close',))

    def test_unknown_packet_type_closes_stream(self):
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id))
            stream.sendall(packet(0x03, 0, b'key') + packet(0x06, 0, b'?'))
            self.assert_closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual(session.error, 'unknown-packet')
        self.assertEqual([event[0] for event in self.sinks[0].events], ['configure', 'video', 'close'])

    def test_audio_without_audio_header_and_truncation_fail(self):
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id, audio=None))
            stream.sendall(packet(0x05, 0, b'opus'))
            self.assert_closed_by_desktop(stream)
        self.assertTrue(session.join(5))
        self.assertEqual(session.error, 'unexpected-audio')
        session, port = self.open('camera')
        with self.connect(port) as stream:
            transport.send(stream, self.header(session.id))
            stream.sendall(PACKET.pack(0x02, 0, 100) + b'short')
        self.assertTrue(session.join(5))
        self.assertEqual(session.error, 'truncated')

    def test_revocation_closes_waiting_and_streaming_sessions(self):
        waiting, _ = self.open('camera')
        streaming, port = self.open('screen')
        with self.connect(port) as stream:
            transport.send(stream, self.header(streaming.id, 'screen', audio=None))
            self.assertTrue(wait_for(lambda: streaming.state == 'streaming'))
            self.media.close_peer(self.phone_pin)
            self.assertTrue(streaming.join(5))
            self.assert_closed_by_desktop(stream)
        self.assertTrue(waiting.join(5))
        self.assertEqual((waiting.state, streaming.state), ('cancelled', 'cancelled'))
        journal = Journal(self.desktop / 'continuity.db')
        try: journal.revoke(self.phone_pin)
        finally: journal.close()
        with self.assertRaises(Denied):
            self.media.open_session(self.phone_pin, 'camera', False)

    def test_unknown_peer_and_arguments_are_rejected(self):
        with self.assertRaises(Denied):
            self.media.open_session('0' * 64, 'camera', False)
        with self.assertRaises(ValueError):
            self.media.open_session(self.phone_pin, 'microphone', False)
        with self.assertRaises(ValueError):
            self.media.open_session(self.phone_pin.upper(), 'camera', False)
        with self.assertRaises(ValueError):
            MediaSessions(self.desktop, addresses=['0.0.0.0'], sink_factory=RecordingSink)


class ValidationTest(unittest.TestCase):
    session = 'a' * 32

    def header(self, **changes):
        value = {'session': self.session, 'kind': 'screen', 'codec': 'h264', 'width': 1080, 'height': 2400,
                 'fps': 60, 'rotation': 0, 'audio': None}
        value.update(changes)
        return value

    def test_header_limits(self):
        self.assertEqual(validate_header(self.header(), self.session, 'screen'), self.header())
        for changes, code in (({'width': 8193}, 'invalid-header'), ({'height': 0}, 'invalid-header'),
                              ({'width': True}, 'invalid-header'), ({'fps': 121}, 'invalid-header'),
                              ({'fps': 0}, 'invalid-header'), ({'rotation': 45}, 'invalid-header'),
                              ({'audio': 'aac'}, 'invalid-header'), ({'codec': 'h265'}, 'invalid-header'),
                              ({'extra': 1}, 'invalid-header'), ({'session': 'b' * 32}, 'wrong-session'),
                              ({'kind': 'camera'}, 'wrong-kind'), ({'fps': 30.0}, 'invalid-header')):
            with self.subTest(changes=changes):
                with self.assertRaises(StreamError) as caught:
                    validate_header(self.header(**changes), self.session, 'screen')
                self.assertEqual(caught.exception.code, code)

    def test_control_events(self):
        self.assertEqual(validate_control({'type': 'touch', 'action': 'move', 'x': 10000, 'y': 0})['action'], 'move')
        self.assertEqual(validate_control({'type': 'text', 'text': 'x' * 4096})['type'], 'text')
        for event in ({'type': 'touch', 'action': 'hover', 'x': 1, 'y': 1}, {'type': 'touch', 'action': 'down', 'x': -1, 'y': 1},
                      {'type': 'touch', 'action': 'down', 'x': 1.5, 'y': 1}, {'type': 'touch', 'action': 'down', 'x': 1},
                      {'type': 'text', 'text': ''}, {'type': 'text', 'text': 'x' * 4097},
                      {'type': 'key', 'key': 'power'}, {'type': 'key', 'key': 'back', 'extra': 1}, {'type': 'scroll'}, []):
            with self.subTest(event=event):
                with self.assertRaises(ValueError):
                    validate_control(event)


class PresentationPlanTest(unittest.TestCase):
    peer = 'c' * 64
    header = {'session': 'a' * 32, 'kind': 'camera', 'codec': 'h264', 'width': 1280, 'height': 720,
              'fps': 30, 'rotation': 90, 'audio': 'opus'}

    @staticmethod
    def available(*names):
        return lambda name: name in names

    def test_camera_is_a_pipewire_video_source_and_microphone(self):
        everything = self.available('decodebin', 'pipewiresink', 'opusparse', 'opusdec', 'audioconvert', 'audioresample')
        plan = pipeline_plan('camera', self.header, device_name='Pixel\x07 9', peer=self.peer, available=everything)
        self.assertEqual([factory for factory, _ in plan['video']],
                         ['appsrc', 'h264parse', 'decodebin', 'videoconvert', 'videoflip', 'pipewiresink'])
        self.assertEqual(plan['video'][0][1]['caps'], 'video/x-h264,stream-format=byte-stream,alignment=au')
        self.assertEqual(plan['video'][4][1], {'video-direction': '90r'})
        sink = plan['video'][-1][1]
        suffix = node_suffix(self.peer)
        self.assertRegex(suffix, r'\A[0-9a-f]{12}\Z')
        self.assertNotIn(suffix, self.peer)
        self.assertEqual(sink['mode'], 'provide')
        self.assertEqual(sink['stream-properties'], {'media.class': 'Video/Source', 'media.role': 'Camera',
                                                     'node.name': 'luma-connect-camera-' + suffix,
                                                     'node.description': 'Pixel 9 Camera'})
        self.assertEqual([factory for factory, _ in plan['audio']],
                         ['appsrc', 'opusparse', 'opusdec', 'audioconvert', 'audioresample', 'pipewiresink'])
        self.assertEqual(plan['audio'][-1][1]['stream-properties']['media.class'], 'Audio/Source')

    def test_camera_fallbacks(self):
        header = {**self.header, 'audio': None, 'rotation': 0}
        plan = pipeline_plan('camera', header, device_name='P', peer=self.peer,
                             available=self.available('avdec_h264', 'v4l2sink'), v4l2_device='/dev/video7')
        self.assertEqual([factory for factory, _ in plan['video']], ['appsrc', 'h264parse', 'avdec_h264', 'videoconvert', 'v4l2sink'])
        self.assertEqual(plan['video'][-1][1]['device'], '/dev/video7')
        self.assertIsNone(plan['audio'])
        with self.assertRaises(MediaUnavailable):
            pipeline_plan('camera', header, device_name='P', peer=self.peer, available=self.available('avdec_h264', 'v4l2sink'))
        with self.assertRaises(ValueError):
            pipeline_plan('camera', header, device_name='P', peer=self.peer,
                          available=self.available('avdec_h264', 'v4l2sink'), v4l2_device='/etc/passwd')
        with self.assertRaises(MediaUnavailable) as caught:
            pipeline_plan('camera', header, device_name='P', peer=self.peer, available=self.available('pipewiresink'))
        self.assertEqual(caught.exception.code, 'h264-decoder-unavailable')

    def test_codec_preference_follows_installed_decoders(self):
        from luma_continuity.companion_media import decodable_codecs
        fedora = self.available('vp8dec', 'vp9dec', 'vp9parse', 'dav1ddec', 'av1parse', 'decodebin')
        self.assertEqual(decodable_codecs(fedora), ['av1', 'vp9', 'vp8'])
        self.assertEqual(decodable_codecs(self.available('vp8dec')), ['vp8'])
        header = dict(self.header, codec='vp8', audio=None)
        plan = pipeline_plan('camera', header, device_name='P', peer=self.peer,
                             available=self.available('vp8dec', 'decodebin', 'videoconvert', 'videoflip', 'pipewiresink'))
        self.assertEqual([factory for factory, _ in plan['video']], ['appsrc', 'decodebin', 'videoconvert', 'videoflip', 'pipewiresink'])
        self.assertEqual(plan['video'][0][1]['caps'], 'video/x-vp8')

    def test_screen_uses_gtk4_paintable_and_plain_playback(self):
        header = {**self.header, 'kind': 'screen', 'rotation': 270}
        everything = self.available('openh264dec', 'gtk4paintablesink', 'pipewiresink', 'opusparse', 'opusdec',
                                    'audioconvert', 'audioresample')
        plan = pipeline_plan('screen', header, device_name='Pixel 9', peer=self.peer, available=everything)
        self.assertEqual(plan['video'][-1][0], 'gtk4paintablesink')
        self.assertEqual(plan['video'][-2], ('videoflip', {'video-direction': '90l'}))
        self.assertNotIn('mode', plan['audio'][-1][1])
        self.assertNotIn('media.class', plan['audio'][-1][1]['stream-properties'])

    def test_pointer_normalization_accounts_for_letterboxing(self):
        # 1080x2400 content in a 1000x1000 widget: shown 450x1000, 275 px bars each side.
        self.assertEqual(normalize_point(275, 0, 1000, 1000, 1080, 2400), (0, 0))
        self.assertEqual(normalize_point(725, 1000, 1000, 1000, 1080, 2400), (10000, 10000))
        self.assertEqual(normalize_point(500, 500, 1000, 1000, 1080, 2400), (5000, 5000))
        self.assertIsNone(normalize_point(100, 500, 1000, 1000, 1080, 2400))
        self.assertEqual(normalize_point(100, 1200, 1000, 1000, 1080, 2400, clamp=True), (0, 10000))
        self.assertIsNone(normalize_point(1, 1, 0, 1000, 1080, 2400))


if __name__ == '__main__':
    unittest.main()
