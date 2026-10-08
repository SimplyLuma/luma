# SPDX-License-Identifier: GPL-3.0-only
"""Compile the existing qualified Luma GTK menu source on the Linux builder.

An owned disposable container provides sassc. No installed system package,
shared Luma checkout, or existing build container is modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

IMAGE = 'registry.fedoraproject.org/fedora@sha256:69b1219730fe52d6cbb0874cbc1a36dd43b722354a603f6cdf73acbff8dd7c59'
QUALIFIED_SHA256 = 'b7e84d5caebda22c6ca8f1aba941a39b81940ce059a1a809c65ebb3e48e9f7fb'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--patch', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expected-sha256', default=QUALIFIED_SHA256)
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise RuntimeError('Compile toolkit resources on the Linux build server')
    data = args.patch.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.expected_sha256:
        raise RuntimeError('Qualified Luma source patch hash mismatch')
    marker = '+++ b/gtk/theme/Default/_luma-menu.scss\n'
    section = data.decode().split(marker, 1)[1].split('\ndiff --git', 1)[0]
    lines = section.splitlines()
    match = re.fullmatch(r'@@ -0,0 \+1,(\d+) @@', lines[0])
    if not match or any(not line.startswith('+') for line in lines[1:]):
        raise RuntimeError('Unexpected native menu source patch structure')
    if len(lines) - 1 != int(match[1]):
        raise RuntimeError('Native menu patch line count mismatch')
    source = '\n'.join(line[1:] for line in lines[1:]) + '\n'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'source.patch').write_bytes(data)
    (output / '_luma-menu.scss').write_text(source)
    for mode in ('light', 'dark'):
        (output / (mode + '.scss')).write_text('$variant: ' + mode + ";\n@import 'luma-menu';\n")
    with (output / 'build.log').open('w') as log:
        subprocess.run(['podman', 'run', '--rm', '--name', 'viola-native-menu-resources-' + str(os.getpid()),
            '-v', str(output) + ':/work:Z', IMAGE, 'sh', '-c',
            'dnf5 -y install sassc && sassc /work/light.scss /work/menu-light.css && '
            'sassc /work/dark.scss /work/menu-dark.css && rpm -q sassc libsass > /work/compiler-packages.txt'],
            stdout=log, stderr=subprocess.STDOUT, check=True)
    manifest = {'classification': 'private toolkit resource candidate; existing native menu source unchanged',
                'source': str(args.patch.resolve()), 'sha256': digest,
                'source_license': 'LGPL-2.1-or-later (Luma GTK source patch)',
                'builder_image': IMAGE, 'compiler_packages': (output / 'compiler-packages.txt').read_text().splitlines(),
                'compiled': {}}
    for mode in ('light', 'dark'):
        path = output / ('menu-' + mode + '.css')
        manifest['compiled'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / 'provenance.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
