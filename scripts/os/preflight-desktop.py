#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Report exact desktop package availability before an expensive composition.

This checks actual RPM headers and file digests. Success does not qualify the
application baseline, boot, installation, updates, or any physical device.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from desktop_target import ARCHITECTURES, ROOT, packages, target


def inspect(root, architecture, directories, profile_name='full'):
    required = packages(root, architecture, profile_name=profile_name)
    required += [p for p in packages(root, architecture, fedora=True, profile_name=profile_name)
                 if p.endswith(('.x86_64', '.aarch64', '.noarch'))]
    candidates = {}
    for directory in directories:
        for path in directory.rglob('*.rpm'):
            if path.name.removesuffix('.rpm') in required:
                candidates.setdefault(path.name.removesuffix('.rpm'), []).append(path)
    rows = []
    for nevra in required:
        matches = candidates.get(nevra, [])
        files = []
        for path in sorted(set(matches)):
            header = subprocess.run(['rpm', '-qp', '--nosignature', '--qf', '%{NEVRA}', str(path)],
                                    capture_output=True, text=True, timeout=30)
            if header.returncode or header.stdout != nevra:
                raise ValueError(f'RPM header differs from filename: {path}')
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            files.append({'path': str(path), 'sha256': digest})
        if len({f['sha256'] for f in files}) > 1:
            raise ValueError(f'Conflicting RPM bodies for {nevra}; select one qualified input directory')
        rows.append({'nevra': nevra, 'status': 'ready' if files else 'missing', 'files': files})
    return {'schema': 'org.projectluma.desktop-preflight/v1', 'architecture': architecture, 'profile': profile_name,
            'kernel_provider': target(root, architecture)[0]['kernel_provider'],
            'ready': all(row['status'] == 'ready' for row in rows), 'packages': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=ARCHITECTURES, required=True)
    parser.add_argument('--profile', default=os.environ.get('LUMA_OS_PROFILE', 'full'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--package-dir', type=Path, action='append', required=True)
    args = parser.parse_args()
    for directory in args.package_dir:
        if not directory.is_dir():
            parser.error(f'Package directory is missing: {directory}')
    try:
        result = inspect(args.root, args.arch, args.package_dir, args.profile)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f'error: {error}\n')
    print(json.dumps(result, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    sys.exit(main())
