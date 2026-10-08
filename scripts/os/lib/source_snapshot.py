#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bind private dirty OS candidates to their actual frozen source and checks.

This admits no public release. Clean releases retain their Git archive path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat

SHA = re.compile(r'[0-9a-f]{64}')
REV = re.compile(r'[0-9a-f]{40}')
REQUIRED = ('tests/os/image-contract.sh', 'tests/smoke/desktop.sh',
            'config/desktop/inputs.env', 'config/desktop/packages.txt',
            'config/os/release.env', 'config/boot/apply-grub-policy.sh')


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate manifest key: {key!r}')
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def safe_name(name):
    if not isinstance(name, str) or not name or '\0' in name:
        raise ValueError('invalid manifest path')
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ('', '.', '..', '.git') for part in p.parts) or str(p) != name:
        raise ValueError(f'unsafe manifest path: {name!r}')
    return p


def observed(root):
    result = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        if Path(current) == root and '.git' in dirs:
            dirs.remove('.git')
        for name in list(dirs):
            if (Path(current) / name).is_symlink():
                files.append(name)
                dirs.remove(name)
        for name in files:
            path = Path(current) / name
            rel = str(path.relative_to(root))
            safe_name(rel)
            s = path.lstat()
            if stat.S_ISREG(s.st_mode):
                row = {'type': 'file', 'mode': stat.S_IMODE(s.st_mode),
                       'size': s.st_size, 'sha256': digest(path)}
            elif stat.S_ISLNK(s.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve(strict=False).is_relative_to(Path(root).resolve()):
                    raise ValueError(f'source symlink escapes snapshot: {rel!r}')
                row = {'type': 'symlink', 'mode': stat.S_IMODE(s.st_mode),
                       'size': s.st_size, 'target': os.readlink(path)}
            else:
                raise ValueError(f'non-source filesystem object: {rel!r}')
            result[rel] = row
    return result


def trusted_path(path, *, allow_leaf_symlink=False, regular=False):
    path = Path(path).absolute()
    for index, current in enumerate((path, *path.parents)):
        entry = current.lstat()
        is_link = stat.S_ISLNK(entry.st_mode)
        if is_link and not (index == 0 and allow_leaf_symlink):
            raise ValueError(f'private build path has a symlink ancestor/metadata: {current}')
        if index > 0 and not stat.S_ISDIR(entry.st_mode):
            raise ValueError(f'private build parent is not a directory: {current}')
        if index == 0 and regular and not stat.S_ISREG(entry.st_mode):
            raise ValueError(f'private build metadata is not a regular file: {current}')
        sticky_parent = index > 0 and stat.S_ISDIR(entry.st_mode) and bool(entry.st_mode & stat.S_ISVTX)
        if entry.st_uid != 0 or (not is_link and stat.S_IMODE(entry.st_mode) & 0o022 and not sticky_parent):
            raise ValueError(f'private build input is not root-owned and protected: {current}')


def trusted_tree(root):
    trusted_path(root)
    for current, dirs, files in os.walk(root, followlinks=False):
        if Path(current) == Path(root) and '.git' in dirs:
            dirs.remove('.git')
        for name in dirs + files:
            entry = Path(current) / name
            trusted_path(entry, allow_leaf_symlink=entry.is_symlink())


def validate(root, manifest, revision, expected_sha=None):
    trusted_path(root)
    root = Path(root).resolve(strict=True)
    trusted_tree(root)
    trusted_path(manifest, regular=True)
    sha = digest(manifest)
    if expected_sha is not None and (not SHA.fullmatch(expected_sha) or sha != expected_sha):
        raise ValueError('full source snapshot digest changed')
    data = load(manifest)
    if data.get('mode') != 'private-dirty-candidate' or not REV.fullmatch(revision) or data.get('base_revision') != revision:
        raise ValueError('snapshot is not this private dirty candidate')
    files = data.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('empty source snapshot')
    for name in files:
        safe_name(name)
    actual = observed(root)
    if actual != files:
        changed = sorted(name for name in files.keys() | actual.keys() if files.get(name) != actual.get(name))
        raise ValueError(f'full source snapshot differs: {changed[:8]!r}')
    return data, sha


def selected(name):
    return name in REQUIRED or name.startswith('tests/os/gate/')


def capture(root, manifest, revision, output):
    data, sha = validate(root, manifest, revision)
    names = {name: row for name, row in data['files'].items() if selected(name)}
    if any(name not in names or names[name]['type'] != 'file' for name in REQUIRED):
        raise ValueError('required release checks/pins are missing or not regular files')
    if not any(name.startswith('tests/os/gate/') and name.endswith('.sh') and row['type'] == 'file' for name, row in names.items()):
        raise ValueError('no release gate scripts captured')
    output = Path(output)
    trusted_path(output.parent)
    output.mkdir(mode=0o755)  # Never replace a previous admission.
    (output / 'tree').mkdir(mode=0o755)
    shutil.copyfile(manifest, output / 'SOURCE-SNAPSHOT.json')
    for name, row in names.items():
        src = Path(root) / name
        dst = output / 'tree' / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if row['type'] != 'file':
            raise ValueError('release-check subset must contain regular files only')
        shutil.copyfile(src, dst)
        dst.chmod(row['mode'])
    payload = {'schema': 'org.projectluma.private-os-release-checks/v1',
               'private': True, 'base_revision': revision,
               'source_snapshot_sha256': sha, 'files': names}
    (output / 'RELEASE-CHECKS.json').write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')
    # Revalidate complete inputs after copying, and the actual copied subset.
    validate(root, manifest, revision, sha)
    if observed(output / 'tree') != names or digest(output / 'SOURCE-SNAPSHOT.json') != sha:
        raise ValueError('captured release checks changed during admission')
    return {'source_snapshot_sha256': sha,
            'release_checks_sha256': digest(output / 'RELEASE-CHECKS.json')}


def admit(bundle, revision, snapshot_sha, checks_sha, provenance):
    bundle = Path(bundle)
    trusted_tree(bundle)
    trusted_path(provenance, regular=True)
    trusted_path(bundle / "SOURCE-SNAPSHOT.json", regular=True)
    trusted_path(bundle / "RELEASE-CHECKS.json", regular=True)
    if not SHA.fullmatch(snapshot_sha) or not SHA.fullmatch(checks_sha):
        raise ValueError('missing private source binding')
    if digest(bundle / 'SOURCE-SNAPSHOT.json') != snapshot_sha or digest(bundle / 'RELEASE-CHECKS.json') != checks_sha:
        raise ValueError('retained source/check binding changed')
    checks = load(bundle / 'RELEASE-CHECKS.json')
    if checks.get('schema') != 'org.projectluma.private-os-release-checks/v1' or checks.get('private') is not True or checks.get('base_revision') != revision or checks.get('source_snapshot_sha256') != snapshot_sha:
        raise ValueError('wrong private release-check identity')
    full = load(bundle / 'SOURCE-SNAPSHOT.json')
    if full.get('mode') != 'private-dirty-candidate' or full.get('base_revision') != revision:
        raise ValueError('retained full snapshot is not this private candidate')
    for name in full['files']:
        safe_name(name)
    expected = {name: row for name, row in full['files'].items() if selected(name)}
    if checks['files'] != expected or observed(bundle / 'tree') != expected:
        raise ValueError('captured source checks do not match the full snapshot')
    source = load(provenance)['source']
    if source.get('dirty') is not True or source.get('revision') != revision or source.get('snapshot_sha256') != snapshot_sha or source.get('release_checks_sha256') != checks_sha or source.get('private') is not True:
        raise ValueError('provenance does not bind these private source checks')
    return bundle / 'tree'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for cmd in ('validate', 'capture'):
        s = sub.add_parser(cmd)
        for key in ('root', 'manifest', 'revision'):
            s.add_argument('--' + key, required=True)
        s.add_argument('--expected-sha') if cmd == 'validate' else s.add_argument('--output', required=True)
    s = sub.add_parser('admit')
    for key in ('bundle', 'revision', 'snapshot-sha', 'checks-sha', 'provenance'):
        s.add_argument('--' + key, required=True)
    a = p.parse_args()
    try:
        if a.command == 'validate':
            _, sha = validate(a.root, a.manifest, a.revision, a.expected_sha)
            result = {'source_snapshot_sha256': sha}
        elif a.command == 'capture':
            result = capture(a.root, a.manifest, a.revision, a.output)
        else:
            result = {'release_checks_root': str(admit(a.bundle, a.revision, a.snapshot_sha, a.checks_sha, a.provenance))}
        print(json.dumps(result, sort_keys=True))
    except (OSError, KeyError, TypeError, ValueError) as error:
        p.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()
