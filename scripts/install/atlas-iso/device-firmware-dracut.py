#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Include device-tree-selected firmware at early boot, and verify its bytes."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def firmware_rows(root, manifest):
    data = json.loads(manifest.read_text())
    if data.get('device') == 'generic':
        return []
    if (data.get('schema') != 'org.projectluma.hardware-inputs/v1'
            or data.get('scope') != 'owner-personal-test-only'):
        raise ValueError('Unrecognized device firmware manifest')
    rows = []
    for row in data['files']:
        name = row['path']
        if not name.startswith('rootfs/usr/lib/firmware/'):
            continue
        relative = name.removeprefix('rootfs/')
        path = PurePosixPath(relative)
        if (not re.fullmatch(r'[A-Za-z0-9_+./-]+', relative)
                or '..' in path.parts or str(path) != relative):
            raise ValueError('Unsafe firmware path')
        actual = root / relative
        if not actual.resolve().is_relative_to(root.resolve()) or not actual.is_file():
            raise ValueError('Device firmware is missing or escapes root: ' + relative)
        with actual.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != row['sha256']:
            raise ValueError('Device firmware bytes changed: ' + relative)
        rows.append(relative)
    if not rows:
        raise ValueError('Device has no early firmware files')
    return sorted(set(rows))


def configure(root, manifest):
    rows = firmware_rows(root, manifest)
    if not rows:
        return
    destination = root/'usr/lib/dracut/dracut.conf.d/50-luma-device-firmware.conf'
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError('Dracut configuration escapes root')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text('# Generated from admitted device firmware.\ninstall_items+=" '
                           + ' '.join('/'+name for name in rows) + ' "\n')
    destination.chmod(0o644)
    print('Configured exact early device firmware files:', len(rows))


def verify(root, manifest):
    rows = firmware_rows(root, manifest)
    print('Verified exact device firmware files:', len(rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('configure', 'verify'))
    parser.add_argument('root', type=Path)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    globals()[args.action](args.root.resolve(), args.manifest.resolve())
