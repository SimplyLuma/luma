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


# These are the desktop pins actually consumed by the provider builders.
# WebKit's own source pins live in config/shared/webkitgtk-inputs.env.
DESKTOP_PIN_PREFIXES = {
    'webkit': ('FEDORA_RPM_BUILD_CONTAINER',),
    'browser': ('FEDORA_RPM_BUILD_CONTAINER', 'GTK4_', 'LIBADWAITA_',
                'LUMA_DEVELOPER_PLATFORM_'),
}


def changed_input_keys(before, after):
    def values(body):
        return dict(line.split('=', 1) for line in body.splitlines()
                    if re.match(r'^[A-Z][A-Z0-9_]*=', line))
    old, new = values(before), values(after)
    return {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}


def affected(component, paths, architecture=None, input_keys=None):
    if component not in INPUTS:
        raise ValueError('Unknown desktop provider')
    # Workflow/selector-only metadata changes do not invalidate a qualified
    # package. workflow_dispatch explicitly requests a fresh build when needed.
    for path in paths:
        if path == 'config/desktop/inputs.env' and input_keys is not None:
            if not any(key.startswith(DESKTOP_PIN_PREFIXES[component]) for key in input_keys):
                continue
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
    input_keys = None
    if 'config/desktop/inputs.env' in paths:
        before = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                          f'{args.base}:config/desktop/inputs.env'], text=True)
        after = (ROOT / 'config/desktop/inputs.env').read_text()
        input_keys = changed_input_keys(before, after)
    if args.print_matrix:
        print(json.dumps(matrix([arch for arch in ('x86_64', 'aarch64')
                                 if affected(args.component, paths, arch, input_keys)])))
    else:
        print('true' if affected(args.component, paths, input_keys=input_keys) else 'false')


if __name__ == '__main__':
    main()
