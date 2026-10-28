# SPDX-License-Identifier: Apache-2.0
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from luma_tester.core import Session, TesterError, digest, read_manifest
from luma_tester.__main__ import main


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luma-tester-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir(mode=0o700)
        env = patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.runtime)})
        env.start()
        self.addCleanup(env.stop)
        self.source = self.root / 'source.qcow2'
        self.source.write_bytes(b'fixture')
        self.m = {'schema': 1, 'id': 'mobile-test', 'target': 'mobile', 'arch': 'x86_64',
                  'build': 'test-only-no-os', 'boot': 'bios', 'cloud_init': True, 'user': 'luma',
                  'image': {'url': self.source.as_uri(), 'sha256': digest(self.source), 'bytes': self.source.stat().st_size}}
        self.path = self.root / 'manifest.json'
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.m))
        return Session(self.path, self.root / 'state')

    def fixture_disk(self):
        if not shutil.which('qemu-img'):
            self.skipTest('qemu-img not installed')
        self.source.unlink()
        subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', str(self.source), '32M'], check=True)
        self.m['image'].update(sha256=digest(self.source), bytes=self.source.stat().st_size)
        return self.save()

    def test_mac_native_acceleration_and_cross_arch_emulation(self):
        with patch('luma_tester.core.platform.system',return_value='Darwin'),patch('luma_tester.core.platform.machine',return_value='arm64'):
            session=self.save();command=session.command(headless=True,graphics='software')
            self.assertEqual(command[command.index('-accel')+1],'tcg,thread=multi')
            self.m.update(arch='aarch64',boot='uefi')
            session=self.save()
            with patch.object(session,'firmware',return_value='/test/firmware.fd'):
                command=session.command(headless=True,graphics='software')
                self.assertEqual(command[command.index('-accel')+1],'hvf')
                self.assertEqual(command[command.index('-cpu')+1],'host')
                command=session.command(headless=True,graphics='software',software=True)
                self.assertEqual(command[command.index('-accel')+1],'tcg,thread=multi')

    def test_https_redirect_cannot_downgrade(self):
        from urllib.request import Request
        from luma_tester.core import HTTPSRedirects
        request = Request('https://example.org/guest.qcow2')
        with self.assertRaises(TesterError):
            HTTPSRedirects().redirect_request(request, None, 302, 'Found', {}, 'http://example.org/guest.qcow2')

    def test_manifest_rejects_path_traversal(self):
        self.m['id'] = '../../other'
        with self.assertRaises(TesterError):
            self.save()

    def test_manifest_rejects_insecure_url(self):
        self.m['image']['url'] = 'http://example.org/disk'
        with self.assertRaises(TesterError):
            self.save()

    def test_manifest_rejects_incomplete_identity(self):
        self.m['build'] = ''
        with self.assertRaises(TesterError):
            self.save()

    def test_manifest_rejects_arm_bios(self):
        self.m['arch'] = 'aarch64'
        with self.assertRaises(TesterError):
            self.save()

    def test_mobile_image_can_declare_its_firmware_display_and_controls(self):
        self.m.update(arch='aarch64', boot='uefi',
            display=dict(width=864, height=1920, scale=2),
            capabilities=dict(home=False, dynamic_display=False))
        session=self.save()
        with patch.object(session,'firmware',return_value='/test/uefi.fd'):
            command=session.command(headless=True,graphics='software')
        self.assertIn('virtio-gpu-pci,edid=on,xres=864,yres=1920',command)
        with self.assertRaisesRegex(TesterError,'gesture bar'):session.home()
        from luma_tester import device_profiles
        with self.assertRaisesRegex(TesterError,'fixed phone display'):
            device_profiles.apply(session,device_profiles.DEFAULT)

    def test_manifest_rejects_unsafe_display_or_control_metadata(self):
        for field,value in [('display',dict(width=True,height=1920,scale=2)),
                ('display',dict(width=864,height=9000,scale=2)),
                ('display',dict(width=864,height=1920,scale=float('nan'))),
                ('capabilities',dict(home='false')),('capabilities',dict(command=True))]:
            with self.subTest(field=field,value=value):
                self.m[field]=value
                with self.assertRaises(TesterError):self.save()
                del self.m[field]

    def test_manifest_rejects_size_and_digest(self):
        for key, value in [('bytes', True), ('bytes', -1), ('sha256', 'not-a-hash')]:
            with self.subTest(key=key, value=value):
                old = self.m['image'][key]
                self.m['image'][key] = value
                with self.assertRaises(TesterError):
                    self.save()
                self.m['image'][key] = old

    def test_runtime_must_be_private(self):
        self.runtime.chmod(0o755)
        with self.assertRaises(TesterError):
            self.save()

    def test_session_lock_excludes_second_owner(self):
        session = self.save()
        with session.lock():
            with self.assertRaises(TesterError):
                with self.save().lock():
                    pass

    def test_different_images_have_separate_locks(self):
        first = self.save()
        self.m['id'] = 'other'
        second = self.save()
        with first.lock(), second.lock():
            self.assertNotEqual(first.qmp_path, second.qmp_path)

    def test_portrait_and_desktop_modes(self):
        command = self.save().command(software=True, graphics='software')
        self.assertIn('virtio-gpu-pci,edid=on,xres=432,yres=960', command)
        self.assertIn('tcg,thread=multi', command)
        self.m['target'] = 'desktop'
        self.assertIn('virtio-gpu-pci,edid=on,xres=1920,yres=1080', self.save().command(graphics='software'))

    def test_embedded_gpu_uses_private_display_and_egl(self):
        with patch.object(Session, 'render_device', return_value='/dev/dri/renderD128'):
            argv = self.save().command(embedded=True)
        self.assertIn('virtio-gpu-gl-pci,edid=on,xres=432,yres=960', argv)
        self.assertIn('egl-headless,rendernode=/dev/dri/renderD128', argv)
        self.assertTrue(argv[argv.index('-vnc') + 1].startswith('unix:'))

    def test_no_gpu_and_explicit_software_work_without_render_device(self):
        with patch.object(Session, 'render_device', return_value=None):
            self.assertIn('virtio-gpu-pci,edid=on,xres=432,yres=960', self.save().command())
            with self.assertRaisesRegex(TesterError, 'render device'):
                self.save().command(graphics='virgl')
        with patch.object(Session, 'render_device', side_effect=AssertionError('must not open GPU')):
            self.save().command(graphics='software', embedded=True)

    def test_headless_builder_remains_software_by_default(self):
        with patch.object(Session, 'render_device', return_value='/dev/dri/renderD128'):
            argv = self.save().command(headless=True)
        self.assertIn('none', argv)
        self.assertNotIn('virtio-gpu-gl-pci,edid=on,xres=432,yres=960', argv)

    def test_cli_forwards_graphics_selection(self):
        with patch.object(Session, 'start', return_value=0) as start, patch('sys.argv',
                ['luma-tester', '--state', str(self.root / 'state'), 'start', str(self.path),
                 '--graphics', 'software', '--render-node', '/dev/dri/renderD129']):
            self.assertEqual(main(), 0)
        self.assertEqual(start.call_args.kwargs['graphics'], 'software')
        self.assertEqual(start.call_args.kwargs['render_node'], '/dev/dri/renderD129')

    def test_auto_graphics_retries_software_when_egl_fails(self):
        session = self.save()
        session.directory.mkdir(parents=True)
        with patch.object(session, '_prepare'), \
             patch.object(session, 'render_device', return_value='/dev/dri/renderD128'), \
             patch.object(session, 'probe_graphics', side_effect=TesterError('EGL failed')), \
             patch('subprocess.Popen') as child:
            child.return_value.wait.return_value = 0
            child.return_value.poll.return_value = 0
            self.assertEqual(session.start(embedded=True), 0)
        argv = child.call_args.args[0]
        self.assertIn('virtio-gpu-pci,edid=on,xres=432,yres=960', argv)
        self.assertEqual(json.loads((session.directory / 'graphics.json').read_text())['reason'], 'EGL failed')

    def test_explicit_virgl_failure_never_silently_uses_software(self):
        session = self.save()
        with patch.object(session, '_prepare'), \
             patch.object(session, 'render_device', return_value='/dev/dri/renderD128'), \
             patch.object(session, 'probe_graphics', side_effect=TesterError('EGL failed')), \
             patch('subprocess.Popen') as child:
            with self.assertRaisesRegex(TesterError, 'EGL failed'):
                session.start(embedded=True, graphics='virgl')
        child.assert_not_called()

    def test_cross_arch_never_uses_kvm(self):
        self.m.update(arch='aarch64', boot='uefi')
        with patch('platform.machine', return_value='x86_64'), patch('os.access', return_value=True):
            argv = self.save().command(firmware=str(self.source))
        self.assertIn('qemu-system-aarch64', argv)
        self.assertIn('tcg,thread=multi', argv)
        self.assertNotIn('kvm', argv)

    def test_native_uses_kvm_if_accessible(self):
        with patch('platform.machine', return_value='x86_64'), patch('os.access', return_value=True):
            argv = self.save().command()
        self.assertIn('kvm', argv)
        self.assertEqual(argv[argv.index('-cpu') + 1], 'host')

    def test_qemu_paths_are_json_not_drive_options(self):
        session = Session(self.path, self.root / 'spaces,comma' / 'state')
        command = session.command()
        block = json.loads(command[command.index('-blockdev') + 1])
        self.assertEqual(block['file']['filename'], str(session.disk))
        self.assertIn('hostfwd=tcp:127.0.0.1:2228-:22', ' '.join(command))
        self.assertNotIn('-virtfs', command)

    def test_resource_bounds(self):
        for options in [dict(port=22), dict(memory=12), dict(cpus=1024)]:
            with self.assertRaises(TesterError):
                self.save().command(**options)

    def test_unprepared_status_is_stopped(self):
        self.assertEqual(self.save().status(), 'stopped')

    def test_ssh_quotes_guest_arguments_and_pins_hosts(self):
        session = self.save()
        session.directory.mkdir(parents=True)
        (session.directory / 'connection.json').write_text('{"port":2229}')
        with patch.object(session, 'status', return_value='running'):
            argv = session.ssh(['echo', '$(do-not-run); spaces'])
        self.assertEqual(argv[-1], "echo '$(do-not-run); spaces'")
        self.assertIn('StrictHostKeyChecking=accept-new', argv)
        self.assertEqual(argv[1:3], ['-F', '/dev/null'])
        self.assertIn('2229', argv)
        self.assertNotIn('StrictHostKeyChecking=no', argv)

    def test_shutdown_resumes_before_requesting_guest_poweroff(self):
        session = self.save()
        with patch.object(session, 'status', return_value='paused'), patch.object(session, 'qmp') as qmp, \
             patch.object(session, 'ssh', return_value=['ssh', 'sudo systemctl poweroff']) as ssh, \
             patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, stderr='')):
            session.shutdown()
        qmp.assert_called_once_with('cont')
        ssh.assert_called_once_with(['sudo', 'systemctl', '--no-block', 'poweroff'])

    def test_shutdown_waits_for_vm_after_ssh_closes_early(self):
        session=self.save()
        with patch.object(session,'status',side_effect=['running']*10+['stopped']), \
             patch.object(session,'ssh',return_value=['ssh']), \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],255,stderr='Connection closed')), \
             patch('luma_tester.core.time.monotonic',side_effect=range(30)), \
             patch('luma_tester.core.time.sleep'):
            session.shutdown()

    def test_ssh_disconnect_alone_does_not_prove_shutdown(self):
        session=self.save()
        with patch.object(session,'status',return_value='running'), \
             patch.object(session,'ssh',return_value=['ssh']), \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],255,stderr='Connection closed')), \
             patch('luma_tester.core.time.monotonic',side_effect=[0,61]):
            with self.assertRaises(TesterError):session.shutdown()

    def test_windows_shutdown_disconnect_requires_actual_poweroff(self):
        session=self.save()
        for code in (-1,1,255):
            with self.subTest(code=code), patch('luma_tester.core.host.windows',return_value=True), \
                 patch.object(session,'status',side_effect=['running','stopped']), \
                 patch.object(session,'ssh',return_value=['ssh']), \
                 patch('subprocess.run',return_value=subprocess.CompletedProcess([],code,stderr='Connection closed')), \
                 patch('luma_tester.core.time.monotonic',side_effect=range(10)):
                session.shutdown()
        with patch('luma_tester.core.host.windows',return_value=True), \
             patch.object(session,'status',return_value='running'), \
             patch.object(session,'ssh',return_value=['ssh']), \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,stderr='Connection failed')), \
             patch('luma_tester.core.time.monotonic',side_effect=[0,61]):
            with self.assertRaises(TesterError):session.shutdown()

    def test_sync_rejects_home_or_root(self):
        session = self.save()
        for source in [Path.home(), Path('/')]:
            with self.assertRaises(TesterError):
                session.sync(source)

    def test_sync_never_deletes_guest_and_excludes_secrets(self):
        session = self.save()
        with patch.object(session, 'ssh', return_value=['ssh', '-p', '2228', 'luma@127.0.0.1']), patch('luma_tester.core.run') as run:
            session.sync(self.root)
        args = run.call_args_list[-1].args[0]
        self.assertNotIn('--delete', args)
        self.assertIn('--exclude=.env.*', args)
        self.assertIn('--safe-links', args)

    def test_prepare_rejects_changed_bytes(self):
        session = self.fixture_disk()
        self.m['image']['sha256'] = '0' * 64
        session = self.save()
        with self.assertRaisesRegex(TesterError, 'SHA-256 mismatch'):
            session.prepare(lambda _: None)
        self.assertFalse(session.disk.exists())
        self.assertFalse(session.base.exists())
        self.assertEqual(list(session.base.parent.iterdir()), [])

    def test_prepare_rejects_oversized_download(self):
        self.fixture_disk()
        self.m['image']['bytes'] = 1
        session = self.save()
        with self.assertRaisesRegex(TesterError, 'exceeds declared'):
            session.prepare(lambda _: None)
        self.assertFalse(session.base.exists())

    def test_prepare_persists_overlay_and_key(self):
        session = self.fixture_disk()
        original = digest(self.source)
        session.prepare(lambda _: None)
        overlay_inode = session.disk.stat().st_ino
        key = session.key.read_bytes()
        session.prepare(lambda _: None)
        self.assertEqual(session.disk.stat().st_ino, overlay_inode)
        self.assertEqual(session.key.read_bytes(), key)
        self.assertEqual(digest(self.source), original)
        info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(session.disk)]))
        self.assertEqual(info['backing-filename'], str(session.base))
        self.assertEqual(session.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(session.key.stat().st_mode & 0o777, 0o600)
        self.assertTrue((session.directory / 'seed.iso').exists())

    def test_published_disk_uses_tickets_once_and_keeps_disk_validation(self):
        self.fixture_disk()
        self.m['image']['url'] = 'https://media.simplyluma.com/developer.qcow2'
        self.m['artifact_id'] = 'os/nightly/test/x86_64'
        session = self.save()
        with patch('luma_tester.downloads.download', side_effect=lambda artifact, path, **kw:
                   shutil.copy2(self.source, path)) as fetch:
            session.prepare(lambda _: None)
            session.prepare(lambda _: None)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.kwargs['experience'], 'mobile')
        self.assertEqual(fetch.call_args.args[0]['sha256'], self.m['image']['sha256'])
        self.assertTrue(session.disk.exists())

    def test_hub_file_is_not_automatically_a_bootable_developer_disk(self):
        self.m['image']['url'] = 'https://media.simplyluma.com/installer.iso'
        self.m['artifact_id'] = 'os/nightly/test/x86_64'
        session = self.save()
        with patch('luma_tester.downloads.download', side_effect=lambda artifact, path, **kw:
                   shutil.copy2(self.source, path)), self.assertRaisesRegex(TesterError, 'qcow2'):
            session.prepare(lambda _: None)
        self.assertFalse(session.base.exists())
        self.assertFalse(session.disk.exists())

    def test_image_writer_closes_before_cache_promotion(self):
        session = self.fixture_disk()
        opened = []
        create = tempfile.NamedTemporaryFile
        replace = Path.replace

        def tracked_file(*args, **kwargs):
            stream = create(*args, **kwargs)
            opened.append(stream)
            return stream

        def windows_replace(path, destination):
            self.assertTrue(opened)
            self.assertTrue(all(stream.closed for stream in opened),
                            'Windows cannot promote an open image file')
            return replace(path, destination)

        with patch('luma_tester.core.tempfile.NamedTemporaryFile', side_effect=tracked_file), \
             patch.object(Path, 'replace', windows_replace):
            session.prepare(lambda _: None)
        self.assertEqual(digest(session.base), self.m['image']['sha256'])

    def test_failed_image_transfer_closes_before_cleanup(self):
        self.fixture_disk()
        self.m['image']['sha256'] = '0' * 64
        session = self.save()
        opened = []
        create = tempfile.NamedTemporaryFile
        unlink = Path.unlink

        def tracked_file(*args, **kwargs):
            stream = create(*args, **kwargs)
            opened.append(stream)
            return stream

        def windows_unlink(path, *args, **kwargs):
            for stream in opened:
                if path == Path(stream.name):
                    self.assertTrue(stream.closed, 'Windows cannot remove an open image file')
            return unlink(path, *args, **kwargs)

        with patch('luma_tester.core.tempfile.NamedTemporaryFile', side_effect=tracked_file), \
             patch.object(Path, 'unlink', windows_unlink), \
             self.assertRaisesRegex(TesterError, 'SHA-256 mismatch'):
            session.prepare(lambda _: None)
        self.assertFalse(session.base.exists())
        self.assertEqual(list(session.base.parent.iterdir()), [])

    def test_mobile_and_desktop_share_base_but_keep_separate_profiles(self):
        mobile = self.fixture_disk()
        mobile.prepare(lambda _: None)
        self.m.update(id='desktop-test', target='desktop')
        desktop = self.save()
        desktop.prepare(lambda _: None)
        self.assertEqual(mobile.base, desktop.base)
        self.assertNotEqual(mobile.disk, desktop.disk)
        self.assertNotEqual(mobile.key.read_bytes(), desktop.key.read_bytes())
        for session, target in [(mobile, 'mobile'), (desktop, 'desktop')]:
            text = (session.directory / 'user-data').read_text()
            data = json.loads(text.split('\n', 1)[1])
            marker = next(item for item in data['write_files'] if item['path'] == '/etc/luma-tester-target')
            self.assertEqual(marker['content'], target + '\n')

    def test_same_verified_image_can_move_without_replacing_workspace(self):
        session = self.fixture_disk()
        session.prepare(lambda _: None)
        key = session.key.read_bytes()
        disk = session.disk.stat().st_ino
        moved = self.root / 'relocated.qcow2'
        shutil.copy2(self.source, moved)
        self.m['image']['url'] = moved.as_uri()
        session = self.save()
        session.prepare(lambda _: None)
        self.assertEqual(session.key.read_bytes(), key)
        self.assertEqual(session.disk.stat().st_ino, disk)

    def test_prepare_refuses_silent_rebase(self):
        session = self.fixture_disk()
        session.prepare(lambda _: None)
        self.m['build'] = 'different-build'
        with self.assertRaisesRegex(TesterError, 'another image'):
            self.save().prepare(lambda _: None)

    def test_corrupted_cache_is_rejected(self):
        session = self.fixture_disk()
        session.prepare(lambda _: None)
        session.base.chmod(0o600)
        session.base.write_bytes(b'corrupt')
        with self.assertRaisesRegex(TesterError, 'Cached image changed'):
            session.prepare(lambda _: None)

    def test_backing_file_image_is_rejected(self):
        self.fixture_disk()
        backing = self.root / 'backed.qcow2'
        subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-F', 'qcow2', '-b', str(self.source), str(backing)], check=True)
        self.m['image'].update(url=backing.as_uri(), sha256=digest(backing), bytes=backing.stat().st_size)
        with self.assertRaisesRegex(TesterError, 'standalone'):
            self.save().prepare(lambda _: None)

    def test_cli_manifest_and_dry_run(self):
        self.fixture_disk()
        output = self.root / 'export.json'
        argv = ['luma-tester', 'manifest', str(self.source), '--output', str(output), '--id', 'desktop-test',
                '--target', 'desktop', '--arch', 'x86_64', '--boot', 'bios', '--build', 'fixture']
        with patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        self.assertEqual(read_manifest(output)['target'], 'desktop')
        buffer = io.StringIO()
        with patch('sys.argv', ['luma-tester', '--state', str(self.root / 'dry'), 'start', str(output), '--dry-run']), contextlib.redirect_stdout(buffer):
            self.assertEqual(main(), 0)
        self.assertIn('qemu-system-x86_64', json.loads(buffer.getvalue()))
        self.assertFalse((self.root / 'dry').exists())

    def test_dev_once_restarts_guest_service(self):
        session = self.save()
        with patch.object(session, 'wait_graphical'), patch.object(session, 'sync') as sync, patch.object(session, 'ssh', side_effect=lambda argv: ['ssh', *argv]), patch('subprocess.call'), patch('luma_tester.core.run') as run:
            session.dev(self.root, ['python3', '-m', 'example'], once=True)
        sync.assert_called_once_with(self.root)
        argv = run.call_args.args[0]
        self.assertIn('--working-directory="$HOME/Projects/app"', argv[3])
        self.assertEqual(argv[-3:], ['python3', '-m', 'example'])

    def test_manifest_wrong_types_report_errors(self):
        for value in [[], None, 42]:
            self.path.write_text(json.dumps(value))
            with self.assertRaises(TesterError):
                read_manifest(self.path)
        for name in ('id', 'user', 'arch', 'build'):
            original = self.m[name]
            self.m[name] = []
            with self.assertRaises(TesterError):
                self.save()
            self.m[name] = original

    def test_external_data_image_is_rejected(self):
        self.fixture_disk()
        external = self.root / 'external.qcow2'
        subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-o',
                        'data_file=' + str(self.root / 'data.raw'), str(external), '32M'], check=True)
        self.m['image'].update(url=external.as_uri(), sha256=digest(external), bytes=external.stat().st_size)
        with self.assertRaisesRegex(TesterError, 'standalone'):
            self.save().prepare(lambda _: None)

    def test_cli_start_holds_lock_and_exits_cleanly(self):
        if not shutil.which('qemu-system-x86_64'):
            self.skipTest('QEMU x86 system emulator not installed')
        import socket
        import sys
        session = self.fixture_disk()
        original = digest(self.source)
        with socket.socket() as socket_for_port:
            socket_for_port.bind(('127.0.0.1', 0))
            port = socket_for_port.getsockname()[1]
        log = open(self.root / 'cli.log', 'w')
        self.addCleanup(log.close)
        proc = subprocess.Popen([sys.executable, '-m', 'luma_tester', '--state', str(session.root),
                                 'start', str(self.path), '--headless', '--software', '--cpus', '1',
                                 '--memory', '1024', '--port', str(port)], stdout=log, stderr=log)
        def finish():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=10)
        self.addCleanup(finish)
        deadline = time.monotonic() + 10
        while session.status() == 'stopped':
            if proc.poll() is not None or time.monotonic() > deadline:
                self.fail('CLI startup failed: ' + (self.root / 'cli.log').read_text())
            time.sleep(0.05)
        with self.assertRaises(TesterError):
            with session.lock():
                pass
        # A terminated supervisor forwards termination and reaps its QEMU child.
        proc.terminate()
        proc.wait(timeout=10)
        self.assertFalse(session.qmp_path.exists())
        self.assertEqual(digest(self.source), original)
        self.assertTrue(session.disk.exists())
        with session.lock():
            pass

    def test_real_qemu_qmp_controls_and_screenshot(self):
        if not shutil.which('qemu-system-x86_64'):
            self.skipTest('QEMU x86 system emulator not installed')
        session = self.fixture_disk()
        session.prepare(lambda _: None)
        argv = session.command(headless=True, software=True, memory=1024, cpus=1)
        # No OS is installed: this tests the actual emulator/control path only.
        argv += ['-S']
        log = open(self.root / 'qemu.log', 'w')
        self.addCleanup(log.close)
        proc = subprocess.Popen(argv, stdout=log, stderr=log)
        def finish():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=10)
        self.addCleanup(finish)
        deadline = time.monotonic() + 10
        while not session.qmp_path.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                self.fail('QEMU startup failed: ' + (self.root / 'qemu.log').read_text())
            time.sleep(0.05)
        self.assertEqual(session.status(), 'prelaunch')
        session.qmp('cont')
        self.assertEqual(session.status(), 'running')
        session.qmp('stop')
        self.assertEqual(session.status(), 'paused')
        path = session.screenshot(self.root / 'screen.ppm')
        self.assertTrue(path.read_bytes().startswith(b'P6'))
        with self.assertRaises(TesterError):
            session.screenshot(path)
        session.qmp('quit')
        self.assertEqual(proc.wait(timeout=10), 0)


if __name__ == '__main__':
    unittest.main()
