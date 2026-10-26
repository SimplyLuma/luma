#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare an immutable, hash-pinned device kernel source without firmware.

Archives are downloaded once into CACHE; OUTPUT must not exist. The caller
supplies a reviewed source lock, not moving branches or arbitrary patch URLs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import urllib.request


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(value: str) -> str:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or str(p) != value:
        raise ValueError(f'Unsafe relative path: {value!r}')
    return value


def validate(lock: dict) -> None:
    if lock.get('schema') != 'org.projectluma.kernel-source/v1':
        raise ValueError('Unknown kernel source schema')
    if set(lock['sources']) != {'linux', 'overlay'}:
        raise ValueError('Kernel source lock requires linux and overlay archives')
    for row in [*lock['sources'].values(), *lock['patches']]:
        if len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256']):
            raise ValueError('Invalid source SHA-256')
    for row in lock['sources'].values():
        if not row['url'].startswith('https://'):
            raise ValueError('Kernel source downloads require HTTPS')
        if '/' in relative(row['name']):
            raise ValueError('Archive name must be a filename')
    seen = set()
    for row in lock['patches']:
        if row['source'] not in ('repository', 'overlay'):
            raise ValueError('Unknown patch source')
        key = (row['source'], relative(row['path']))
        if key in seen:
            raise ValueError('Duplicate patch')
        seen.add(key)


def fetch(row: dict, cache: Path, offline: bool) -> Path:
    path = cache / row['name']
    if not path.exists():
        if offline:
            raise ValueError(f'Missing cached archive: {path.name}')
        partial = path.with_suffix(path.suffix + '.part')
        with urllib.request.urlopen(row['url'], timeout=120) as remote, partial.open('xb') as stream:
            while chunk := remote.read(1024 * 1024):
                stream.write(chunk)
        if digest(partial) != row['sha256']:
            partial.unlink()
            raise ValueError(f'Download hash mismatch: {path.name}')
        partial.rename(path)
    if path.is_symlink() or digest(path) != row['sha256']:
        raise ValueError(f'Cached source hash mismatch: {path.name}')
    return path


def extract(archive: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter='data')
    entries = list(destination.iterdir())
    if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
        raise ValueError('Source archive must have exactly one root directory')
    return entries[0]


def prepare(lock_path: Path, cache: Path, output: Path, repository: Path, offline: bool) -> None:
    lock = json.loads(lock_path.read_text())
    validate(lock)
    if output.exists():
        raise ValueError('Output already exists; never patch an existing source tree')
    cache.mkdir(parents=True, exist_ok=True)
    archives = {name: fetch(row, cache, offline) for name, row in lock['sources'].items()}
    output.mkdir(parents=True)
    trees = {name: extract(path, output / name) for name, path in archives.items()}
    for row in lock['patches']:
        base = repository if row['source'] == 'repository' else trees['overlay']
        patch = (base / row['path']).resolve()
        if not patch.is_relative_to(base.resolve()) or not patch.is_file() or digest(patch) != row['sha256']:
            raise ValueError(f'Patch hash/path mismatch: {row["path"]}')
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=trees['linux'], check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=trees['linux'], check=True)
    receipt = {'schema': 'org.projectluma.prepared-kernel/v1',
               'lock_sha256': digest(lock_path), 'release': lock['release'],
               'linux': str(trees['linux']), 'patch_count': len(lock['patches'])}
    (output / 'PREPARED.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(trees['linux'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    prepare(args.lock.resolve(), args.cache.resolve(), args.output.absolute(), repository, args.offline)


if __name__ == '__main__':
    main()
