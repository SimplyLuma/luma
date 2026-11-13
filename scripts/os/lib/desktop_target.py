#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Resolve desktop inputs from one source selection for both architectures.

The resulting NEVRAs are build requirements, not claims that artifacts exist.
Only explicitly scoped hardware packages may differ between architectures.
A hardware target (config/os/desktop-targets.json hardware_targets, admitted
in lib/devices.tsv) is the same desktop plus its device packages, optionally
on its own kernel, published on its own ref luma/<major>/<arch>/<slug>/.
A device repository (device_repositories) holds packages no image carries,
which a device installs as client-side overrides of the generic commit (the
Raspberry Pi 5 kernel).
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


def hardware(root, architecture, name):
    """The hardware target's selection, or None for the generic desktop."""
    registry = json.loads((root / 'config/os/desktop-targets.json').read_text())
    targets = registry.get('hardware_targets', {})
    if not name:
        return None
    if name not in targets:
        raise ValueError(f'Unknown desktop hardware target: {name}')
    row = dict(targets[name])
    table = {}
    for line in (root / 'scripts/os/lib/devices.tsv').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            fields = line.split('\t')
            table[fields[0]] = fields
    if name not in table:
        raise ValueError(f'Hardware target is not admitted in devices.tsv: {name}')
    row['slug'], row['arch'] = table[name][1], table[name][2]
    if row['arch'] != architecture:
        raise ValueError(f'Hardware target {name} is {row["arch"]} only')
    if row.get('kernel') and row['kernel'] not in row['packages']:
        raise ValueError(f'Hardware target {name} kernel is not one of its packages')
    return row


def device_package_names(root):
    """Every package name some hardware target or device repository owns; generic images never get them."""
    registry = json.loads((root / 'config/os/desktop-targets.json').read_text())
    return ({name for row in registry.get('hardware_targets', {}).values() for name in row['packages']}
            | {name for row in registry.get('device_repositories', {}).values() for name in row['packages']})


def device_repository(root, name):
    """A device repository's row and its pinned NEVRAs (config/desktop/packages.txt)."""
    registry = json.loads((root / 'config/os/desktop-targets.json').read_text())
    repositories = registry.get('device_repositories', {})
    if name not in repositories:
        raise ValueError(f'Unknown device repository: {name}')
    row = repositories[name]
    if row['architecture'] not in ARCHITECTURES:
        raise ValueError(f'Device repository {name} has an unsupported architecture')
    wanted = set(row['packages'])
    pins = [value for value in lines(root / 'config/desktop/packages.txt')
            if re.search(r'\.(aarch64|x86_64|noarch)$', value) and package_name(value) in wanted]
    pins = [re.sub(r'\.(x86_64|aarch64)$', '.' + row['architecture'], value) for value in pins]
    missing = wanted - {package_name(value) for value in pins}
    if missing:
        raise ValueError(f'Device repository packages without a pin: {", ".join(sorted(missing))}')
    return row, pins


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


def packages(root, architecture, *, fedora=False, profile_name='full', hardware_target=''):
    selection = profile(root, architecture, profile_name)
    _, scoped = target(root, architecture)
    device = hardware(root, architecture, hardware_target)
    owned = device_package_names(root)
    wanted = set(device['packages']) if device else set()
    path = 'config/os/fedora-packages.txt' if fedora else 'config/desktop/packages.txt'
    values = lines(root / path)
    if fedora and selection['capabilities'].get('phone'):
        values += lines(root / 'config/os/phone-packages.txt')
    result = []
    for value in values:
        name = package_name(value) if re.search(r'\.(x86_64|aarch64|noarch)$', value) else value
        if excluded(name, selection):
            continue
        if name in scoped and architecture not in scoped[name]:
            continue
        if name in owned and name not in wanted:
            continue
        wanted.discard(name)
        # Architecture is the only transformation. Never select an older ARM
        # release from the historical handheld/preview pin variables.
        result.append(re.sub(r'\.(x86_64|aarch64)$', '.' + architecture, value))
    if len(set(result)) != len(result):
        raise ValueError('Duplicate resolved desktop packages')
    if wanted and not fedora:
        raise ValueError(f'Hardware target packages without a pin: {", ".join(sorted(wanted))}')
    return result


def contract(root, architecture, profile_name='full', hardware_target=''):
    profile(root, architecture, profile_name)
    row, _ = target(root, architecture)
    device = hardware(root, architecture, hardware_target)
    values = dict(line.split('=', 1) for line in lines(root / 'config/os/release.env'))
    values.update(LUMA_OS_ARCH=architecture,
                  LUMA_OS_REF_PREFIX=f'luma/{values["LUMA_OS_MAJOR"]}/{architecture}',
                  LUMA_OS_BASE_IMAGE_TAG=row['base_tag'],
                  LUMA_OS_BASE_IMAGE_DIGEST=row['base_digest'])
    if profile_name != 'full':
        values['LUMA_OS_PROFILE'] = profile_name
    if device:
        values['LUMA_OS_HARDWARE_TARGET'] = hardware_target
        values['LUMA_OS_KERNEL_PACKAGE'] = device.get('kernel', '')
        values['LUMA_OS_REF_PREFIX'] += '/' + device['slug']
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
    parser.add_argument('--hardware-target', default=os.environ.get('LUMA_OS_HARDWARE_TARGET', ''))
    parser.add_argument('--device-repository', default='')
    parser.add_argument('output', choices=('pins', 'fedora', 'contract', 'plan', 'roles', 'capabilities', 'device-repository'))
    args = parser.parse_args()
    if args.output == 'device-repository':
        print('\n'.join(device_repository(args.root, args.device_repository)[1]))
    elif args.output == 'contract':
        print('\n'.join(f'{key}={value}' for key, value in contract(args.root, args.arch, args.profile, args.hardware_target).items()))
    elif args.output in ('pins', 'fedora'):
        print('\n'.join(packages(args.root, args.arch, fedora=args.output == 'fedora', profile_name=args.profile,
                                 hardware_target=args.hardware_target)))
    elif args.output == 'roles':
        print('\n'.join(roles(args.root, args.arch, args.profile)))
    elif args.output == 'capabilities':
        print(json.dumps(capabilities(args.root, args.arch, args.profile), indent=2))
    else:
        row, _ = target(args.root, args.arch)
        print(json.dumps({'architecture': args.arch, **row,
                          'profile': args.profile, 'hardware_target': args.hardware_target,
                          'kernel_package': (hardware(args.root, args.arch, args.hardware_target) or {}).get('kernel', ''), 'capability_manifest': capabilities(args.root, args.arch, args.profile),
                          'packages': packages(args.root, args.arch, profile_name=args.profile,
                                               hardware_target=args.hardware_target),
                          'fedora_packages': packages(args.root, args.arch, fedora=True, profile_name=args.profile)}, indent=2))


if __name__ == '__main__':
    main()
