"""CompanionService camera/screen sessions and Power Mode orchestration, with fakes."""
from pathlib import Path
import tempfile
import unittest

from luma_continuity.bootstrap import create_identity
from luma_continuity.companion_desktop import CompanionService
from luma_continuity.companion_power import MdnsService, PowerLinks, Result

PHONE = 'a' * 64


class FakeMedia:
    def __init__(self):
        self.opened, self.closed, self.closed_peers = [], [], []

    def open_session(self, peer, kind, control, codecs=('h264',)):
        self.opened.append((peer, kind, control))
        self.codecs = list(codecs)
        return 'b' * 32, 40123

    def close(self, session_id):
        self.closed.append(session_id)

    def close_peer(self, peer):
        self.closed_peers.append(peer)

    def sessions(self):
        return []

    def close_all(self):
        pass


class FakeAdb:
    def __init__(self):
        self.calls = []
        self.discoveries = 0

    def discover(self):
        self.discoveries += 1
        services = []
        if self.discoveries >= 2:
            services.append(MdnsService(self.expected, '_adb-tls-pairing._tcp', '192.168.1.40', 37001))
        if self.calls and self.calls[-1][0] == 'pair':
            services.append(MdnsService('adb-XYZ', '_adb-tls-connect._tcp', '192.168.1.40', 41234))
        return Result(True, 'ok', {'services': services})

    def pair(self, host, port, password):
        self.calls.append(('pair', host, port))
        return Result(True, 'paired', {})

    def connect(self, host, port):
        self.calls.append(('connect', host, port))
        return Result(True, 'connected', {'serial': f'{host}:{port}'})

    def forget(self, serial):
        self.calls.append(('forget', serial))
        return Result(True, 'disconnected', {})


class FakeScrcpy:
    def __init__(self):
        self.calls = []

    def mirror(self, serial, **options):
        self.calls.append(('mirror', serial, options))
        return Result(True, 'launched', {})

    def app_window(self, serial, package, **options):
        self.calls.append(('app', serial, package, options))
        return Result(True, 'launched', {})


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'desktop'
        create_identity(self.directory)
        self.receipts, self.done = [], []
        self.media, self.adb, self.scrcpy = FakeMedia(), FakeAdb(), FakeScrcpy()
        self.clock = [1000.0]

        def call(directory, fingerprint, capability, payload, **_):
            self.receipts.append((capability, payload))
            return self.answer

        self.answer = {'state': 'complete', 'result': {'error': 'needs-user'}}
        self.service = CompanionService(self.directory, downloads=Path(self.temp.name) / 'Downloads', name='Desk',
                                        dispatch=lambda work: work(), submit=lambda work: work(),
                                        submit_transfer=lambda work: work(), changed=lambda: None,
                                        call=call, addresses=lambda: ['127.0.0.1'], media_sessions=self.media,
                                        adb=self.adb, scrcpy=self.scrcpy, now=lambda: self.clock[0],
                                        sleep=lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds))
        self.addCleanup(self.service.close)
        self.service.devices = [{'fingerprint': PHONE, 'name': 'Pixel 9', 'model': 'Pixel 9', 'platform': 'android',
                                 'paired_at': 1, 'seen_at': 1, 'status': None, 'incoming': [],
                                 'outgoing': ['camera.stream', 'screen.view', 'screen.control'], 'reachable': True}]

    def test_camera_request_waits_for_the_phone(self):
        self.service.start_media(PHONE, 'camera', self.done.append)
        self.assertEqual(self.done, ['needs-user'])
        self.assertEqual(self.media.opened, [(PHONE, 'camera', False)])
        capability, payload = self.receipts[0]
        self.assertEqual(capability, 'camera.stream')
        self.assertEqual((payload['session'], payload['port'], payload['facing']), ('b' * 32, 40123, 'back'))
        self.assertEqual(payload['codecs'], self.media.codecs)
        self.assertIn('vp8', payload['codecs'])
        self.assertEqual(self.media.closed, [])

    def test_screen_session_carries_control_only_when_granted(self):
        self.service.start_media(PHONE, 'screen', self.done.append)
        self.assertEqual(self.media.opened, [(PHONE, 'screen', True)])
        self.service.devices[0]['outgoing'] = ['screen.view']
        self.service.start_media(PHONE, 'screen', self.done.append)
        self.assertEqual(self.media.opened[-1], (PHONE, 'screen', False))

    def test_refused_request_closes_the_session(self):
        self.answer = {'state': 'complete', 'result': {'error': 'revoked'}}
        self.service.start_media(PHONE, 'camera', self.done.append)
        self.assertEqual(self.done, ['refused'])
        self.assertEqual(self.media.closed, ['b' * 32])

    def test_media_requires_grant(self):
        self.service.devices[0]['outgoing'] = []
        with self.assertRaises(PermissionError):
            self.service.start_media(PHONE, 'camera', self.done.append)

    def test_power_mode_flow_with_known_service(self):
        self.assertEqual(self.service.power_open(PHONE), 'not-set-up')
        original = __import__('luma_continuity.companion_power', fromlist=['wifi_pairing_qr'])
        saved = original.wifi_pairing_qr
        original.wifi_pairing_qr = lambda: ('WIFI:T:ADB;S:studio-abcdefghij;P:Password1234;;', 'studio-abcdefghij', 'Password1234')
        self.addCleanup(setattr, original, 'wifi_pairing_qr', saved)
        self.adb.expected = 'studio-abcdefghij'
        self.service.power_pair(PHONE, self.done.append)
        self.assertEqual(self.done, ['ready'])
        self.assertEqual(self.adb.calls[:2], [('pair', '192.168.1.40', 37001), ('connect', '192.168.1.40', 41234)])
        self.assertEqual(PowerLinks(self.directory).serial_for(PHONE), '192.168.1.40:41234')
        self.assertEqual(self.service.snapshot()['companion_power'][PHONE], {'state': 'ready'})
        self.assertEqual(self.service.power_open(PHONE), 'launched')
        self.assertEqual(self.scrcpy.calls[-1][:2], ('mirror', '192.168.1.40:41234'))
        self.assertEqual(self.service.power_open(PHONE, 'org.example.notes'), 'launched')
        self.assertEqual(self.scrcpy.calls[-1][:3], ('app', '192.168.1.40:41234', 'org.example.notes'))

    def test_power_mode_times_out_without_the_phone(self):
        self.adb.expected = 'studio-never'
        self.adb.discoveries = -10_000
        self.service.power_pair(PHONE, self.done.append, window=10)
        self.assertEqual(self.done, ['not-found'])

    def test_power_mode_requires_a_paired_phone(self):
        with self.assertRaises(PermissionError):
            self.service.power_pair('c' * 64, self.done.append)


if __name__ == '__main__':
    unittest.main()
