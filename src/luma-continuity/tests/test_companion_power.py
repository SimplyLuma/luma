"""Synthetic tests for Power Mode. Fake subprocess runners only; adb and scrcpy never run."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from luma_continuity.companion_power import (CONNECT_SERVICE, PAIRING_SERVICE, Adb, MdnsService, PowerLinks,
                                             ScrcpyLauncher, find_connect_service, find_pairing_service,
                                             forget_companion, parse_mdns_services, wifi_pairing_qr)

# Shapes built from AOSP adb: docs/dev/adb_wifi.md (space-aligned example) and
# client/transport_mdns.cpp / mdnsresponder_client.cpp ("%s\t%s\t%s:%u\n"; the Bonjour
# backend reports the DNS-SD regtype with its trailing dot, as test_adb.py expects).
DOC_SAMPLE = """List of discovered mdns services
adb-14141FDF600081         _adb._tcp\t          192.168.86.38:5555
adb-14141FDF600081-QXjCrW  _adb-tls-pairing._tcp  192.168.86.38:33861
adb-14141FDF600081-TnSdi9  _adb-tls-connect._tcp  192.168.86.38:33015
studio-g@<xeYnap/          _adb-tls-pairing._tcp  192.168.86.39:55861
"""
TAB_SAMPLE = ("List of discovered mdns services\n"
              "studio-k3j9x0a2m1\t_adb-tls-pairing._tcp.\t192.168.1.20:37111\n"
              "adb-R5CT1234ABC-Zx9q1a\t_adb-tls-connect._tcp.\t192.168.1.20:41999\n"
              "Living Room TV\t_adb-tls-connect._tcp\t192.168.1.30:40000\n"
              "\n"
              "bogus line without fields\n"
              "adb-x\t_ipp._tcp\t192.168.1.9:631\n"
              "adb-y\t_adb-tls-connect._tcp\t192.168.1.300:5555\n"
              "adb-z\t_adb-tls-connect._tcp\t192.168.1.5:70000\n"
              "adb-w\t_adb-tls-connect._tcp\t0.0.0.0:5555\n")


class FakeRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, *, input=None, timeout, env=None):
        self.calls.append(SimpleNamespace(args=args, input=input, timeout=timeout, env=env))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        stdout, stderr, code = response if isinstance(response, tuple) else (response, '', 0)
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


class QrTest(unittest.TestCase):
    def test_format_matches_android_studio(self):
        text, service, password = wifi_pairing_qr()
        self.assertRegex(service, r'\Astudio-[a-z0-9]{10}\Z')
        self.assertRegex(password, r'\A[A-Za-z0-9]{12}\Z')
        self.assertEqual(text, f'WIFI:T:ADB;S:{service};P:{password};;')
        self.assertNotEqual(wifi_pairing_qr()[1:], (service, password))


class MdnsParsingTest(unittest.TestCase):
    def test_documented_output(self):
        services = parse_mdns_services(DOC_SAMPLE)
        self.assertEqual(services, [
            MdnsService('adb-14141FDF600081', '_adb._tcp', '192.168.86.38', 5555),
            MdnsService('adb-14141FDF600081-QXjCrW', PAIRING_SERVICE, '192.168.86.38', 33861),
            MdnsService('adb-14141FDF600081-TnSdi9', CONNECT_SERVICE, '192.168.86.38', 33015),
            MdnsService('studio-g@<xeYnap/', PAIRING_SERVICE, '192.168.86.39', 55861)])

    def test_tab_output_trailing_dots_and_garbage(self):
        services = parse_mdns_services(TAB_SAMPLE)
        self.assertEqual([(item.name, item.service, item.host, item.port) for item in services], [
            ('studio-k3j9x0a2m1', PAIRING_SERVICE, '192.168.1.20', 37111),
            ('adb-R5CT1234ABC-Zx9q1a', CONNECT_SERVICE, '192.168.1.20', 41999),
            ('Living Room TV', CONNECT_SERVICE, '192.168.1.30', 40000)])
        self.assertEqual(find_pairing_service(services, 'studio-k3j9x0a2m1').port, 37111)
        self.assertIsNone(find_pairing_service(services, 'studio-other00000'))
        self.assertEqual(find_connect_service(services, '192.168.1.20').port, 41999)
        self.assertIsNone(find_connect_service(services, '192.168.1.99'))
        self.assertEqual(parse_mdns_services(None), [])
        self.assertEqual(parse_mdns_services('List of discovered mdns services\n'), [])


class AdbTest(unittest.TestCase):
    ADB = '/usr/bin/adb'

    def adb(self, *responses):
        runner = FakeRunner(*responses)
        return Adb(self.ADB, runner=runner), runner

    def test_missing_adb_is_reported(self):
        adb = Adb(which=lambda name: None, runner=FakeRunner())
        self.assertFalse(adb.available)
        for result in (adb.pair('192.168.1.20', 37111, 'abcdef123456'), adb.connect('192.168.1.20', 5555),
                       adb.discover(), adb.forget('192.168.1.20:5555'), adb.state('x')):
            self.assertEqual((result.ok, result.code), (False, 'adb-missing'))
        adb, _ = self.adb(FileNotFoundError(2, 'No such file', 'adb'))
        self.assertEqual(adb.discover().code, 'adb-missing')
        self.assertEqual(Adb(which=lambda name: '/opt/adb').path, '/opt/adb')

    def test_pair_keeps_password_out_of_argv(self):
        adb, runner = self.adb('Enter pairing code: Successfully paired to 192.168.1.20:37111 [guid=adb-R5CT1234ABC-Zx9q1a]\n')
        result = adb.pair('192.168.1.20', 37111, 'Secret123abc')
        self.assertEqual((result.ok, result.code, result.data), (True, 'paired', {'guid': 'adb-R5CT1234ABC-Zx9q1a'}))
        call = runner.calls[0]
        self.assertEqual(call.args, [self.ADB, 'pair', '192.168.1.20:37111'])
        self.assertEqual(call.input, 'Secret123abc\n')
        self.assertNotIn('Secret123abc', ' '.join(call.args))
        self.assertLessEqual(call.timeout, 30)

    def test_pair_failures_map_to_codes(self):
        adb, runner = self.adb('Enter pairing code: Failed: Wrong password or connection was dropped.',
                               ('', 'error: protocol fault (couldn\'t read status): Connection reset by peer', 1),
                               subprocess.TimeoutExpired(['adb'], 30))
        self.assertEqual(adb.pair('192.168.1.20', 37111, 'Secret123abc').code, 'wrong-password')
        self.assertEqual(adb.pair('192.168.1.20', 37111, 'Secret123abc').code, 'pair-failed')
        self.assertEqual(adb.pair('192.168.1.20', 37111, 'Secret123abc').code, 'timeout')
        for host, port, password in (('not-an-ip', 1, 'Secret123abc'), ('224.0.0.1', 1, 'Secret123abc'),
                                     ('192.168.1.20', 0, 'Secret123abc'), ('192.168.1.20', 1, 'short'),
                                     ('192.168.1.20', 1, 'has space 123'), ('192.168.1.20', '1', 'Secret123abc')):
            self.assertEqual(adb.pair(host, port, password).code, 'invalid-argument')
        self.assertEqual(len(runner.calls), 3)

    def test_connect_and_ipv6_endpoint(self):
        adb, runner = self.adb('connected to 192.168.1.20:41999\n', 'already connected to [fe80::1]:5555\n',
                               "failed to connect to '192.168.1.20:41999': Connection refused\n")
        result = adb.connect('192.168.1.20', 41999)
        self.assertEqual((result.code, result.data['serial']), ('connected', '192.168.1.20:41999'))
        self.assertEqual(adb.connect('fe80::1', 5555).code, 'already-connected')
        self.assertEqual(runner.calls[1].args, [self.ADB, 'connect', '[fe80::1]:5555'])
        self.assertEqual(adb.connect('192.168.1.20', 41999).code, 'connect-failed')
        self.assertIsNone(runner.calls[0].input)

    def test_discover_and_forget(self):
        adb, runner = self.adb(TAB_SAMPLE, 'unexpected', 'disconnected 192.168.1.20:41999\n',
                               ('', "error: no such device '192.168.1.20:41999'", 1), 'device\n')
        self.assertEqual(len(adb.discover().data['services']), 3)
        self.assertEqual(runner.calls[0].args, [self.ADB, 'mdns', 'services'])
        self.assertEqual(adb.discover().code, 'mdns-unavailable')
        self.assertEqual(adb.forget('192.168.1.20:41999').code, 'disconnected')
        self.assertEqual(runner.calls[2].args, [self.ADB, 'disconnect', '192.168.1.20:41999'])
        self.assertEqual((adb.forget('192.168.1.20:41999').ok), True)
        self.assertEqual(adb.state('adb-R5CT1234ABC-Zx9q1a._adb-tls-connect._tcp').data['state'], 'device')
        for serial in ('-a', '--help', '', 'a b', 'x;rm', None):
            self.assertEqual(adb.forget(serial).code, 'invalid-argument')
        self.assertEqual(len(runner.calls), 5)


class PowerLinksTest(unittest.TestCase):
    peer = 'd' * 64

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'identity'
        self.directory.mkdir(mode=0o700)

    def test_link_file_is_private_and_atomic(self):
        links = PowerLinks(self.directory)
        self.assertEqual(links.links(), {})
        links.link(self.peer, '192.168.1.20:41999', now=5)
        path = self.directory / 'power-mode.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_text(), '{"links":{"%s":{"linked_at":5,"serial":"192.168.1.20:41999"}},"version":1}' % self.peer)
        self.assertEqual([item.name for item in self.directory.iterdir()], ['power-mode.json'])
        self.assertEqual(PowerLinks(self.directory).serial_for(self.peer), '192.168.1.20:41999')
        with self.assertRaises(ValueError):
            links.link('not-a-pin', '192.168.1.20:41999')
        with self.assertRaises(ValueError):
            links.link(self.peer, '-oProxyCommand')

    def test_unsafe_files_are_refused(self):
        links = PowerLinks(self.directory)
        links.link(self.peer, '192.168.1.20:41999')
        path = self.directory / 'power-mode.json'
        path.chmod(0o644)
        with self.assertRaises(PermissionError):
            links.links()
        path.unlink()
        target = Path(self.temp.name) / 'elsewhere.json'
        target.write_text('{"version":1,"links":{}}')
        target.chmod(0o600)
        path.symlink_to(target)
        with self.assertRaises(PermissionError):
            links.links()
        path.unlink()
        self.directory.chmod(0o755)
        try:
            with self.assertRaises(PermissionError):
                links.link(self.peer, '192.168.1.20:41999')
        finally:
            self.directory.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('{"version":1,"links":{"%s":{"serial":"-x","linked_at":1}}}' % self.peer)
        with self.assertRaises(ValueError):
            links.links()

    def test_unpairing_companion_disconnects_adb(self):
        PowerLinks(self.directory).link(self.peer, '192.168.1.20:41999')
        runner = FakeRunner('disconnected 192.168.1.20:41999\n')
        result = forget_companion(self.directory, self.peer, adb=Adb('/usr/bin/adb', runner=runner))
        self.assertEqual((result.ok, result.code, result.data['serial']), (True, 'disconnected', '192.168.1.20:41999'))
        self.assertEqual(runner.calls[0].args, ['/usr/bin/adb', 'disconnect', '192.168.1.20:41999'])
        self.assertIsNone(PowerLinks(self.directory).serial_for(self.peer))
        self.assertEqual(forget_companion(self.directory, self.peer, adb=Adb('/usr/bin/adb', runner=FakeRunner())).code, 'not-linked')


class ScrcpyTest(unittest.TestCase):
    PATH = '/usr/bin/scrcpy'
    SERIAL = '192.168.1.20:41999'

    def launcher(self, *responses):
        self.popen_calls = []

        def popen(args, **options):
            self.popen_calls.append((args, options))
            return SimpleNamespace(pid=42)
        return ScrcpyLauncher(self.PATH, adb_path='/usr/bin/adb', runner=FakeRunner(*responses), popen=popen)

    def test_mirror_arguments(self):
        launcher = self.launcher()
        self.assertEqual(launcher.mirror_args(self.SERIAL, title='Pixel\n9', codec='h265', max_size=1920, bit_rate=16_000_000), [
            self.PATH, '--serial=192.168.1.20:41999', '--video-codec=h265', '--max-size=1920',
            '--video-bit-rate=16000000', '--window-title=Pixel9', '--keyboard=uhid', '--mouse=uhid'])
        self.assertIn('--no-audio', launcher.mirror_args(self.SERIAL, title='P', audio=False))
        dup = launcher.mirror_args(self.SERIAL, title='P', audio='dup')
        self.assertEqual(dup[3:5], ['--audio-source=playback', '--audio-dup'])
        for options in ({'codec': 'av1'}, {'max_size': 10}, {'bit_rate': 1}, {'audio': 1}, {'audio': 'loud'},
                        {'keyboard': 'aoa'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                launcher.mirror_args(self.SERIAL, title='P', **options)
        with self.assertRaises(ValueError):
            launcher.mirror_args('--otg', title='P')

    def test_app_window_arguments(self):
        launcher = self.launcher()
        self.assertEqual(launcher.app_args(self.SERIAL, 'org.videolan.vlc', title='VLC', width=1280, height=800, dpi=240), [
            self.PATH, '--serial=192.168.1.20:41999', '--video-codec=h264', '--no-audio', '--window-title=VLC',
            '--new-display=1280x800/240', '--start-app=org.videolan.vlc', '--no-vd-system-decorations'])
        self.assertEqual(launcher.app_args(self.SERIAL, 'com.android.settings', title='S', decorations=True, flex=True)[-1],
                         '--flex-display')
        for package in ('+org.videolan.vlc', '?firefox', '-x', 'vlc', 'org..vlc', 'org.vlc;id'):
            with self.subTest(package=package), self.assertRaises(ValueError):
                launcher.app_args(self.SERIAL, package, title='X')
        with self.assertRaises(ValueError):
            launcher.app_args(self.SERIAL, 'org.videolan.vlc', title='X', dpi=10)

    def test_version_gate_and_launch(self):
        launcher = self.launcher('scrcpy 2.7 <https://github.com/Genymobile/scrcpy>\n',
                                 'scrcpy 3.3.4 <https://github.com/Genymobile/scrcpy>\n\nDependencies (compiled / linked):\n',
                                 'scrcpy 3.3.4 <https://github.com/Genymobile/scrcpy>\n',
                                 'scrcpy 4.1 <https://github.com/Genymobile/scrcpy>\n', 'garbage',
                                 'scrcpy 4.1 <https://github.com/Genymobile/scrcpy>\n')
        old = launcher.app_window(self.SERIAL, 'org.videolan.vlc', title='VLC')
        self.assertEqual((old.ok, old.code, old.data['version']), (False, 'unsupported-version', (2, 7, 0)))
        self.assertEqual(self.popen_calls, [])
        started = launcher.app_window(self.SERIAL, 'org.videolan.vlc', title='VLC')
        self.assertEqual((started.ok, started.code), (True, 'launched'))
        args, options = self.popen_calls[0]
        self.assertEqual(args[0], self.PATH)
        self.assertIn('--start-app=org.videolan.vlc', args)
        self.assertEqual(options['env']['ADB'], '/usr/bin/adb')
        self.assertNotIn('shell', options)
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertEqual(launcher.runner.calls[0].args, [self.PATH, '--version'])
        self.assertEqual(launcher.app_window(self.SERIAL, 'org.videolan.vlc', title='VLC', flex=True).code, 'unsupported-version')
        self.assertEqual(launcher.app_window(self.SERIAL, 'org.videolan.vlc', title='VLC', flex=True).code, 'launched')
        self.assertEqual(launcher.version().code, 'unknown-version')
        self.assertEqual(launcher.app_window(self.SERIAL, '-bad', title='VLC').code, 'invalid-argument')

    def test_missing_scrcpy_and_bad_launch(self):
        launcher = ScrcpyLauncher(which=lambda name: None, runner=FakeRunner())
        self.assertEqual(launcher.version().code, 'scrcpy-missing')
        self.assertEqual(launcher.mirror(self.SERIAL, title='P').code, 'scrcpy-missing')
        launcher = self.launcher(FileNotFoundError(2, 'missing'))
        self.assertEqual(launcher.version().code, 'scrcpy-missing')
        self.assertEqual(launcher.launch(['/bin/sh', '-c', 'true']).code, 'invalid-argument')
        self.assertEqual(launcher.mirror('bad serial', title='P').code, 'invalid-argument')
        self.assertTrue(re.fullmatch(r'launched', launcher.mirror(self.SERIAL, title='P').code))


if __name__ == '__main__':
    unittest.main()
