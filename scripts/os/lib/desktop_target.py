#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Resolve desktop inputs from one source selection for both architectures.

The resulting NEVRAs are build requirements, not claims that artifacts exist.
Only explicitly scoped hardware packages may differ between architectures.
"""
import argparse
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARCHITECTURES = ('x86_64', 'aarch64')


def lines(path):
    return [line.split('#', 1)[0].strip() for line in path.read_text().splitlines()
            if line.split('#', 1)[0].strip()]


def target(root, architecture):
    if architecture not in ARCHITECTURES:
        raise ValueError(f'Unsupported desktop architecture: {architecture}')
    registry = json.loads((root / 'config/os/desktop-targets.json').read_text())
    if registry.get('schema') != 'org.projectluma.desktop-targets/v1':
        raise ValueError('Unknown desktop target schema')
    row = registry['architectures'][architecture]
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', row['base_digest']):
        raise ValueError('Invalid base image digest')
    if not row['base_tag'].endswith('-' + architecture):
        raise ValueError('Base image architecture does not match target')
    return row, registry['hardware_packages']


def package_name(nevra):
    return nevra.rsplit('.', 1)[0].rsplit('-', 2)[0]


def profile(root, architecture, name='full'):
    target(root, architecture)
    registry = json.loads((root / 'config/os/desktop-profiles.json').read_text())
    if registry.get('schema') != 'org.projectluma.desktop-profiles/v1':
        raise ValueError('Unknown desktop profile schema')
    if name not in registry['profiles']:
        raise ValueError(f'Unknown desktop profile: {name}')
    row = registry['profiles'][name]
    if architecture not in row['architectures']:
        raise ValueError(f'Desktop profile {name} does not support {architecture}')
    return row


def excluded(name, selection):
    return name in selection['excluded_packages'] or name.startswith(tuple(selection['excluded_package_prefixes']))


def capabilities(root, architecture, name='full'):
    selection = profile(root, architecture, name)
    return {'schema': 'org.projectluma.desktop-capabilities/v1',
            'architecture': architecture, 'profile': name,
            'capabilities': selection['capabilities'],
            'excluded_packages': selection['excluded_packages'],
            'excluded_package_prefixes': selection['excluded_package_prefixes']}


def roles(root, architecture, name='full'):
    selection = profile(root, architecture, name)
    values = lines(root / 'config/shared/application-packages.txt') + lines(root / 'config/desktop/application-packages.txt')
    return list(dict.fromkeys(value for value in values if not excluded(value, selection)))


def packages(root, architecture, *, fedora=False, profile_name='full'):
    selection = profile(root, architecture, profile_name)
    _, scoped = target(root, architecture)
    path = 'config/os/fedora-packages.txt' if fedora else 'config/desktop/packages.txt'
    result = []
    for value in lines(root / path):
        name = package_name(value) if re.search(r'\.(x86_64|aarch64|noarch)$', value) else value
        if excluded(name, selection):
            continue
        if name in scoped and architecture not in scoped[name]:
            continue
        # Architecture is the only transformation. Never select an older ARM
        # release from the historical handheld/preview pin variables.
        result.append(re.sub(r'\.(x86_64|aarch64)$', '.' + architecture, value))
    if len(set(result)) != len(result):
        raise ValueError('Duplicate resolved desktop packages')
    return result


def contract(root, architecture, profile_name='full'):
    profile(root, architecture, profile_name)
    row, _ = target(root, architecture)
    values = dict(line.split('=', 1) for line in lines(root / 'config/os/release.env'))
    values.update(LUMA_OS_ARCH=architecture,
                  LUMA_OS_REF_PREFIX=f'luma/{values["LUMA_OS_MAJOR"]}/{architecture}',
                  LUMA_OS_BASE_IMAGE_TAG=row['base_tag'],
                  LUMA_OS_BASE_IMAGE_DIGEST=row['base_digest'])
    if profile_name != 'full':
        values['LUMA_OS_PROFILE'] = profile_name
    if architecture != 'x86_64':
        values['LUMA_OS_GRAPH_PREFIX'] += '/' + architecture
    values['LUMA_OS_HUB_GRAPH_URL_TEMPLATE'] = re.sub(
        r'arch=(x86_64|aarch64)', 'arch=' + architecture,
        values['LUMA_OS_HUB_GRAPH_URL_TEMPLATE'])
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--arch', choices=ARCHITECTURES, required=True)
    parser.add_argument('--profile', default=os.environ.get('LUMA_OS_PROFILE', 'full'))
    parser.add_argument('output', choices=('pins', 'fedora', 'contract', 'plan', 'roles', 'capabilities'))
    args = parser.parse_args()
    if args.output == 'contract':
        print('\n'.join(f'{key}={value}' for key, value in contract(args.root, args.arch, args.profile).items()))
    elif args.output in ('pins', 'fedora'):
        print('\n'.join(packages(args.root, args.arch, fedora=args.output == 'fedora', profile_name=args.profile)))
    elif args.output == 'roles':
        print('\n'.join(roles(args.root, args.arch, args.profile)))
    elif args.output == 'capabilities':
        print(json.dumps(capabilities(args.root, args.arch, args.profile), indent=2))
    else:
        row, _ = target(args.root, args.arch)
        print(json.dumps({'architecture': args.arch, **row,
                          'profile': args.profile, 'capability_manifest': capabilities(args.root, args.arch, args.profile),
                          'packages': packages(args.root, args.arch, profile_name=args.profile),
                          'fedora_packages': packages(args.root, args.arch, fedora=True, profile_name=args.profile)}, indent=2))


if __name__ == '__main__':
    main()
