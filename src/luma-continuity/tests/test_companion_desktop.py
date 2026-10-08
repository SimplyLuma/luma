"""Synthetic tests for companion desktop owners and the daemon service. Loopback only, no D-Bus."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import selectors
import socket
import ssl
import tempfile
import threading
import time
import unittest

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity import transport
from luma_continuity.bootstrap import create_identity
from luma_continuity.companion import PAIRING_ALPN, SCHEMA, DesktopAdapters, Registry
from luma_continuity.companion_desktop import (DEFAULT_DESKTOP_TO_PHONE, DEFAULT_PHONE_TO_DESKTOP, FILE_CHUNK,
                                               PLAYER_INTERFACE, REMOTE_DESKTOP_SESSION, ROOT_INTERFACE,
                                               AvahiPublisher, Cancelled, CompanionService, FreedesktopNotifier,
                                               InputInjector, MprisBridge, ShellClipboard, mpris_bus_name,
                                               select_addresses, send_file, validate_invoke)
from luma_continuity.local import bounded_stream
from luma_continuity.policy import Journal
from luma_continuity.session import Receiver

KEY, ACTION, REPLY = secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(16)
PEER = 'ab' * 32


def post(**changes):
    value = {'op': 'post', 'key': KEY, 'app': 'Messages', 'package': 'com.example.messages', 'title': 'Sam',
             'text': 'Running <late>', 'when': 1, 'silent': False, 'conversation': None, 'device': 'Pixel 9',
             'actions': [{'id': ACTION, 'label': 'Mark as read', 'reply': False},
                         {'id': REPLY, 'label': 'Reply', 'reply': True}]}
    value.update(changes)
    return value


class FakeNotifications:
    def __init__(self):
        self.calls, self.next = [], 40

    def __call__(self, method, signature, values):
        self.calls.append((method, signature, values))
        if method == 'Notify':
            self.next += 1
            return (values[1] or self.next,)
        return ()


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.bus, self.acts, self.replies, self.launched, self.shown = FakeNotifications(), [], [], [], []
        self.allowed = True
        self.notifier = FreedesktopNotifier(self.bus, act=lambda peer, payload: self.acts.append((peer, payload)),
                                            reply=lambda peer, request: self.replies.append((peer, request)),
                                            launch=self.launched.append, show_file=self.shown.append,
                                            can_act=lambda peer: self.allowed)

    def test_post_maps_actions_and_replaces_by_key(self):
        self.notifier('notification', PEER, post())
        method, signature, values = self.bus.calls[0]
        self.assertEqual((method, signature), ('Notify', '(susssasa{sv}i)'))
        self.assertEqual(values[0], 'Luma Connect')
        self.assertEqual(values[1], 0)
        self.assertEqual(values[3], 'Sam')
        self.assertIn('Running &lt;late&gt;', values[4])
        self.assertEqual(values[5], ['default', 'Open', ACTION, 'Mark as read', REPLY, 'Reply'])
        self.notifier('notification', PEER, post(text='Here now'))
        self.assertEqual(self.bus.calls[1][2][1], 41)  # replaces the first notification
        self.notifier('notification', PEER, {'op': 'remove', 'key': KEY, 'device': 'Pixel 9'})
        self.assertEqual(self.bus.calls[2][:1] + self.bus.calls[2][2:], ('CloseNotification', (41,)))
        self.notifier.notification_closed(41, 3)
        self.assertEqual(self.acts, [])

    def test_actions_become_notifications_act_and_reply_prompts(self):
        self.notifier('notification', PEER, post())
        self.notifier.action_invoked(41, ACTION)
        self.notifier.notification_closed(41, 2)  # the Shell closes after an action; not a dismissal
        self.assertEqual(self.acts, [(PEER, {'key': KEY, 'action': ACTION})])
        self.notifier('notification', PEER, post())
        self.notifier.action_invoked(42, REPLY)
        self.assertEqual(self.replies[0][1]['action'], REPLY)
        self.assertEqual(len(self.acts), 1)
        self.notifier('notification', PEER, post())
        self.notifier.action_invoked(43, 'default')
        self.notifier('notification', PEER, post())
        self.notifier.notification_closed(44, 2)
        self.assertEqual(self.acts[1:], [(PEER, {'key': KEY, 'action': 'open'}), (PEER, {'key': KEY, 'action': 'dismiss'})])

    def test_no_phone_actions_without_the_outgoing_grant(self):
        self.allowed = False
        self.notifier('notification', PEER, post())
        self.assertEqual(self.bus.calls[0][2][5], [])
        self.notifier.notification_closed(41, 2)
        self.assertEqual(self.acts, [])

    def test_links_and_files_open_only_after_a_click(self):
        self.notifier('link', PEER, {'device': 'Pixel 9', 'url': 'https://example.org/', 'title': ''})
        self.assertEqual(self.launched, [])
        self.notifier.action_invoked(41, 'open')
        self.assertEqual(self.launched, ['https://example.org/'])
        self.notifier('file', PEER, {'device': 'Pixel 9', 'path': '/tmp/a b.jpg', 'name': 'a b.jpg'})
        self.assertEqual(self.bus.calls[-1][2][5], ['default', 'Open', 'open', 'Open', 'show', 'Show in Files'])
        self.notifier.action_invoked(42, 'show')
        self.assertEqual(self.shown, ['/tmp/a b.jpg'])

    def test_clear_closes_every_notification_of_that_phone(self):
        self.notifier('notification', PEER, post())
        self.notifier('notification', PEER, post(key=secrets.token_hex(16)))
        self.notifier('notification', 'cd' * 32, post())
        self.notifier('notification', PEER, {'op': 'clear', 'device': 'Pixel 9'})
        closed = sorted(values[0] for method, _, values in self.bus.calls if method == 'CloseNotification')
        self.assertEqual(closed, [41, 42])


class ClipboardTest(unittest.TestCase):
    def test_shell_abi(self):
        calls = []
        def caller(method, signature, values):
            calls.append((method, signature, values))
            return (True, 'from shell') if method == 'GetText' else ()
        clipboard = ShellClipboard(caller)
        clipboard.set_text('hello', True)
        self.assertEqual(clipboard.get_text(), 'from shell')
        self.assertEqual(calls, [('SetText', '(sb)', ('hello', True)), ('GetText', '()', ())])
        self.assertIsNone(ShellClipboard(lambda *_: (False, '')).get_text())


class FakeExport:
    def __init__(self, name, method, properties):
        self.name, self.method, self.properties = name, method, properties
        self.changes, self.seeks, self.closed = [], [], False

    def changed(self, interface, values): self.changes.append((interface, values))
    def seeked(self, position): self.seeks.append(position)
    def close(self): self.closed = True


def media(**changes):
    value = {'state': 'playing', 'title': 'Song', 'artist': 'Band', 'album': 'Record', 'app': 'Music',
             'duration_ms': 200_000, 'position_ms': 10_000, 'actions': ['play', 'pause', 'next', 'seek']}
    value.update(changes)
    return value


class MprisTest(unittest.TestCase):
    def setUp(self):
        self.clock, self.exports, self.controls = [100.0], [], []
        def exporter(*args):
            self.exports.append(FakeExport(*args)); return self.exports[-1]
        self.bridge = MprisBridge(exporter, control=lambda peer, payload: self.controls.append(payload),
                                  device_name=lambda peer: 'Pixel 9', now=lambda: self.clock[0])

    def test_state_maps_to_mpris_properties(self):
        self.bridge.update(PEER, media())
        export = self.exports[0]
        self.assertEqual(export.name, mpris_bus_name(PEER))
        self.assertEqual(export.name, 'org.mpris.MediaPlayer2.LumaConnect.p' + 'ab' * 6)
        self.clock[0] += 2
        properties = export.properties()
        player, root = properties[PLAYER_INTERFACE], properties[ROOT_INTERFACE]
        self.assertEqual(player['PlaybackStatus'], ('s', 'Playing'))
        self.assertEqual(player['Position'], ('x', 12_000_000))
        self.assertEqual(player['CanGoNext'], ('b', True))
        self.assertEqual(player['CanGoPrevious'], ('b', False))
        self.assertEqual(player['CanSeek'], ('b', True))
        metadata = player['Metadata'][1]
        self.assertEqual(metadata['mpris:length'], ('x', 200_000_000))
        self.assertEqual(metadata['xesam:artist'], ('as', ['Band']))
        self.assertTrue(metadata['mpris:trackid'][1].startswith('/org/projectluma/Connect/track/t'))
        self.assertEqual(root['Identity'], ('s', 'Music on Pixel 9'))

    def test_methods_call_media_control_and_respect_actions(self):
        self.bridge.update(PEER, media())
        export = self.exports[0]
        export.method('PlayPause', ())
        export.method('Next', ())
        export.method('Seek', (5_000_000,))
        track = export.properties()[PLAYER_INTERFACE]['Metadata'][1]['mpris:trackid'][1]
        export.method('SetPosition', (track, 42_000_000))
        export.method('SetPosition', ('/stale', 1))
        self.assertEqual(self.controls, [{'command': 'pause'}, {'command': 'next'},
                                         {'command': 'seek', 'position_ms': 15_000}, {'command': 'seek', 'position_ms': 42_000}])
        with self.assertRaises(PermissionError):
            export.method('Previous', ())
        with self.assertRaises(NotImplementedError):
            export.method('OpenUri', ('https://example.org',))

    def test_updates_signal_changes_and_close_when_nothing_plays(self):
        self.bridge.update(PEER, media())
        self.bridge.update(PEER, media(state='paused', position_ms=90_000))
        export = self.exports[0]
        self.assertIn((PLAYER_INTERFACE, {'PlaybackStatus': ('s', 'Paused')}), export.changes)
        self.assertEqual(export.seeks, [90_000_000])
        self.bridge.update(PEER, media(state='none'))
        self.assertTrue(export.closed)
        self.assertEqual(self.bridge.players, {})


class InputTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.received = []
        self.adapters = DesktopAdapters(Path(self.temp.name), downloads=Path(self.temp.name) / 'Downloads',
                                        input=lambda peer, events: self.received.append(events))

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_batch_reaches_injector(self):
        events = [{'type': 'move', 'dx': -12, 'dy': 30}, {'type': 'button', 'button': 'left', 'pressed': True},
                  {'type': 'scroll', 'dx': 0, 'dy': -20}, {'type': 'key', 'key': 'enter', 'pressed': True},
                  {'type': 'key', 'key': 'é', 'pressed': False}, {'type': 'text', 'text': 'hello\nworld'}]
        self.assertEqual(self.adapters.for_peer(PEER)['input.control']('input.control', {'events': events}), {'accepted': True})
        self.assertEqual(self.received, [events])

    def test_invalid_batches_are_rejected(self):
        bad = [
            {'events': []},
            {'events': [{'type': 'move', 'dx': 1, 'dy': 1}] * 65},
            {'events': [{'type': 'move', 'dx': 5000, 'dy': 0}]},
            {'events': [{'type': 'move', 'dx': 1.5, 'dy': 0}]},
            {'events': [{'type': 'move', 'dx': True, 'dy': 0}]},
            {'events': [{'type': 'scroll', 'dx': 0, 'dy': 601}]},
            {'events': [{'type': 'button', 'button': 'back', 'pressed': True}]},
            {'events': [{'type': 'key', 'key': 'launch_rocket', 'pressed': True}]},
            {'events': [{'type': 'key', 'key': '\x1b', 'pressed': True}]},
            {'events': [{'type': 'text', 'text': 'x' * 257}]},
            {'events': [{'type': 'text', 'text': 'x' * 256}] * 5},
            {'events': [{'type': 'text', 'text': 'bell\x07'}]},
            {'events': [{'type': 'move', 'dx': 1, 'dy': 1, 'extra': 1}]},
            {'events': [], 'more': True},
        ]
        for payload in bad:
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(ValueError):
                self.adapters.input_events(PEER, payload)
        self.assertEqual(self.received, [])

    def test_injector_creates_session_lazily_and_closes_when_idle(self):
        calls, clock = [], [0.0]
        def call(path, interface, method, signature, values):
            calls.append((path, method, values))
            return ('/org/gnome/Mutter/RemoteDesktop/Session/u1',) if method == 'CreateSession' else ()
        injector = InputInjector(call, now=lambda: clock[0], timer=False)
        self.assertEqual(calls, [])
        injector(PEER, [{'type': 'move', 'dx': 3, 'dy': -4}, {'type': 'button', 'button': 'right', 'pressed': True},
                        {'type': 'scroll', 'dx': 0, 'dy': 10}, {'type': 'text', 'text': 'A€'}])
        session = '/org/gnome/Mutter/RemoteDesktop/Session/u1'
        self.assertEqual([method for _, method, _ in calls[:2]], ['CreateSession', 'Start'])
        self.assertEqual(calls[2], (session, 'NotifyPointerMotionRelative', (3.0, -4.0)))
        self.assertEqual(calls[3], (session, 'NotifyPointerButton', (0x111, True)))
        self.assertEqual(calls[4], (session, 'NotifyPointerAxis', (0.0, 10.0, 4)))
        self.assertEqual(calls[5:9], [(session, 'NotifyKeyboardKeysym', (0x41, True)), (session, 'NotifyKeyboardKeysym', (0x41, False)),
                                      (session, 'NotifyKeyboardKeysym', (0x010020ac, True)), (session, 'NotifyKeyboardKeysym', (0x010020ac, False))])
        self.assertEqual(calls[9], (session, 'NotifyPointerAxis', (0.0, 0.0, 5)))
        clock[0] = 59
        self.assertFalse(injector.reap())
        clock[0] = 61
        self.assertTrue(injector.reap())
        self.assertEqual(calls[-2:], [(session, 'NotifyPointerButton', (0x111, False)), (session, 'Stop', ())])
        injector(PEER, [{'type': 'move', 'dx': 1, 'dy': 1}])
        self.assertEqual(sum(method == 'CreateSession' for _, method, _ in calls), 2)
        locked = InputInjector(call, locked=lambda: True, timer=False)
        count = len(calls)
        self.assertIs(locked(PEER, [{'type': 'text', 'text': 'secret'}]), False)
        self.assertEqual(len(calls), count)
        adapters = DesktopAdapters(Path(self.temp.name), downloads=Path(self.temp.name) / 'Downloads', input=locked)
        self.assertEqual(adapters.input_events(PEER, {'events': [{'type': 'move', 'dx': 1, 'dy': 1}]}), {'error': 'needs-user'})
        self.assertEqual(REMOTE_DESKTOP_SESSION, 'org.gnome.Mutter.RemoteDesktop.Session')


class AvahiTest(unittest.TestCase):
    def test_publish_collision_and_stop(self):
        calls, handlers = [], []
        def call(path, interface, method, signature, values):
            calls.append((path, method, signature, values))
            if method == 'EntryGroupNew': return ('/Client1/EntryGroup1',)
            if method == 'GetAlternativeServiceName': return (values[0] + ' #2',)
            return ()
        publisher = AvahiPublisher(47810, call=call, subscribe=lambda path, handler: (handlers.append(handler), lambda: handlers.clear())[1],
                                   name='Luma Connect 0a0b0c')
        publisher.start()
        self.assertEqual(calls[1], ('/Client1/EntryGroup1', 'AddService', '(iiussssqaay)',
                                    (-1, -1, 0, 'Luma Connect 0a0b0c', '_luma-connect._tcp', '', '', 47810, [b'v=1'])))
        self.assertEqual(calls[2][1], 'Commit')
        handlers[0](3, '')
        self.assertEqual([entry[1] for entry in calls[3:]], ['GetAlternativeServiceName', 'Reset', 'AddService', 'Commit'])
        self.assertEqual(calls[5][3][3], 'Luma Connect 0a0b0c #2')
        publisher.stop()
        self.assertEqual(calls[-1][1], 'Free')
        self.assertEqual(handlers, [])


class AddressTest(unittest.TestCase):
    def test_only_explicit_private_unicast_addresses(self):
        self.assertEqual(select_addresses(['0.0.0.0', '127.0.0.1', '8.8.8.8', 'fe80::1', 'fd00::5', '192.168.1.20',
                                           '10.0.0.2', '192.168.1.20', '224.0.0.1', 'nonsense', '172.16.0.1']),
                         ['192.168.1.20', '10.0.0.2', '172.16.0.1', 'fd00::5'])


class InvokeAllowlistTest(unittest.TestCase):
    def test_only_reviewed_capabilities_and_strict_payloads(self):
        self.assertEqual(validate_invoke('device.ring', '{"ring":true}'), {'ring': True})
        self.assertEqual(validate_invoke('notifications.act', json.dumps({'key': KEY, 'action': 'dismiss'}))['action'], 'dismiss')
        self.assertEqual(validate_invoke('media.control', '{"command":"seek","position_ms":5}')['position_ms'], 5)
        for capability in ('files.write', 'input.control', 'messages.send', 'camera.stream', 'screen.control'):
            with self.subTest(capability=capability), self.assertRaises(PermissionError):
                validate_invoke(capability, '{}')
        for capability, raw in [('clipboard.write', json.dumps({'text': 'x' * (64 * 1024)})),
                                ('device.ring', '{"ring":true,"ring":false}'), ('device.ring', '[]'),
                                ('media.control', '{"command":"seek","position_ms":1.5}'),
                                ('links.open', '{"url":"file:///etc/passwd"}'),
                                ('notifications.act', json.dumps({'key': KEY, 'action': 'open', 'operation': KEY})),
                                ('media.control', '{"command":"eject"}')]:
            with self.subTest(capability=capability, raw=raw[:40]), self.assertRaises(ValueError):
                validate_invoke(capability, raw)


class FakePhone:
    """A phone endpoint: the real Receiver and journal with DesktopAdapters as its effects."""

    def __init__(self, directory, desktop, downloads, events):
        self.directory, self.desktop = directory, desktop
        self.adapters = DesktopAdapters(directory, downloads=downloads,
                                        ring=lambda value: events.append(('ring', value)))
        self.chunks = []
        original = self.adapters.file
        self.adapters.file = lambda peer, payload: (self.chunks.append((payload['offset'], payload['final'])), original(peer, payload))[1]
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0)); self.socket.listen(4)
        self.port = self.socket.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def approve(self, epoch, incoming, outgoing):
        pin = transport.fingerprint((self.desktop / 'device.pem').read_text())
        (self.directory / 'peers').mkdir(mode=0o700, exist_ok=True)
        (self.directory / 'peers' / (pin + '.pem')).write_text((self.desktop / 'device.pem').read_text())
        journal = Journal(self.directory / 'continuity.db')
        try: journal.approve(pin, epoch, set(incoming), outgoing_grants=set(outgoing))
        finally: journal.close()
        Registry(self.directory).devices()  # the phone's registry table exists before dispatch, as on a real endpoint
        self.pin = pin
        self.thread.start()

    def run(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.socket, selectors.EVENT_READ)
            while not self.stop.is_set():
                if not selector.select(timeout=.1): continue
                raw, _ = self.socket.accept()
                with raw:
                    tls = transport.context(self.directory / 'device.pem', self.directory / 'device.key',
                                            self.directory / 'peers' / (self.pin + '.pem'), server=True)
                    try:
                        with bounded_stream(raw, tls, server=True, timeout=5) as stream:
                            peer = transport.authenticate(stream, self.pin)
                            journal = Journal(self.directory / 'continuity.db')
                            try: Receiver(journal, self.adapters.for_peer(peer)).serve_one(stream, peer)
                            finally: journal.close()
                    except (OSError, ssl.SSLError, ValueError):
                        pass

    def close(self):
        self.stop.set()
        if self.thread.is_alive(): self.thread.join(5)
        self.socket.close()


def pair_over_tls(port, desktop_pin, phone, uri_token, listen_port, p2d, d2p):
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE  # the pin is compared below, as the phone does
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.set_alpn_protocols([PAIRING_ALPN])
    request = {'schema': SCHEMA, 'kind': 'request', 'token': uri_token, 'certificate': (phone / 'device.pem').read_text(),
               'pin': transport.fingerprint((phone / 'device.pem').read_text()), 'name': 'Pixel 9',
               'model': 'Google Pixel 9', 'platform': 'android', 'listen_port': listen_port,
               'phone_to_desktop': p2d, 'desktop_to_phone': d2p}
    with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
        with tls.wrap_socket(raw) as stream:
            assert hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() == desktop_pin
            transport.send(stream, request)
            return transport.receive(stream)


class CompanionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.desktop, self.phone = self.root / 'desktop', self.root / 'phone'
        create_identity(self.phone)
        self.events, self.changes, self.lock = [], [], threading.Lock()
        def changed():
            with self.lock: self.changes.append(time.monotonic())
        self.service = CompanionService(self.desktop, downloads=self.root / 'Downloads', name='Desk',
                                        dispatch=lambda work: work(), submit=lambda work: work(),
                                        submit_transfer=lambda work: work(), changed=changed,
                                        addresses=lambda: ['127.0.0.1'], port=0)
        self.addCleanup(self.service.close)
        self.fake = FakePhone(self.phone, self.desktop, self.root / 'PhoneDownloads', self.events)
        self.addCleanup(self.fake.close)

    def tearDown(self):
        self.temp.cleanup()

    def pair(self):
        uris = []
        self.service.start_pairing(list(DEFAULT_PHONE_TO_DESKTOP), list(DEFAULT_DESKTOP_TO_PHONE), uris.append)
        uri = uris[0]
        self.assertTrue(uri.startswith('luma-connect://pair?v=1&host=127.0.0.1&port='))
        self.assertTrue(self.service.snapshot()['companion_listening'])
        fields = dict(part.split('=', 1) for part in uri.split('?', 1)[1].split('&'))
        response = pair_over_tls(int(fields['port']), fields['pin'], self.phone, fields['token'], self.fake.port,
                                 ['device.status', 'files.write'], ['device.ring', 'files.write', 'clipboard.write'])
        self.assertEqual(response['kind'], 'accepted')
        for _ in range(100):
            state = self.service.snapshot()['companion_pairing']
            if state and state['state'] == 'paired': break
            time.sleep(.02)
        self.assertEqual(state['sas'], response['sas'])
        self.fake.approve(response['epoch'], response['desktop_to_phone'], response['phone_to_desktop'])
        return transport.fingerprint((self.phone / 'device.pem').read_text())

    def test_pairing_invoke_allowlist_and_removal(self):
        phone = self.pair()
        devices = self.service.snapshot()['companion_devices']
        self.assertEqual([(row['fingerprint'], row['name'], row['outgoing']) for row in devices],
                         [(phone, 'Pixel 9', ['clipboard.write', 'device.ring', 'files.write'])])
        receipts = []
        self.service.invoke(phone, 'device.ring', '{"ring":true}', receipts.append)
        self.assertEqual(receipts[0], {'state': 'complete', 'result': {'accepted': True}})
        self.assertEqual(self.events, [('ring', True)])
        with self.assertRaises(PermissionError):
            self.service.invoke(phone, 'files.write', '{}', receipts.append)
        self.service.invoke(phone, 'links.open', '{"url":"https://example.org"}', receipts.append)
        self.assertIsNone(receipts[1])  # not granted on the pairing: refused before transmission
        self.service.cancel_pairing()
        self.assertIsNone(self.service.snapshot()['companion_pairing'])
        self.service.remove(phone)
        self.assertEqual(self.service.snapshot()['companion_devices'], [])
        self.assertFalse(self.service.snapshot()['companion_listening'])
        self.assertTrue(Registry(self.desktop).devices()[0]['revoked'])

    def test_file_is_sent_in_ordered_verified_chunks(self):
        phone = self.pair()
        data = secrets.token_bytes(FILE_CHUNK * 2 + 1000)
        source = self.root / 'holiday photo.jpg'
        source.write_bytes(data)
        transfer = self.service.send_file(phone, str(source))
        row = next(row for row in self.service.snapshot()['companion_transfers'] if row['transfer'] == transfer)
        self.assertEqual((row['state'], row['sent'], row['size']), ('complete', len(data), len(data)))
        self.assertEqual(self.fake.chunks, [(0, False), (FILE_CHUNK, False), (FILE_CHUNK * 2, True)])
        self.assertEqual((self.root / 'PhoneDownloads' / 'holiday photo.jpg').read_bytes(), data)
        with self.assertRaises(ValueError):
            self.service.send_file(phone, 'relative.txt')

    def test_notification_effects_are_gated_by_outgoing_grants(self):
        calls = []
        self.service.call = lambda directory, fingerprint, capability, payload: (calls.append((capability, payload)),
                                                                                  {'state': 'complete', 'result': {'state': 'complete'}})[1]
        self.service.devices = [{'fingerprint': PEER, 'name': 'Pixel 9', 'outgoing': ['media.control']}]
        self.service.effect_call(PEER, 'notifications.act', {'key': KEY, 'action': 'dismiss'})
        self.service.effect_call(PEER, 'media.control', {'command': 'pause'})
        self.assertEqual(calls, [('media.control', {'command': 'pause'})])
        self.service.request_reply(PEER, {'key': KEY, 'action': REPLY, 'label': 'Reply', 'title': 'Sam', 'app': 'Messages', 'device': 'Pixel 9'})
        self.assertEqual(self.service.snapshot()['companion_reply']['key'], KEY)
        self.service.invoke(PEER, 'notifications.act', json.dumps({'key': KEY, 'action': REPLY, 'text': 'On my way'}), lambda _: None)
        self.assertIsNone(self.service.snapshot()['companion_reply'])


class SendFileTest(unittest.TestCase):
    def test_cancel_between_chunks_and_empty_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'big.bin'
            path.write_bytes(b'x' * (FILE_CHUNK + 1))
            sent, stop = [], [False]
            def call(fingerprint, capability, payload):
                sent.append(payload['offset'])
                size = len(base64.b64decode(payload['data']))
                return {'state': 'complete', 'result': {'received': payload['offset'] + size, 'name': 'big.bin'}}
            def progress(offset, size): stop[0] = True
            with self.assertRaises(Cancelled):
                send_file(call, PEER, str(path), cancelled=lambda: stop[0], progress=progress)
            self.assertEqual(sent, [0])
            empty = Path(temp) / 'empty.txt'
            empty.write_bytes(b'')
            payloads = []
            send_file(lambda fingerprint, capability, payload: (payloads.append(payload),
                      {'state': 'complete', 'result': {'received': 0, 'name': 'empty.txt'}})[1], PEER, str(empty))
            self.assertEqual((payloads[0]['final'], payloads[0]['sha256']), (True, hashlib.sha256(b'').hexdigest()))
            with self.assertRaises(ValueError):
                send_file(lambda *_: {'state': 'complete', 'result': {'error': 'invalid-request'}}, PEER, str(path))


if __name__ == '__main__':
    unittest.main()
