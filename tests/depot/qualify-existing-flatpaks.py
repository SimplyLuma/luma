#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Opt-in signed installed-update check, using retained offline baseline bundles.

Run with --baseline BASELINE_DIRECTORY --output NEW_DIRECTORY. This creates an
isolated per-user installation; production installations and app data are never
changed. It checks real Depot discovery for Filer/Ari and Filer update/rollback.
Runtime dependencies, GUI execution and Ari's host service require OS VM tests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
out = args.output.resolve()
out.mkdir(mode=0o700, parents=True, exist_ok=False)
for key, directory in [('FLATPAK_USER_DIR', 'flatpak'), ('XDG_DATA_HOME', 'data'),
                       ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                       ('XDG_STATE_HOME', 'state')]:
    path = out / directory
    path.mkdir(mode=0o700)
    os.environ[key] = str(path)
os.environ['LUMA_DEPOT_DATA_DIRECTORY'] = str(root / 'src/luma-installer/data')
sys.path[:0] = [str(root / 'src/luma-installer'), str(root / 'src/luma-depot')]

import gi  # noqa: E402
gi.require_version('Flatpak', '1.0')
from gi.repository import Flatpak, Gio  # noqa: E402
from luma_depot import native  # noqa: E402
from luma_installer import depot_flatpak  # noqa: E402


def command(*argv):
    subprocess.run(argv, check=True)


command('flatpak', 'remote-add', '--user', 'luma',
        str(root / 'src/luma-installer/data/luma.flatpakrepo'))
manifest = json.loads((args.baseline / 'manifest.json').read_text())
baselines = []
repo = str(out / 'flatpak/repo')
for app in ('org.gnome.Nautilus', 'org.projectluma.Ari'):
    row = next(r for r in manifest['bundles'] if r['ref'] == f'app/{app}/x86_64/beta')
    bundle = (args.baseline / row['file']).resolve()
    assert bundle.parent == args.baseline.resolve()
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == row['sha256']
    command('flatpak', 'install', '--user', '--noninteractive', '--no-deps', '--no-related',
            'luma', app + '//beta')
    command('flatpak', 'build-import-bundle', '--no-update-summary', repo, str(bundle))
    # Trust the remote key shipped in source, not a key supplied by the fixture.
    command('ostree', 'show', '--repo=' + repo, '--gpg-verify-remote=luma', row['commit'])
    # Offline-cache setup only: activate the authenticated retained baseline.
    command('ostree', 'refs', '--repo=' + repo, '--force',
            '--create=luma:' + row['ref'], row['commit'])
    command('flatpak', 'update', '--user', '--noninteractive', '--no-deps', '--no-related',
            '--no-pull', '--commit=' + row['commit'], app + '//beta')
    baselines.append(row)
(out / 'baseline.json').write_text(json.dumps(baselines, indent=2) + '\n')
installation = Flatpak.Installation.new_for_path(Gio.File.new_for_path(str(out / 'flatpak')), True, None)


def refs():
    return {ref.get_name(): ref for ref in
            installation.list_installed_refs_by_kind(Flatpak.RefKind.APP, None)}


for row in baselines:
    ref = refs()[row['ref'].split('/')[1]]
    assert ref.get_commit() == row['commit'] and ref.get_origin() == 'luma'
provider = native.NativeInstallation()
# Only the installation selection is replaced; discovery, signature policy,
# immutable permission comparison and transactions use the production backend.
with patch.object(native, '_installations', return_value=(installation,)):
    updates = provider._check_updates(None)
assert set(updates) == {'org.gnome.Nautilus', 'org.projectluma.Ari'}
for info in updates.values():
    assert info['commit'] != info['installed_commit']
    assert not any(change.change in ('added', 'widened') for change in info['changes'])
app = 'org.gnome.Nautilus'
prior = refs()[app]
state = out / 'config/nautilus/fixture-state'
state.parent.mkdir()
state.write_bytes(b'preserve-existing-state\n')
state_sha = hashlib.sha256(state.read_bytes()).hexdigest()
source = depot_flatpak.resolve(installation, app, 'x86_64', None, 'luma', 'beta')
tx = depot_flatpak.transaction(installation, source, update=True,
                                expected_installed_commit=prior.get_commit())
tx.set_disable_dependencies(True)
tx.set_disable_related(True)
tx.run(None)
installation.drop_caches(None)
updated = refs()[app]
assert updated.get_commit() == updates[app]['commit']
assert updated.get_branch() == 'beta' and updated.get_origin() == 'luma'
assert hashlib.sha256(state.read_bytes()).hexdigest() == state_sha
rollback = depot_flatpak.revert_transaction(installation, updated, prior.get_commit())
rollback.set_disable_dependencies(True)
rollback.set_disable_related(True)
rollback.run(None)
installation.drop_caches(None)
assert refs()[app].get_commit() == prior.get_commit()
assert hashlib.sha256(state.read_bytes()).hexdigest() == state_sha
report = {'result': 'PASS', 'scope': 'isolated signed Flatpak storage and Depot backend',
          'apps': {app: {key: value for key, value in info.items() if key != 'changes'}
                   for app, info in updates.items()},
          'filer_update_and_rollback': True, 'data_preserved': True,
          'permission_growth': False, 'runtime_dependencies_installed': False,
          'app_execution_and_ari_host_service_tested': False}
(out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS: older Filer/Ari update discovery; Filer update, rollback and state preservation')
