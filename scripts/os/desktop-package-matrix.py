#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Select affected native package groups, always for both desktop targets."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED = {'config/desktop/inputs.env', 'config/os/desktop-build-groups.json',
          'scripts/packages/run-in-rpm-builder.sh', 'scripts/packages/spec-release.sh',
          'scripts/os/desktop-package-matrix.py', '.github/workflows/desktop-components.yml'}


def matrix(root, changed=None):
    registry = json.loads((root / 'config/os/desktop-build-groups.json').read_text())
    if registry.get('schema') != 'org.projectluma.desktop-build-groups/v1':
        raise ValueError('Unknown native build group schema')
    values = dict(line.split('=', 1) for line in (root / 'config/desktop/inputs.env').read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    result = []
    for group, row in registry['groups'].items():
        affected = changed is None or bool(SHARED.intersection(changed)) or any(
            path == row['recipe'] or any(path.startswith(prefix) for prefix in row['paths']) for path in changed)
        if not affected:
            continue
        for architecture, runner, key in (
            ('x86_64', 'ubuntu-24.04', 'FEDORA_RPM_BUILD_CONTAINER'),
            ('aarch64', 'ubuntu-24.04-arm', 'FEDORA_RPM_BUILD_CONTAINER_AARCH64')):
            result.append({'component': group, 'recipe': row['recipe'], 'architecture': architecture,
                           'runner': runner, 'image': values[key]})
    return {'include': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--head', default='HEAD')
    args = parser.parse_args()
    changed = None
    if args.base:
        data = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--name-only', '-z',
                                        args.base, args.head], text=True)
        changed = set(filter(None, data.split('\0')))
    print(json.dumps(matrix(ROOT, changed), separators=(',', ':')))


if __name__ == '__main__':
    main()
