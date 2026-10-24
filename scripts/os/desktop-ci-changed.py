#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Avoid rebuilding qualified native providers after unrelated PR pushes."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INPUTS = {
    'webkit': ('scripts/packages/build-webkitgtk-luma.sh',
               'scripts/packages/run-in-rpm-builder.sh', 'config/desktop/inputs.env',
               'config/shared/webkitgtk-inputs.env', 'packaging/rpm/webkitgtk-luma.spec',
               'patches/webkitgtk/'),
    'browser': ('scripts/os/build-desktop-browser.sh', 'scripts/packages/build-viola-browser.sh',
                'scripts/packages/run-in-rpm-builder.sh', 'config/desktop/inputs.env',
                'packaging/rpm/viola-browser-stable.spec', 'src/external/viola/chromium-linux/'),
}


def affected(component, paths, architecture=None):
    if component not in INPUTS:
        raise ValueError('Unknown desktop provider')
    # Workflow/selector-only metadata changes do not invalidate a qualified
    # package. workflow_dispatch explicitly requests a fresh build when needed.
    for path in paths:
        if component == 'browser' and architecture is not None:
            engine_arch = {'native-30-engine.sha256': 'x86_64',
                           'native-31-engine.sha256': 'aarch64'}.get(Path(path).name)
            if engine_arch is not None and engine_arch != architecture:
                continue
        if any(path.startswith(prefix) for prefix in INPUTS[component]):
            return True
    return False


def matrix(architectures):
    pins = dict(line.split('=', 1) for line in
                (ROOT / 'config/desktop/inputs.env').read_text().splitlines()
                if line.startswith('FEDORA_RPM_BUILD_CONTAINER'))
    return {'include': [
        {'architecture': arch,
         'runner': 'ubuntu-24.04-arm' if arch == 'aarch64' else 'ubuntu-24.04',
         'image': pins['FEDORA_RPM_BUILD_CONTAINER_AARCH64' if arch == 'aarch64'
                       else 'FEDORA_RPM_BUILD_CONTAINER']}
        for arch in architectures]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', required=True, choices=INPUTS)
    parser.add_argument('--base', default='')
    parser.add_argument('--print-matrix', action='store_true')
    args = parser.parse_args()
    if not args.base or set(args.base) == {'0'}:
        print(json.dumps(matrix(['x86_64', 'aarch64'])) if args.print_matrix else 'true')
        return
    if not re.fullmatch(r'[a-f0-9]{40,64}', args.base):
        raise ValueError('Expected an exact previous commit identity')
    data = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--name-only', '-z',
                                    args.base, 'HEAD', '--'], text=True)
    paths = list(filter(None, data.split('\0')))
    if args.print_matrix:
        print(json.dumps(matrix([arch for arch in ('x86_64', 'aarch64')
                                 if affected(args.component, paths, arch)])))
    else:
        print('true' if affected(args.component, paths) else 'false')


if __name__ == '__main__':
    main()
