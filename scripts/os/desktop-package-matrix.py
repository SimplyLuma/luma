#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Select affected native package groups, with both desktop targets as the default."""
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = 'config/os/desktop-build-groups.json'
WORKFLOW = '.github/workflows/desktop-components.yml'
SHARED = {'config/desktop/inputs.env',
          'scripts/packages/run-in-rpm-builder.sh', 'scripts/packages/spec-release.sh',
          '.github/workflows/desktop-components.yml'}


def routing_only_workflow_additions(previous, current):
    """Only an exact, ordinary paths-list addition may avoid a shared rebuild."""
    marker = '  pull_request:\n    paths:\n'
    def split(body):
        if not isinstance(body, str) or body.count(marker) != 1:
            return None
        prefix, rest = body.split(marker)
        lines = rest.splitlines(keepends=True)
        paths = []
        while lines and lines[0].startswith('      '):
            line = lines.pop(0)
            match = re.fullmatch(r"      - '([A-Za-z0-9_.*?/-]+)'\n", line)
            if not match:
                return None
            paths.append(match.group(1))
        if not paths or len(paths) != len(set(paths)):
            return None
        return prefix + marker, paths, ''.join(lines)
    old, new = split(previous), split(current)
    return bool(old and new and old[0] == new[0] and old[2] == new[2]
                and set(old[1]) < set(new[1])
                and [path for path in new[1] if path in old[1]] == old[1])


def matrix(root, changed=None, previous_registry=None, previous_workflow=None):
    registry = json.loads((root / REGISTRY).read_text())
    if registry.get('schema') != 'org.projectluma.desktop-build-groups/v1':
        raise ValueError('Unknown native build group schema')
    values = dict(line.split('=', 1) for line in (root / 'config/desktop/inputs.env').read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    shared_changed = SHARED.intersection(changed or ())
    if WORKFLOW in shared_changed and routing_only_workflow_additions(
            previous_workflow, (root / WORKFLOW).read_text()):
        shared_changed.remove(WORKFLOW)
    result = []
    for group, row in registry['groups'].items():
        architectures = row.get('architectures', ['x86_64', 'aarch64'])
        if (not isinstance(architectures, list) or not architectures or
                len(set(architectures)) != len(architectures) or
                any(arch not in ('x86_64', 'aarch64') for arch in architectures)):
            raise ValueError(f'Invalid native architectures for {group}')
        if not isinstance(row.get('toolkit', False), bool):
            raise ValueError(f'Invalid toolkit prerequisite for {group}')
        registry_changed = changed is not None and REGISTRY in changed and (
            previous_registry is None or previous_registry.get('groups', {}).get(group) != row)
        affected = changed is None or registry_changed or bool(shared_changed) or (
            bool(row.get('spec')) and 'scripts/os/check-native-noarch-package.py' in changed) or any(
            path == row['recipe'] or any(path.startswith(prefix) for prefix in row['paths']) for path in changed)
        if not affected:
            continue
        for architecture, runner, key in (
            ('x86_64', 'ubuntu-24.04', 'FEDORA_RPM_BUILD_CONTAINER'),
            ('aarch64', 'ubuntu-24.04-arm', 'FEDORA_RPM_BUILD_CONTAINER_AARCH64')):
            if architecture not in architectures:
                continue
            result.append({'toolkit': row.get('toolkit', False), 'spec': row.get('spec', ''),
                           'component': group, 'recipe': row['recipe'], 'architecture': architecture,
                           'runner': runner, 'image': values[key]})
    return {'include': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--head', default='HEAD')
    args = parser.parse_args()
    changed = None
    previous_registry = None
    previous_workflow = None
    if args.base:
        data = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--name-only', '-z',
                                        args.base, args.head], text=True)
        changed = set(filter(None, data.split('\0')))
        if WORKFLOW in changed:
            old_workflow = subprocess.run(['git', '-C', str(ROOT), 'show', f'{args.base}:{WORKFLOW}'],
                                          text=True, capture_output=True)
            if old_workflow.returncode == 0:
                previous_workflow = old_workflow.stdout
        if REGISTRY in changed:
            old = subprocess.run(['git', '-C', str(ROOT), 'show', f'{args.base}:{REGISTRY}'],
                                 text=True, capture_output=True)
            if old.returncode == 0:
                previous_registry = json.loads(old.stdout)
                if previous_registry.get('schema') != 'org.projectluma.desktop-build-groups/v1':
                    raise ValueError('Unknown previous native build group schema')
    print(json.dumps(matrix(ROOT, changed, previous_registry, previous_workflow), separators=(',', ':')))


if __name__ == '__main__':
    main()
