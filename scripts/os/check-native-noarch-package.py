#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Check maintained noarch package identities and required shipped files."""
import argparse
import hashlib
from pathlib import Path
import subprocess

REQUIRED = {
    'luma-application-installer': {
        '/usr/bin/luma-installer', '/usr/bin/luma-depot',
        '/usr/lib/systemd/user/luma-retired-apps.service',
        '/usr/lib/systemd/system/luma-retired-system-apps.service',
        '/usr/lib/systemd/user/default.target.wants/luma-retired-apps.service',
        '/usr/lib/systemd/system/multi-user.target.wants/luma-retired-system-apps.service',
    },
    'luma-ari': {'/usr/bin/ari', '/usr/lib/systemd/user/ari-daemon.service'},
    'luma-desktop-launcher-policy': {
        '/usr/share/luma/system-flatpak-replacements.txt',
        '/usr/libexec/luma-flatpak-replacements',
    },
}


def output(*command):
    return subprocess.check_output(command, text=True).strip()


def check(root, spec):
    name = output('rpmspec', '--query', '--rpms', '--qf', '%{NAME}', str(spec))
    if name not in REQUIRED:
        raise ValueError('Not a maintained noarch component')
    expected = output('rpmspec', '--query', '--rpms', '--qf', '%{NEVRA}', str(spec))
    directory = (root / 'build/packages' / name).resolve()
    packages = list(directory.glob('RPMS/**/*.rpm'))
    if len(packages) != 1:
        raise ValueError('Expected exactly one noarch binary package')
    rpm = packages[0]
    if output('rpm', '-qp', '--nosignature', '--qf', '%{ARCH}', str(rpm)) != 'noarch':
        raise ValueError('Package is not architecture independent')
    if output('rpm', '-qp', '--nosignature', '--qf', '%{NEVRA}', str(rpm)) != expected:
        raise ValueError('Built package does not match the current source spec')
    covered = set()
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        digest, relative = line.split(maxsplit=1)
        path = (directory / relative.lstrip('*')).resolve()
        if not path.is_relative_to(directory):
            raise ValueError('Checksum path escaped package output')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Package checksum mismatch')
        covered.add(path)
    if rpm.resolve() not in covered:
        raise ValueError('Binary package is missing checksum coverage')
    files = set(output('rpm', '-qpl', '--nosignature', str(rpm)).splitlines())
    missing = REQUIRED[name] - files
    if missing:
        raise ValueError(f'Missing shipped files: {sorted(missing)}')
    if name == 'luma-application-installer' and not any(
            path.endswith('/site-packages/luma_installer/retired_apps.py') for path in files):
        raise ValueError('Installer does not ship its retirement implementation')
    if name == 'luma-ari':
        requirements = output('rpm', '-qp', '--nosignature', '--requires', str(rpm)).splitlines()
        if 'luma-display-config' not in requirements or any(
                row.split()[0] == 'luma-displays' for row in requirements):
            raise ValueError('Ari must depend on the headless display backend')
    print(f'Checked {expected}: checksum, noarch header and required payload')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, type=Path)
    args = parser.parse_args()
    check(Path.cwd(), args.spec)


if __name__ == '__main__':
    main()
