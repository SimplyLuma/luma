#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Admit the offline app input into the declared OS composition."""
import argparse
import importlib.machinery
import importlib.util
import json
import platform
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
loader = importlib.machinery.SourceFileLoader(
    'luma_os_app_baseline', str(REPO/'image/luma-desktop/rootfs/usr/libexec/luma-app-baseline'))
spec = importlib.util.spec_from_loader(loader.name, loader)
baseline = importlib.util.module_from_spec(spec)
loader.exec_module(baseline)


def admit(root, shipping):
    manifest = baseline.validate(root, platform.machine())
    if baseline.digest(root/'shipping.json') != baseline.digest(shipping):
        raise ValueError('The app baseline does not match this OS source selection.')
    return {'schema':'org.projectluma.os-app-input/v1',
            'manifest_sha256':baseline.digest(root/'manifest.json'),
            'architecture':manifest['architecture'],
            'applications':24, 'runtimes':3,
            'bundle_bytes':sum(row['bytes'] for row in manifest['bundles']),
            'bundles':manifest['bundles']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--shipping', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(admit(args.baseline, args.shipping), sort_keys=True))
