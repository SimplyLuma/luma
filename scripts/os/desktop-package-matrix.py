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
    marker = 'on:\n  pull_request:\n    paths:\n'
    def split(body):
        if not isinstance(body, str) or body.count(marker) != 1:
            return None
        prefix, rest = body.split(marker)
        if prefix and not prefix.endswith('\n'):
            return None
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


def selection_base_only_workflow_change(previous, current):
    old = 'github.event.before || github.event.pull_request.base.sha'
    new = 'github.event.pull_request.base.sha || github.event.before'
    return (isinstance(previous, str) and previous.count(old) == 1
            and previous.replace(old, new, 1) == current)


def matrix(root, changed=None, previous_registry=None, previous_workflow=None,
           previous_inputs=None):
    registry = json.loads((root / REGISTRY).read_text())
    if registry.get('schema') != 'org.projectluma.desktop-build-groups/v1':
        raise ValueError('Unknown native build group schema')
    values = dict(line.split('=', 1) for line in (root / 'config/desktop/inputs.env').read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    shared_changed = SHARED.intersection(changed or ())
    if WORKFLOW in shared_changed and (routing_only_workflow_additions(
            previous_workflow, (root / WORKFLOW).read_text()) or selection_base_only_workflow_change(
            previous_workflow, (root / WORKFLOW).read_text())):
        shared_changed.remove(WORKFLOW)
    pin_consumers = None
    if 'config/desktop/inputs.env' in shared_changed and previous_inputs is not None:
        keys = {key for key in previous_inputs.keys() | values.keys()
                if previous_inputs.get(key) != values.get(key)}
        scopes = {
            # Shell packages are checked by the native UI workflow.
            'GNOME_SHELL_': set(),
            'PRAIRIE_ICON_THEME_': {'icon-theme', 'ari'},
            'LUMA_APPLICATION_INSTALLER_': {'application-installer', 'ari'},
            'LUMA_DEVELOPER_PLATFORM_': {group for group, row in registry['groups'].items()
                                        if row.get('toolkit', False)},
        }
        if all(any(key.startswith(prefix) for prefix in scopes) for key in keys):
            pin_consumers = set().union(*(groups for prefix, groups in scopes.items()
                                          if any(key.startswith(prefix) for key in keys)))
            shared_changed.remove('config/desktop/inputs.env')
        # Unknown pins retain the conservative complete component rebuild.
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
            pin_consumers is not None and group in pin_consumers) or (
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
    previous_inputs = None
    if args.base:
        data = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--name-only', '-z',
                                        args.base, args.head], text=True)
        changed = set(filter(None, data.split('\0')))
        if 'config/desktop/inputs.env' in changed:
            old_inputs = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                                  f'{args.base}:config/desktop/inputs.env'], text=True)
            previous_inputs = dict(line.split('=', 1) for line in old_inputs.splitlines()
                                   if line and not line.startswith('#') and '=' in line)
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
    print(json.dumps(matrix(ROOT, changed, previous_registry, previous_workflow, previous_inputs), separators=(',', ':')))


if __name__ == '__main__':
    main()
