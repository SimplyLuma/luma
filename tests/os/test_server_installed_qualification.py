#!/usr/bin/env python3
"""Contract tests use synthetic files; they never claim a guest/release PASS."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import socket
import threading
import subprocess
import shutil
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[2] / 'scripts/os/lib/server_installed_qualification.py'
spec = importlib.util.spec_from_file_location('server_qualification', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


class ServerQualification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root / 'fixture'
        self.iso = self.root / 'final.iso'
        self.iso.write_bytes(b'unit fixture, not an actual ISO')
        self.qemu = self.root / 'qemu-system-aarch64'
        self.qemu.write_bytes(b'unit fixture, never executed')
        self.firmware = self.root / 'AAVMF_CODE.fd'
        self.firmware.write_bytes(b'unit firmware, never executed')
        self.checks = self.root / 'checks'
        self.checks.mkdir()
        for name in m.CHECK_FILES:
            (self.checks / name).write_text('# unit capsule, not actual guest code\n')
        self.heads = {'app/' + app + '/aarch64/beta': sha(app.encode())
                      for app in sorted({app for app, _ in m.BEHAVIORS})}
        for number in range(24 - len(self.heads)):
            app = 'org.projectluma.Unit' + str(number)
            self.heads['app/' + app + '/aarch64/beta'] = sha(app.encode())
        for app in ('Platform', 'Platform.Locale', 'Platform.GL.default'):
            self.heads['runtime/org.projectluma.' + app + '/aarch64/44'] = sha(app.encode())
        self.headers = {name: {'nevra': name + '-0.1-1.aarch64', 'header_sha256': sha(name.encode())}
                        for name in ('luma-terminal', 'luma-application-installer')}
        self.expected = dict(build_id='20261024.1', source_revision='1' * 40,
                             profile='arm-preview', candidate='2' * 64,
                             iso_sha256=sha(self.iso.read_bytes()), iso_bytes=self.iso.stat().st_size,
                             fixture_token='unit-fixture-never-booted')
        source_inputs = {'static_proof': {'result': 'UNIT_TEST_ONLY'},
                         'checks_preparation': {'result': 'UNIT_TEST_ONLY'},
                         'signed_heads': self.heads, 'native_headers': self.headers}
        self.inputs = dict(iso=str(self.iso), checks_directory=str(self.checks),
                           qemu=str(self.qemu), firmware=str(self.firmware),
                           target_disk_path=str(self.root / 'disk.qcow2'), planned_growth_bytes=1024)
        for key, value in source_inputs.items():
            path = self.root / (key + '.json')
            path.write_bytes(encoded(value))
            self.inputs[key] = str(path)
            hash_key = {'signed_heads': 'signed_heads', 'native_headers': 'native_headers'}.get(key, key)
            self.expected[hash_key + '_sha256'] = sha(path.read_bytes())
        self.expected['checks_manifest_sha256'] = sha(encoded({
            name: sha((self.checks / name).read_bytes()) for name in m.CHECK_FILES}))
        argv = [str(self.qemu), '-accel', 'tcg,thread=multi', '-machine', 'virt',
                '-bios', str(self.firmware), '-drive', 'file=' + str(self.iso) + ',readonly=on,format=raw',
                '-drive', 'file=' + self.inputs['target_disk_path'] + ',format=qcow2']
        path = self.root / 'argv.json'
        path.write_bytes(encoded(argv))
        self.inputs['qemu_invocation'] = str(path)
        # Unit only: tiny fixture does not reserve a real VM's storage.
        fs = type('FS', (), {'f_bavail': 100 * 1024**3, 'f_frsize': 1})()
        with patch.object(m.os, 'statvfs', return_value=fs):
            m.prepare_fixture(self.fixture, self.expected, self.inputs)
        self.previous = sha((self.fixture / 'PREPARED.json').read_bytes())
        self.disk_identity = dict(device=1, inode=2, uid=os.getuid(), gid=os.getgid(), mode=0o600, xattrs=m.identity(self.iso)['xattrs'])
        self.stage('CREATED', 'CREATED_ONLY', {
            'created-qemu-img-info.json': {'format': 'qcow2', 'virtual-size': m.VIRTUAL_BYTES, 'dirty-flag': False},
            'created-qemu-img-map.json': [{'data': False, 'zero': True, 'length': m.VIRTUAL_BYTES}],
        }, exclusively_created=True, allocated_data_bytes=0,
                   disk_identity=self.disk_identity, disk_path=self.inputs['target_disk_path'])
        self.stage('INSTALLED', 'PASS_INSTALLED', {
            'installer.log': b'Anaconda unit transcript\nInstallation complete\n',
            'installed-rpm-ostree-status.json': {'deployments': [{'booted': True, 'checksum': self.expected['candidate']}]},
            'installed-boot.json': {'uname_machine': 'aarch64', 'graphical_target': 'active', 'efi_boot': True},
        }, disk_identity=self.disk_identity, installation_origin='fresh-final-luma-iso', unsigned_modifications=[])
        log = b'unit test checker log, no actual guest assertions executed\n'
        rows = [dict(app_id=app, check=check, ref='app/' + app + '/aarch64/beta',
                     commit=self.heads['app/' + app + '/aarch64/beta'], exit_status=0,
                     actual_flatpak_sandbox=True, permission_overrides=False,
                     **({'mapped_window': True} if check == 'installed-launcher' else {}),
                     **({'log_sha256': sha(log)} if check != 'installed-native-and-signed-catalogue-service' else {}))
                for app, check in sorted(m.BEHAVIORS)]
        self.stage('QUALIFIED', 'PASS', {
            'guest-result.json': dict(result='PASS', architecture='aarch64', source_revision=self.expected['source_revision'], results=rows),
            'driver-outcome.json': dict(returncode=0, timed_out=False, results_present=True),
            'runtime-heads.json': {k: v for k, v in self.heads.items() if k.startswith('runtime/')},
            'native-host-headers.json': self.headers, 'checker.log': log,
        }, disk_identity=self.disk_identity, unsigned_modifications=[])
        self.archive = self.root / 'archived-disk.qcow2'
        self.archive.write_bytes(b'unit archive body, not a VM')
        self.archive.chmod(0o600)
        self.stage('CLOSED', 'PASS_CLOSED', {
            'normal-wrapper-outcome.json': dict(returncode=0, result='finished', signal=None),
            'closed-consumers.json': dict(qemu_pids=[], open_fds=[], cgroup_pids=[], domain_state='shut off',
                                         units=[dict(Id='luma-arm-desktop-unit-test.service', ActiveState='inactive', Result='success', ExecMainStatus='0')],
                                         scanned_cgroup='/sys/fs/cgroup/luma.slice/luma-arm.slice/luma-arm-desktop.slice/unit-test',
                                         scanned_disk_identity=self.disk_identity),
            'original-domain.xml': b'<unit-test-only/>\n', 'restored-domain.xml': b'<unit-test-only/>\n',
        }, disk_identity=self.disk_identity, archive_path=str(self.archive), archive_identity=m.identity(self.archive),
                   archive_sha256=sha(self.archive.read_bytes()), archive_bytes=self.archive.stat().st_size,
                   source_disk_sha256=sha(self.archive.read_bytes()), source_disk_bytes=self.archive.stat().st_size,
                   source_input_sha256=self.expected['iso_sha256'])

    def stage(self, name, result, proofs, **fields):
        hashes = {}
        for filename, value in proofs.items():
            data = value if isinstance(value, bytes) else encoded(value)
            path = self.fixture / filename
            path.write_bytes(data)
            path.chmod(0o400)
            hashes[filename] = sha(data)
        receipt = {key: self.expected[key] for key in m.IDENTITY_KEYS}
        receipt.update(provider=m.PROVIDER, result=result, previous_receipt_sha256=self.previous,
                       proof_hashes=hashes, **fields)
        self.previous = m.immutable_json(self.fixture / (name + '.json'), receipt)

    def validate(self):
        return m.validate_server_qualification(self.fixture, self.expected, owner_uid=os.getuid())

    def mutate(self, name, change, rechain=True):
        path = self.fixture / name
        data = json.loads(path.read_text())
        change(data)
        path.chmod(0o600)
        path.write_bytes(encoded(data))
        path.chmod(0o400)
        stages = ['PREPARED', 'CREATED', 'INSTALLED', 'QUALIFIED', 'CLOSED']
        if rechain:
            # Repair receipt links deliberately so each negative tests semantics.
            for index, stage in enumerate(stages[1:], 1):
                p = self.fixture / (stage + '.json')
                v = json.loads(p.read_text())
                v['previous_receipt_sha256'] = sha((self.fixture / (stages[index - 1] + '.json')).read_bytes())
                p.chmod(0o600); p.write_bytes(encoded(v)); p.chmod(0o400)

    def proof_mutation(self, name, stage, change):
        self.mutate(name, change, rechain=False)
        self.mutate(stage + '.json', lambda r: r['proof_hashes'].update({name: sha((self.fixture / name).read_bytes())}))

    def test_complete_fixture_contract_only(self):
        result = self.validate()
        self.assertEqual(result['provider'], m.PROVIDER)
        self.assertFalse(result['physical_hardware_coverage'])
        self.assertIn(str(self.fixture / 'CLOSED.json'), result['proof_hashes'])

    def test_wrong_independent_source(self):
        self.expected['source_revision'] = '3' * 40
        with self.assertRaises(m.QualificationError): self.validate()

    def test_wrong_independent_candidate(self):
        self.expected['candidate'] = '4' * 64
        with self.assertRaises(m.QualificationError): self.validate()

    def test_wrong_iso_same_size(self):
        self.iso.write_bytes(b'X' * self.iso.stat().st_size)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_changed_iso_inode(self):
        data = self.iso.read_bytes(); self.iso.unlink(); self.iso.write_bytes(data)
        # inode reuse is possible: force identity divergence via mode.
        self.iso.chmod(0o400)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_mac_provider_refused(self):
        self.mutate('PREPARED.json', lambda r: r.update(provider='mac-external-apfs-ssd'))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_mac_field_refused(self):
        self.mutate('QUALIFIED.json', lambda r: r.update(native_mac_vm='old'))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_missing_actual_install(self):
        (self.fixture / 'INSTALLED.json').unlink()
        with self.assertRaises(FileNotFoundError): self.validate()

    def test_changed_proof_bytes(self):
        p = self.fixture / 'installer.log'; p.chmod(0o600); p.write_bytes(b'changed'); p.chmod(0o400)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_broken_receipt_chain(self):
        self.mutate('QUALIFIED.json', lambda r: r.update(previous_receipt_sha256='0' * 64), rechain=False)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_writable_receipt(self):
        (self.fixture / 'INSTALLED.json').chmod(0o600)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_path_escape(self):
        self.mutate('QUALIFIED.json', lambda r: r['proof_hashes'].update({'../escape': '0' * 64}))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_symlink_proof(self):
        path = self.fixture / 'checker.log'; data = path.read_bytes(); path.unlink()
        target = self.root / 'redirect'; target.write_bytes(data); target.chmod(0o400); path.symlink_to(target)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_missing_behavior_row(self):
        self.proof_mutation('guest-result.json', 'QUALIFIED', lambda r: r['results'].pop(0))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_duplicate_behavior_row(self):
        self.proof_mutation('guest-result.json', 'QUALIFIED', lambda r: r['results'].__setitem__(0, copy.deepcopy(r['results'][1])))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_unmapped_launcher(self):
        def change(r):
            next(x for x in r['results'] if x['check'] == 'installed-launcher')['mapped_window'] = False
        self.proof_mutation('guest-result.json', 'QUALIFIED', change)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_permission_override(self):
        self.proof_mutation('guest-result.json', 'QUALIFIED', lambda r: r['results'][0].update(permission_overrides=True))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_boolean_zero_exit_is_not_real_zero(self):
        self.proof_mutation('guest-result.json', 'QUALIFIED', lambda r: r['results'][0].update(exit_status=False))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_wrong_signed_app_head(self):
        self.proof_mutation('guest-result.json', 'QUALIFIED', lambda r: r['results'][0].update(commit='5' * 64))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_failed_driver(self):
        self.proof_mutation('driver-outcome.json', 'QUALIFIED', lambda r: r.update(returncode=255))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_unsigned_diagnostic_overlay(self):
        self.mutate('INSTALLED.json', lambda r: r.update(unsigned_modifications=['debug engine']))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_fedora_fixture_is_not_final_luma_iso(self):
        self.mutate('INSTALLED.json', lambda r: r.update(installation_origin='borrowed-fedora-iso'))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_disk_identity_changed(self):
        self.mutate('QUALIFIED.json', lambda r: r['disk_identity'].update(inode=99))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_nonempty_fresh_disk(self):
        self.proof_mutation('created-qemu-img-map.json', 'CREATED', lambda r: r[0].update(data=True))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_backing_dependency(self):
        self.proof_mutation('created-qemu-img-info.json', 'CREATED', lambda r: r.update(**{'backing-filename': 'old.qcow2'}))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_active_consumer(self):
        self.proof_mutation('closed-consumers.json', 'CLOSED', lambda r: r.update(open_fds=[{'pid': 12, 'fd': 7}]))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_interrupted_normal_wrapper(self):
        self.proof_mutation('normal-wrapper-outcome.json', 'CLOSED', lambda r: r.update(returncode=143, signal='SIGTERM'))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_archive_corruption(self):
        self.archive.write_bytes(b'corrupt archive')
        with self.assertRaises(m.QualificationError): self.validate()

    def test_archive_metadata_changed(self):
        self.archive.chmod(0o400)
        with self.assertRaises(m.QualificationError): self.validate()

    def test_missing_xml_restoration(self):
        path = self.fixture / 'restored-domain.xml'; path.chmod(0o600); path.write_bytes(b'changed xml'); path.chmod(0o400)
        self.mutate('CLOSED.json', lambda r: r['proof_hashes'].update({'restored-domain.xml': sha(path.read_bytes())}))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_source_archive_custody_mismatch(self):
        self.mutate('CLOSED.json', lambda r: r.update(source_disk_sha256='f' * 64))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_failed_root_producer(self):
        self.proof_mutation('closed-consumers.json', 'CLOSED', lambda r: r['units'][0].update(ExecMainStatus='1'))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_unowned_cgroup_refused(self):
        self.proof_mutation('closed-consumers.json', 'CLOSED', lambda r: r.update(scanned_cgroup='/sys/fs/cgroup/other-owner'))
        with self.assertRaises(m.QualificationError): self.validate()

    def test_prepare_refuses_existing_fixture(self):
        with self.assertRaises(m.QualificationError): m.prepare_fixture(self.fixture, self.expected, self.inputs)

    def test_prepare_refuses_missing_growth_reserve(self):
        fs = type('FS', (), {'f_bavail': m.FLOOR_BYTES, 'f_frsize': 1})()
        with patch.object(m.os, 'statvfs', return_value=fs), self.assertRaises(m.QualificationError):
            m.prepare_fixture(self.root / 'no-space', self.expected, self.inputs)

    def test_qmp_rejects_different_pid_birth_before_connect(self):
        with self.assertRaises(m.QualificationError):
            m.ServerInputBridge(self.root / 'absent.sock', os.getpid(), -1, ['not-qemu'])


class ActualQcowPreparation(unittest.TestCase):
    setUp = ServerQualification.setUp
    stage = ServerQualification.stage
    # Reuse the small contract fixture set-up, not its test cases.
    def test_actual_exclusive_qcow_creation_and_observation(self):
        executable = shutil.which('qemu-img')
        if executable is None:
            self.skipTest('Host qemu-img unavailable; no qcow creation claim')
        executable = Path(executable).resolve()
        (self.fixture / 'CREATED.json').unlink()
        for name in ('created-qemu-img-info.json', 'created-qemu-img-map.json'):
            (self.fixture / name).unlink()
        created = m.create_fresh_disk(self.fixture, self.expected, executable, m.file_sha256(executable))
        self.assertEqual(created['allocated_data_bytes'], 0)
        self.assertEqual(created['disk_identity'], m.identity(Path(self.inputs['target_disk_path'])))
        actual = json.loads(subprocess.check_output([str(executable), 'info', '--output=json', self.inputs['target_disk_path']]))
        self.assertEqual(actual['virtual-size'], m.VIRTUAL_BYTES)
        self.assertFalse(actual.get('backing-filename'))


class QMPBridge(unittest.TestCase):
    def test_actual_unix_qmp_protocol_and_original_input_translation(self):
        # A real local protocol socket, not a real QEMU/guest qualification.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'qmp.sock'
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path)); server.listen(4)
            observed = []
            errors = []
            def serve():
                try:
                    for _ in range(3):
                        conn, _ = server.accept()
                        with conn:
                            stream = conn.makefile('rwb')
                            stream.write(b'{"QMP":{"version":{}}}\r\n'); stream.flush()
                            hello = json.loads(stream.readline())
                            self.assertEqual(hello['execute'], 'qmp_capabilities')
                            stream.write(b'{"return":{},"id":"qmp_capabilities"}\r\n'); stream.flush()
                            request = json.loads(stream.readline()); observed.append(request)
                            stream.write(encoded({'return': {}, 'id': request['id']}).replace(b'\n', b'') + b'\r\n'); stream.flush()
                except BaseException as error:
                    errors.append(error)
                finally:
                    server.close()
            worker = threading.Thread(target=serve, daemon=True); worker.start()
            # Process identity itself is covered separately; this socket unit
            # substitutes only the live QEMU process precondition, explicitly.
            with patch.object(m.ServerInputBridge, 'check_process', return_value=None):
                bridge = m.ServerInputBridge(path, os.getpid(), -1, ['unit-protocol'])
                bridge.key('SPACE')
                bridge.click(100, 50, 1280, 800)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), 'Bounded protocol server did not close')
            self.assertEqual(errors, [])
            self.assertEqual(observed[0]['arguments']['keys'][0]['data'], 'spc')
            events = observed[1]['arguments']['events']
            self.assertEqual(events[0]['data']['value'], 100 * 32767 // 1279)
            self.assertEqual(events[1]['data']['value'], 50 * 32767 // 799)
            self.assertTrue(events[2]['data']['down'])
            self.assertFalse(observed[2]['arguments']['events'][0]['data']['down'])

    def test_input_guard_rejects_unrequested_keys_and_outside_coordinates(self):
        bridge = m.ServerInputBridge.__new__(m.ServerInputBridge)
        with self.assertRaises(m.QualificationError): bridge.key('DELETE')
        with self.assertRaises(m.QualificationError): bridge.click(1280, 0, 1280, 800)


if __name__ == '__main__':
    unittest.main()
