#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compose the native phone simulator from explicit, checksummed component inputs.

Administrative commands run only inside a disposable QEMU guest. The input
cohort must carry its normal component QA and corresponding-source receipts;
this producer never qualifies a desktop image as Mobile by resizing it.
"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src/luma-tester'))
from luma_tester.core import Session, TesterError, digest, read_manifest


def build(args):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', args.build_id):
        raise TesterError('Use a short lowercase build ID with hyphens.')
    inputs = args.inputs.resolve()
    index = inputs / 'inputs.json'
    receipt = json.loads(index.read_text())
    if receipt.get('schema') != 1 or not receipt.get('files'):
        raise TesterError('Component inputs require a schema1 checksum receipt.')
    files = []
    for entry in receipt['files']:
        path = (inputs / entry['path']).resolve()
        if not path.is_relative_to(inputs) or path == index:
            raise TesterError('Input path escapes the component bundle.')
        if path.stat().st_size != entry['bytes'] or digest(path) != entry['sha256']:
            raise TesterError('Component checksum mismatch: ' + entry['path'])
        files.append((path, entry['path']))
    required = {'keyboard/luma-keyboard-x86_64/luma-latinime-decoder',
                'keyboard/luma-keyboard-x86_64/main_en.dict',
                'keyboard/luma-stevia-x86_64/luma-phosh-osk-stevia',
                'control/libluma-tester-control.so',
                'profiles/desktop.dconf', 'profiles/handheld.dconf'}
    if not required <= {name for _, name in files}:
        raise TesterError('The native keyboard and matching phone presentation profiles are required.')
    base = read_manifest(args.base_manifest)
    if base['target'] not in ('desktop', 'mobile') or base['arch'] != 'x86_64':
        raise TesterError('This producer requires a verified x86_64 common OS base.')
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    identity = dict(source_commit=source, inputs_sha256=digest(index), base=base['image'], build=args.build_id)
    marker = output / 'build-state.json'
    if marker.exists() and (not args.resume or json.loads(marker.read_text()) != identity):
        raise TesterError('Existing builder state requires --resume with identical inputs/source.')
    marker.write_text(json.dumps(identity, indent=2) + '\n')
    payload = output / 'phone-inputs.tar'
    with tarfile.open(payload, 'w') as archive:
        archive.add(index, arcname='inputs.json')
        for path, name in files:
            archive.add(path, arcname=name, recursive=False)
        for name in ('scripts/tester/guest-phone-configure.sh', 'scripts/tester/mobile-session',
                     'scripts/tester/mobile-ready.py', 'scripts/tester/apply-profile',
                     'config/tester/mobile.dconf', 'config/tester/mobile-ready.service'):
            archive.add(REPO / name, arcname=name)
    manifest = dict(base, id='phone-builder-' + args.build_id, build='Disposable native phone image builder')
    manifest_path = output / 'builder.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    session = Session(manifest_path, output / 'builder-state')
    session.prepare()
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(session.disk)]))
    if info['virtual-size'] < 24 * 1024**3:
        subprocess.run(['qemu-img', 'resize', str(session.disk), '24G'], check=True)
    with socket.socket() as port:
        port.bind(('127.0.0.1', 0))
        number = port.getsockname()[1]
    env = dict(os.environ, PYTHONPATH=str(REPO / 'src/luma-tester'))
    def guest(argv, **kwargs):
        return subprocess.run(session.ssh(argv), check=True, timeout=kwargs.pop('timeout', 300), **kwargs)
    with (output / 'qemu.log').open('a') as log:
        child = subprocess.Popen([sys.executable, '-m', 'luma_tester', '--state', str(session.root),
            'start', str(manifest_path), '--headless', '--memory', '4096', '--cpus', '2',
            '--port', str(number)], stdout=log, stderr=log, env=env)
        try:
            session.wait(240)
            guest(['sudo', 'cloud-init', 'status', '--wait'])
            guest(['sudo', 'systemctl', 'stop', 'greetd.service'])
            guest(['sudo', 'mkdir', '-p', '/var/tmp/luma-tester-phone-image'])
            with payload.open('rb') as stream:
                guest(['sudo', 'tar', '-xf', '-', '-C', '/var/tmp/luma-tester-phone-image'], stdin=stream)
            install = '''set -eu
cd /var/tmp/luma-tester-phone-image
[ "$(systemd-detect-virt --vm)" = kvm ]
dnf5 -y --setopt=install_weak_deps=False install ./rpms/*.rpm wlr-randr stevia-phosh-osk-provider hunspell-en-US bindfs
bash scripts/tester/guest-phone-configure.sh "$1"
'''
            with (output / 'compose.log').open('a') as stream:
                guest(['sudo', 'bash', '-c', install, 'phone-compose', args.build_id], stdout=stream, stderr=stream, timeout=1200)
            with (output / 'installed-packages.txt').open('wb') as stream:
                guest(['cat', '/etc/luma/tester-packages.txt'], stdout=stream)
            cleanup = '''set -eu
cloud-init clean --logs --machine-id
rm -f /etc/ssh/ssh_host_* /home/luma/.ssh/authorized_keys /home/luma/.bash_history /root/.bash_history /var/lib/systemd/random-seed
rm -rf /home/luma/.config /home/luma/.cache /home/luma/.local /home/luma/Projects /var/tmp/luma-tester-phone-image /var/lib/cloud/seed /var/log/journal/*
dnf5 clean all
sync
'''
            guest(['sudo', 'sh', '-c', cleanup])
            session.qmp('system_powerdown')
            if child.wait(timeout=120) != 0:
                raise TesterError('Builder exited unsuccessfully.')
            disk = output / 'luma-mobile-x86_64.qcow2'
            if disk.exists():
                raise TesterError('Refusing to overwrite an exported phone image.')
            subprocess.run(['qemu-img', 'convert', '-c', '-O', 'qcow2', str(session.disk), str(disk)], check=True)
            final = dict(manifest, target='mobile', id='luma-mobile-x86-64-' + args.build_id,
                         build='Luma Mobile x86_64 simulator ' + args.build_id,
                         image=dict(url=disk.as_uri(), bytes=disk.stat().st_size, sha256=digest(disk)))
            (output / 'image.json').write_text(json.dumps(final, indent=2) + '\n')
            identity['image'] = final['image']
            identity['qualified'] = False
            (output / 'build-receipt.json').write_text(json.dumps(identity, indent=2) + '\n')
        finally:
            if child.poll() is None:
                try:
                    session.shutdown()
                    child.wait(timeout=45)
                except Exception:
                    child.terminate()
                    child.wait(timeout=15)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-manifest', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build-id', required=True)
    parser.add_argument('--resume', action='store_true')
    build(parser.parse_args())
