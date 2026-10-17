#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a source-identified Luma QEMU guest in an isolated, rootless builder VM.

Run as an admitted devctl job. Host packages, libvirt and release stores are not
modified. All administrative commands run inside the disposable guest over SSH.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tarfile
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src/luma-tester'))
from luma_tester.core import Session, TesterError, digest, run

PIN_VARIABLES = (
    'GNOME_SHELL_NEVRA', 'GNOME_SHELL_COMMON_NEVRA', 'GTK4_NEVRA', 'GTK3_NEVRA',
    'LIBADWAITA_NEVRA', 'LIBHANDY_NEVRA', 'LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA',
    'LUMA_SHELL_STATE_NEVRA', 'LUMA_SEARCH_NEVRA', 'LUMA_BACKGROUNDS_NEVRA',
    'PRAIRIE_ICON_THEME_NEVRA', 'FIGTREE_NEVRA', 'LUMA_APPLICATION_INSTALLER_NEVRA',
    'LUMA_APK_METADATA_X86_64_NEVRA', 'LUMA_MONITOR_NEVRA',
    'LUMA_CALCULATOR_NEVRA', 'LUMA_TERMINAL_NEVRA',
    'LUMA_DEVELOPER_PLATFORM_SDK_NEVRA', 'LUMA_TILING_TOGGLE_NEVRA', 'TILING_SHELL_NEVRA',
)
EXTRA_PINS = (
    'mutter-50.4-1.luma.13.creator20261009.1.fc44.x86_64',
    'mutter-common-50.4-1.luma.13.creator20261009.1.fc44.noarch',
    'gnome-settings-daemon-50.1-1.luma.2.fc44.x86_64',
    'luma-background-0.1.0-1.luma.5.creator20261007.1.fc44.noarch',
    'luma-appimage-compat-0.1.0-1.luma.1.fc44.x86_64',
    'python3-authlib-1.7.2-0.1.luma.experiment.fc44.noarch',
    'python3-joserfc-1.7.5-0.1.luma.experiment.fc44.noarch',
)


def source_pins(root=REPO):
    values = {}
    for line in (root / 'config/desktop/inputs.env').read_text().splitlines():
        match = re.fullmatch(r'([A-Z0-9_]+)=([A-Za-z0-9._:+~-]+)', line)
        if match:
            values[match[1]] = match[2]
    pins = [values[name] for name in PIN_VARIABLES]
    version = values['GNOME_CONTROL_CENTER_LUMA_RELEASE']
    pins += ['gnome-control-center-50.4-' + version + '.fc44.x86_64',
             'gnome-control-center-filesystem-50.4-' + version + '.fc44.noarch']
    return pins + list(EXTRA_PINS), values['PRAIRIE_CORE_APPS_NEVRA']


def tar_file(archive, source, target):
    archive.add(source, arcname=target, recursive=False)


def create_inputs(output, pool, target, build_id, parent=None):
    commit = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    run(['git', '-C', REPO, 'diff', '--exit-code', 'HEAD', '--'], stdout=subprocess.DEVNULL)
    pins, expected_apps = source_pins()
    inputs = []
    parent_files = {p['file'] for p in parent['rpm_inputs']} if parent else set()
    for pin in [pin for pin in pins if pin + '.rpm' not in parent_files]:
        path = pool / (pin + '.rpm')
        if not path.is_file():
            raise TesterError('Missing pinned RPM: ' + str(path))
        # Check the RPM container and bind the exact bytes in the source receipt.
        run(['rpm', '-K', path], stdout=subprocess.DEVNULL)
        inputs.append({'file': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size})
    if parent:
        if parent.get('schema') != 1 or not re.fullmatch(r'[0-9a-f]{40}', parent.get('source_commit', '')):
            raise TesterError('Parent receipt has an unsupported schema or source identity.')
        if not parent_files <= {p + '.rpm' for p in pins}:
            raise TesterError('Parent RPM cohort differs from current source pins.')
        source_paths = ['config/desktop/inputs.env', 'src/prairie-core', 'src/luma-platform',
                        'src/luma-continuity', 'packaging/rpm/prairie-core-apps.spec']
        run(['git', '-C', REPO, 'diff', '--quiet', parent['source_commit'], commit, '--', *source_paths])
        inputs = [*parent['rpm_inputs'], *inputs]
    source_archive = output / 'source.tar'
    with source_archive.open('wb') as stream:
        run(['git', '-C', REPO, 'archive', commit], stdout=stream)
    source = output / 'source'
    source.mkdir(exist_ok=True)
    with tarfile.open(source_archive) as archive:
        archive.extractall(source, filter='data')
    source_archive.unlink()
    # Produce the shared app source inputs identically to its normal package builder.
    core = source / 'src/prairie-core'
    with tarfile.open(output / 'prairie-core-apps.tar.gz', 'w:gz') as archive:
        for name in ('prairie_ui', 'prairie_apps', 'bin', 'data', 'style', 'tests'):
            archive.add(core / name, arcname='prairie-core-apps/' + name)
        archive.add(source / 'tests/unit/test_prairie_photos_backend.py',
                    arcname='prairie-core-apps/tests/test_prairie_photos_backend.py')
    run([sys.executable, source / 'scripts/packages/build-luma-continuity-source.py', output / 'luma-continuity.tar.gz'])
    receipt = {'schema': 1, 'source_commit': commit, 'target': target, 'build': build_id,
               'architecture': 'x86_64', 'rpm_inputs': inputs, 'expected_apps': expected_apps}
    receipt['profiles'] = ['mobile', 'desktop']
    if parent:
        receipt['parent'] = {'source_commit': parent['source_commit'], 'image': parent['image'],
                             'package_log_sha256': parent['package_log_sha256']}
    (output / 'source-identity.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (output / 'expected-apps').write_text(expected_apps + '\n')
    (output / 'target').write_text(target + '\n')
    (output / 'build-id').write_text(build_id + '\n')
    from urllib.request import urlopen
    values = dict(line.split('=', 1) for line in (REPO / 'config/desktop/inputs.env').read_text().splitlines()
                  if line.startswith('DASH_TO_DOCK_SRPM'))
    srpm = output / values['DASH_TO_DOCK_SRPM']
    if not srpm.exists():
        with urlopen(values['DASH_TO_DOCK_SRPM_URL'], timeout=60) as response, srpm.open('wb') as stream:
            import shutil
            shutil.copyfileobj(response, stream)
    if digest(srpm) != values['DASH_TO_DOCK_SRPM_SHA256']:
        raise TesterError('Dock upstream source RPM checksum mismatch.')
    receipt['dock_source_sha256'] = digest(srpm)
    (output / 'source-identity.json').write_text(json.dumps(receipt, indent=2) + '\n')
    payload = output / 'guest-inputs.tar'
    with tarfile.open(payload, 'w') as archive:
        archive.add(source, arcname='source')
        archive.add(srpm, arcname=srpm.name)
        for item in [item for item in inputs if item['file'] not in parent_files]:
            archive.add(pool / item['file'], arcname='rpms/' + item['file'])
        for name in ('source-identity.json', 'expected-apps', 'target', 'build-id',
                     'prairie-core-apps.tar.gz', 'luma-continuity.tar.gz'):
            archive.add(output / name, arcname=name)
    return receipt, payload


def guest(session, argv, **kwargs):
    return run(session.ssh(argv), **kwargs)


def build(args):
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = output / 'build-state.json'
    if marker.exists() and not args.resume:
        raise TesterError('Build state exists. Use --resume for this exact source and build id.')
    source_head = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    identity = {'source': source_head, 'target': args.target, 'build': args.build_id}
    if marker.exists() and json.loads(marker.read_text()) != identity:
        raise TesterError('Resume source/target/build differs from the saved builder state.')
    marker.write_text(json.dumps(identity, indent=2))
    parent = json.loads(args.parent_receipt.read_text()) if args.parent_receipt else None
    receipt, payload = create_inputs(output, args.rpm_pool.resolve(), args.target, args.build_id, parent)
    base = parent['image'] if parent else json.loads((REPO / 'config/tester/fedora-cloud.json').read_text())['x86_64']
    if args.base.stat().st_size != base['bytes'] or digest(args.base) != base['sha256']:
        raise TesterError('Base does not match its pinned image or parent build receipt.')
    manifest = {'schema': 1, 'id': 'builder-' + args.target + '-' + args.build_id,
                'target': args.target, 'arch': 'x86_64', 'boot': 'bios', 'user': 'luma',
                'cloud_init': True, 'build': 'Disposable Luma image builder',
                'image': {'url': args.base.resolve().as_uri(), 'sha256': base['sha256'], 'bytes': base['bytes']}}
    manifest_path = output / 'builder.json'
    manifest_path.write_text(json.dumps(manifest, indent=2))
    session = Session(manifest_path, output / 'builder-state')
    session.prepare()
    run(['qemu-img', 'resize', session.disk, '24G'])
    with socket.socket() as port_socket:
        port_socket.bind(('127.0.0.1', 0))
        port = port_socket.getsockname()[1]
    env = dict(os.environ)
    env['PYTHONPATH'] = str(REPO / 'src/luma-tester')
    log = open(output / 'builder-console-host.log', 'a')
    child = subprocess.Popen([sys.executable, '-m', 'luma_tester', '--state', str(session.root),
                              'start', str(manifest_path), '--headless', '--memory', '3072',
                              '--cpus', '2', '--port', str(port)], stdout=log, stderr=log, env=env)
    try:
        session.wait(300)
        guest(session, ['cloud-init', 'status', '--wait'])
        guest(session, ['sudo', 'dnf', '-y', '--setopt=install_weak_deps=False', 'install', 'tar'])
        guest(session, ['sudo', 'mkdir', '-p', '/var/tmp/luma-tester-build'])
        with payload.open('rb') as stream:
            guest(session, ['sudo', 'tar', '-xf', '-', '-C', '/var/tmp/luma-tester-build'], stdin=stream)
        print('Installing pinned Luma components and building shared apps from source…', flush=True)
        with (output / 'guest-package-build.log').open('a') as stream:
            if parent:
                guest(session, ['sudo', 'bash', '/var/tmp/luma-tester-build/source/scripts/tester/guest-additions.sh'], stdout=stream, stderr=subprocess.STDOUT)
                pins, expected_apps = source_pins()
                guest(session, ['rpm', '-q', *pins, expected_apps], stdout=stream, stderr=subprocess.STDOUT)
                guest(session, ['sudo', 'bash', '/var/tmp/luma-tester-build/source/scripts/tester/guest-configure.sh',
                                args.target, args.build_id], stdout=stream, stderr=subprocess.STDOUT)
            else:
                guest(session, ['sudo', 'bash', '/var/tmp/luma-tester-build/source/scripts/tester/guest-install.sh'],
                      stdout=stream, stderr=subprocess.STDOUT, timeout=3600)
        guest(session, ['sudo', 'cp', '/etc/luma/tester-packages.txt', '/tmp/tester-packages.txt'])
        with (output / 'installed-packages.txt').open('wb') as stream:
            guest(session, ['cat', '/tmp/tester-packages.txt'], stdout=stream)
        # Retain normal package check output and the source-built RPM outside the image.
        if not parent:
            with (output / 'shared-apps-artifacts.tar').open('wb') as stream:
                guest(session, ['sudo', 'tar', '-cf', '-', '-C', '/var/tmp/luma-tester-build/app-rpmbuild',
                                'RPMS', 'SRPMS'], stdout=stream)
        if subprocess.run(session.ssh(['test', '-d', '/var/tmp/luma-tester-build/dock-rpmbuild/RPMS']), check=False).returncode == 0:
            with (output / 'dock-artifacts.tar').open('wb') as stream:
                guest(session, ['sudo', 'tar', '-cf', '-', '-C', '/var/tmp/luma-tester-build/dock-rpmbuild',
                                'RPMS', 'SRPMS'], stdout=stream)
        cleanup = r'''set -eu
          test -f /etc/luma/tester-image
          systemctl stop greetd.service
          cloud-init clean --logs --machine-id
          rm -f /etc/ssh/ssh_host_* /home/luma/.ssh/authorized_keys /home/luma/.bash_history /root/.bash_history
          rm -rf /home/luma/.config /home/luma/.cache /home/luma/.local /home/luma/Projects
          rm -f /var/lib/systemd/random-seed
          rm -rf /var/tmp/luma-tester-build /var/log/journal/* /var/lib/cloud/seed
          fstrim -av || true
          sync
        '''
        guest(session, ['sudo', 'sh', '-c', cleanup])
        session.qmp('system_powerdown')
        child.wait(timeout=120)
        if child.returncode != 0:
            raise TesterError('Builder QEMU exited with status ' + str(child.returncode))
        disk = output / 'luma-preview-x86_64.qcow2'
        if disk.exists():
            raise TesterError('Refusing to overwrite exported guest disk.')
        run(['qemu-img', 'convert', '-c', '-O', 'qcow2', session.disk, disk])
        final = dict(manifest)
        final.update(id='luma-' + args.target + '-x86-' + args.build_id,
                     build='Luma ' + args.target.title() + ' developer preview ' + args.build_id)
        final['image'] = {'url': disk.as_uri(), 'sha256': digest(disk), 'bytes': disk.stat().st_size}
        (output / 'image.json').write_text(json.dumps(final, indent=2) + '\n')
        catalog = output / 'catalog'
        catalog.mkdir(exist_ok=True)
        for target in ('desktop',):
            experience = dict(final)
            experience.update(id='luma-' + target + '-x86-' + args.build_id, target=target,
                              build='Luma ' + target.title() + ' developer preview ' + args.build_id)
            (catalog / (target + '.json')).write_text(json.dumps(experience, indent=2) + '\n')
        receipt['image'] = final['image']
        receipt['package_log_sha256'] = digest(output / 'guest-package-build.log')
        receipt['qualification'] = 'built; fresh-session shell/app qualification still required'
        (output / 'build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print('Exported ' + str(disk), flush=True)
    finally:
        if child.poll() is None:
            try:
                try:
                    session.shutdown()
                except (OSError, TesterError, subprocess.TimeoutExpired):
                    session.qmp('system_powerdown')
                child.wait(timeout=30)
            except (OSError, TesterError, subprocess.TimeoutExpired):
                child.terminate()
                child.wait(timeout=15)
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--rpm-pool', type=Path, required=True)
    parser.add_argument('--target', choices=('desktop',), default='desktop')
    parser.add_argument('--build-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parent-receipt', type=Path, help='Reuse an unchanged, previously built Luma package cohort')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,24}', args.build_id):
        parser.error('Use a short lowercase build id.')
    if args.preflight:
        pins, apps = source_pins()
        missing = [pin for pin in pins if not (args.rpm_pool / (pin + '.rpm')).is_file()]
        print(json.dumps({'required_rpms': len(pins), 'missing': missing, 'source_built_apps': apps}, indent=2))
        return int(bool(missing))
    build(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
