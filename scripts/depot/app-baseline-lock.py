#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Keep the OS image's offline app baseline on the source that built it.

config/os/app-baseline-lock.json pins, per CPU, the signed offline baseline
(manifest SHA256) that images ship, and per application the version, the
signed commit in that baseline and a fingerprint of the source it was built
from. The fingerprint covers the inputs seal-first-party-source.py seals from
this repository (tests excluded) plus the Flatpak recipe.

  app-baseline-lock.py check
      Fail when an application's source no longer matches the fingerprint of
      its baseline bundle (the source changed but the bundle was not rebuilt),
      or when its version differs from its metainfo. Runs in source checks.

  app-baseline-lock.py fingerprint [APP_ID...]
      Print the current source fingerprints.

  app-baseline-lock.py write --baseline ARCH=DIR... [--rebuilt APP_ID...]
      Pin new baselines. Every application whose commit changed must be named
      with --rebuilt; their fingerprints and versions are taken from this tree.

  app-baseline-lock.py verify-baseline --arch ARCH DIR
      Refuse a baseline other than the pinned one (scripts/os/build-image.sh).
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[2]
LOCK = 'config/os/app-baseline-lock.json'
SHIPPING = 'config/os/first-party-app-baseline.json'
SCHEMA = 'org.projectluma.os-app-baseline-lock/v1'
ARCHES = ('aarch64', 'x86_64')


def _sealer(repo):
    path = repo / 'scripts/depot/seal-first-party-source.py'
    spec = importlib.util.spec_from_file_location('luma_seal_first_party_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _short_names(sealer):
    return {entry['id']: short for short, entry in sealer.APPS.items()}


def fingerprint(repo, app_id, sealer=None):
    """Return the source fingerprint of APP_ID, or None when it has no recipe here."""
    sealer = sealer or _sealer(repo)
    short = _short_names(sealer).get(app_id)
    recipe = repo / 'packaging/flatpak/apps' / app_id / f'{app_id}.yml'
    if short is None or not recipe.is_file():
        return None
    entry = sealer.APPS[short]
    tree = entry['source_tree'].rstrip('/') + '/'
    files, tail = sealer.repo_inputs(repo, short)
    materials = {}
    for name, data, mode in files + tail:
        if name.startswith(tree) and {'test', 'tests'} & set(name[len(tree):].split('/')[:-1]):
            continue
        if name == 'config/desktop/inputs.env':
            # Filer reads only its own pins; other package pins move nightly.
            data = b''.join(line for line in data.splitlines(True) if line.startswith(b'NAUTILUS_'))
        materials[name] = [hashlib.sha256(data).hexdigest(), mode]
    document = {'app_id': app_id, 'materials': materials,
                'recipe_sha256': hashlib.sha256(recipe.read_bytes()).hexdigest()}
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def metainfo_version(repo, app_id):
    path = repo / 'packaging/flatpak/apps' / app_id / f'{app_id}.metainfo.xml'
    if not path.is_file():
        return None
    release = ET.parse(path).getroot().find('releases/release')
    return None if release is None else release.get('version')


def load(repo):
    lock = json.loads((repo / LOCK).read_text())
    if lock.get('schema') != SCHEMA:
        raise ValueError(f'{LOCK}: unsupported schema')
    return lock


def shipping(repo):
    return json.loads((repo / SHIPPING).read_text())['applications']


def check(repo, today=None):
    today = today or datetime.date.today()
    lock = load(repo)
    sealer = _sealer(repo)
    failures = []
    apps = lock.get('applications', {})
    wanted = set(shipping(repo))
    for app_id in sorted(wanted - apps.keys()):
        failures.append(f'{app_id}: in {SHIPPING} but not pinned in {LOCK}')
    for app_id in sorted(apps.keys() - wanted):
        failures.append(f'{app_id}: pinned in {LOCK} but not shipped by {SHIPPING}')
    for arch in ARCHES:
        if not re.fullmatch('[0-9a-f]{64}', lock.get('baselines', {}).get(arch, {}).get('manifest_sha256', '')):
            failures.append(f'{arch}: no pinned baseline manifest')
    for app_id in sorted(wanted & apps.keys()):
        row = apps[app_id]
        if set(row.get('commits', {})) != set(ARCHES):
            failures.append(f'{app_id}: needs a pinned commit for {" and ".join(ARCHES)}')
        if row.get('source') == 'external':
            if (repo / 'packaging/flatpak/apps' / app_id).exists():
                failures.append(f'{app_id}: has a recipe here now; pin its source fingerprint')
            continue
        current = fingerprint(repo, app_id, sealer)
        if current is None:
            failures.append(f'{app_id}: no recipe in packaging/flatpak/apps; mark it "source": "external"')
            continue
        version = metainfo_version(repo, app_id)
        if row.get('source_sha256') == current:
            if row.get('version') != version:
                failures.append(f'{app_id}: baseline bundle is {row.get("version")}, metainfo says {version}')
            continue
        hold = row.get('pending')
        if hold:
            try:
                expires = datetime.date.fromisoformat(hold.get('expires', ''))
            except ValueError:
                expires = None
            if expires and today < expires and hold.get('reason'):
                print(f'PENDING {app_id}: {hold["reason"]} (until {expires})')
                continue
        failures.append(
            f'{app_id}: source changed since its baseline bundle was built '
            f'(rebuild it, bump its metainfo release, reseal the baselines and run '
            f'scripts/depot/app-baseline-lock.py write)')
    for line in failures:
        print('FAIL ' + line)
    if not failures:
        print(f'PASS {len(apps)} baseline applications match their source')
    return not failures


def _manifest(directory, arch):
    path = Path(directory) / 'manifest.json'
    data = path.read_bytes()
    document = json.loads(data)
    if document.get('architecture') != arch:
        raise ValueError(f'{path}: baseline is {document.get("architecture")}, not {arch}')
    commits = {}
    for row in document['bundles']:
        kind, name, row_arch, _branch = row['ref'].split('/')
        if kind == 'app' and row_arch == arch:
            commits[name] = row['commit']
    return hashlib.sha256(data).hexdigest(), commits


def write(repo, baselines, rebuilt):
    lock = load(repo)
    sealer = _sealer(repo)
    apps = lock.setdefault('applications', {})
    for arch, directory in baselines.items():
        digest, commits = _manifest(directory, arch)
        lock['baselines'][arch] = {'manifest_sha256': digest}
        for app_id in shipping(repo):
            row = apps.setdefault(app_id, {'commits': {}})
            old = row.get('commits', {}).get(arch)
            new = commits.get(app_id)
            if new is None:
                raise ValueError(f'{app_id}: missing from the {arch} baseline')
            if old is not None and old != new and app_id not in rebuilt:
                raise ValueError(f'{app_id}: {arch} commit changed; name it with --rebuilt')
            row.setdefault('commits', {})[arch] = new
    for app_id in rebuilt:
        row = apps[app_id]
        row['source_sha256'] = fingerprint(repo, app_id, sealer)
        row['version'] = metainfo_version(repo, app_id)
        row.pop('pending', None)
    lock['applications'] = dict(sorted(apps.items()))
    (repo / LOCK).write_text(json.dumps(lock, indent=2) + '\n')


def verify_baseline(repo, arch, directory):
    digest, _ = _manifest(directory, arch)
    pinned = load(repo)['baselines'][arch]['manifest_sha256']
    if digest != pinned:
        print(f'FAIL {arch} app baseline {digest} is not the pinned {pinned} ({LOCK})')
        return False
    print(f'PASS {arch} app baseline {digest}')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', type=Path, default=REPO)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    fp = sub.add_parser('fingerprint')
    fp.add_argument('apps', nargs='*')
    wr = sub.add_parser('write')
    wr.add_argument('--baseline', action='append', required=True, metavar='ARCH=DIR')
    wr.add_argument('--rebuilt', action='append', default=[], metavar='APP_ID')
    vb = sub.add_parser('verify-baseline')
    vb.add_argument('--arch', choices=ARCHES, required=True)
    vb.add_argument('directory', type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.command == 'check':
        return 0 if check(repo) else 1
    if args.command == 'fingerprint':
        sealer = _sealer(repo)
        for app_id in args.apps or shipping(repo):
            print(app_id, fingerprint(repo, app_id, sealer) or 'external')
        return 0
    if args.command == 'write':
        baselines = dict(item.split('=', 1) for item in args.baseline)
        if not set(baselines) <= set(ARCHES):
            parser.error('--baseline takes aarch64=DIR or x86_64=DIR')
        write(repo, baselines, args.rebuilt)
        return 0
    return 0 if verify_baseline(repo, args.arch, args.directory) else 1


if __name__ == '__main__':
    sys.exit(main())
