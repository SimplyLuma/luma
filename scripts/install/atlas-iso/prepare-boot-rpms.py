#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Carry exactly the payload's verified boot inputs into its installer runtime."""
import argparse
import hashlib
import re
import shutil
from pathlib import Path

KEYS = ('LUMA_BOOT_THEME_NEVRA', 'FIGTREE_NEVRA', 'PLYMOUTH_NEVRA',
        'PLYMOUTH_CORE_LIBS_NEVRA', 'PLYMOUTH_GRAPHICS_LIBS_NEVRA',
        'PLYMOUTH_PLUGIN_SCRIPT_NEVRA', 'PLYMOUTH_SCRIPTS_NEVRA',
        'PLYMOUTH_PLUGIN_LABEL_NEVRA', 'PLYMOUTH_PLUGIN_TWO_STEP_NEVRA',
        'PLYMOUTH_SYSTEM_THEME_NEVRA', 'PLYMOUTH_THEME_SPINNER_NEVRA')

def prepare(inputs, manifest, pool, output):
    pins = dict(re.findall(r'^([A-Z_]+)=([^\s]+)$', inputs.read_text(), re.M))
    entries = {}
    for line in manifest.read_text().splitlines():
        nevra, digest, header = line.split()
        if nevra in entries or not re.fullmatch(r'[0-9a-f]{64}', digest) or not re.fullmatch(r'[0-9a-f]{64}', header):
            raise ValueError(f'invalid or duplicate package manifest entry: {nevra}')
        entries[nevra] = digest, header
    selected = []
    for key in KEYS:
        nevra = pins[key]
        digest, header = entries[nevra]
        path = pool / f'{nevra}.rpm'
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'boot RPM digest changed: {nevra}')
        selected.append((path, digest, header))
    # Validate every input before copying any one of them.
    output.mkdir(parents=True, exist_ok=True)
    for path, _, _ in selected:
        shutil.copyfile(path, output / path.name)
    (output.parent / 'boot-packages.manifest').write_text(''.join(
        f'{path.stem} {digest} {header}\n' for path, digest, header in selected))
    return selected

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('inputs', 'manifest', 'pool', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.inputs, args.manifest, args.pool, args.output)
