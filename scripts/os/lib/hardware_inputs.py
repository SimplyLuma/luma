#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Admit a private device input directory before OS or installer composition."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[3]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def admit(directory, device_name, root=ROOT):
    if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', device_name):
        raise ValueError('Invalid hardware device identity')
    device_dir = root / 'devices' / device_name
    device = tomllib.loads((device_dir / 'device.toml').read_text())
    lock_path = root / device['kernel']['source_lock']
    lock = json.loads(lock_path.read_text())
    manifest = json.loads((directory / 'hardware-inputs.json').read_text())
    if manifest.get('schema') != 'org.projectluma.hardware-inputs/v1':
        raise ValueError('Unknown hardware input schema')
    if manifest.get('scope') != 'owner-personal-test-only' or manifest.get('device') != device_name:
        raise ValueError('Hardware inputs have the wrong scope/device')
    if manifest.get('kernel_release') != lock['release'] or manifest.get('source_lock_sha256') != sha(lock_path):
        raise ValueError('Hardware kernel does not match the source lock')
    rows = manifest['files']
    seen = set()
    for row in rows:
        relative = PurePosixPath(row['path'])
        if (not re.fullmatch(r'[A-Za-z0-9_+./-]+', row['path'])
                or relative.is_absolute() or '..' in relative.parts or str(relative) != row['path']
                or row['path'] in seen):
            raise ValueError('Unsafe or duplicate hardware input path')
        seen.add(row['path'])
        if not row['path'].startswith(('rpms/', 'rootfs/usr/lib/firmware/',
                                      'rootfs/usr/share/qcom/', 'rootfs/usr/share/alsa/')):
            raise ValueError('Hardware input is outside the allowed payload directories')
        path = directory / row['path']
        if (not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink()
                or not path.is_file() or sha(path) != row['sha256']):
            raise ValueError('Hardware input body/path changed: ' + row['path'])
    actual = set()
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError('Hardware input directory contains a symlink')
        if not path.is_dir() and not path.is_file():
            raise ValueError('Hardware input contains a special file')
        if path.is_file() and path != directory / 'hardware-inputs.json':
            actual.add(str(path.relative_to(directory)))
    if actual != seen:
        raise ValueError('Hardware inputs contain unlisted or missing payload files')
    packages = [r for r in rows if r['path'].startswith('rpms/')]
    if len(packages) != 1:
        raise ValueError('Expected one complete device kernel RPM')
    rpm_path = directory / packages[0]['path']
    identity = subprocess.check_output(['rpm', '-qp', '--nosignature', '--qf',
        '%{NAME}\n%{ARCH}\n%{VERSION}-%{RELEASE}\n%{SHA256HEADER}', str(rpm_path)], text=True).splitlines()
    if identity[0] != 'kernel-surface-pro12' or identity[1] != 'aarch64':
        raise ValueError('Unexpected device kernel RPM identity')
    expected_rpm = device['kernel'].get('rpm_version_release', lock['release'].removesuffix('.aarch64'))
    if (not re.fullmatch(r'[0-9][A-Za-z0-9._+-]+', expected_rpm)
            or expected_rpm.split('-', 1)[0] != lock['release'].split('-', 1)[0]):
        raise ValueError('Device kernel RPM version pin does not match its ABI')
    if identity[2] != expected_rpm or identity[3] != packages[0].get('header_sha256'):
        raise ValueError('Device kernel RPM release/header does not match')
    provides = subprocess.check_output(['rpm', '-qp', '--nosignature', '--provides', str(rpm_path)], text=True).splitlines()
    if 'kernel-uname-r = ' + lock['release'] not in provides:
        raise ValueError('Device kernel lacks its exact uname provide')
    # Check the source-owned device-specific core firmware list independently
    # of the private manifest, including its recorded manifest hash.
    firmware_manifest = device_dir / 'firmware.manifest'
    if sha(firmware_manifest) != device['firmware']['manifest_sha256']:
        raise ValueError('Source firmware manifest changed')
    for line in firmware_manifest.read_text().splitlines()[1:]:
        kind, _, digest, relative, _ = line.split('\t')
        if kind == 'F':
            path = directory / 'rootfs/usr/lib/firmware' / relative
            if not path.is_file() or sha(path) != digest:
                raise ValueError('Required core firmware is missing or changed: ' + relative)
    return {'device': device_name, 'kernel_release': lock['release'],
            'kernel_rpm': str(rpm_path), 'kernel_nevra': rpm_path.stem,
            'manifest_sha256': sha(directory / 'hardware-inputs.json'),
            'files': rows, 'rootfs': str(directory / 'rootfs')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--device', required=True)
    args = parser.parse_args()
    print(json.dumps(admit(args.inputs.resolve(), args.device), sort_keys=True))


if __name__ == '__main__':
    main()
