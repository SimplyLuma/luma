#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Admit Viola's byte-identical released engine for its independent GTK UI.

Source0 is the same fixed architecture-specific archive admitted by the canonical RPM,
including upstream license material. It is never fetched by this helper.
"""
import argparse
import hashlib
import platform
from pathlib import Path
import tarfile

ENGINE_INPUTS = {
    'x86_64': ('viola-native-engine-0.2.10-native30.tar.zst',
               '2760bcadbb3864ffb637888b06e9717642c1add31656c2b81c6d0fb7fd5b1028', 30),
    'aarch64': ('viola-native-engine-0.2.10-native31.tar.zst',
                '325f3ca8be682bbe6c267251a29315f01229a52971b4627c972dbe0a32552f30', 31),
}


def admitted_engine(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size < 512 * 1024**2:
        raise ValueError('Viola requires the regular bounded admitted engine archive')
    with path.open('rb') as stream:
        data = stream.read(512 * 1024**2 + 1)
    digest = hashlib.sha256(data).hexdigest()
    matches = [row for row in ENGINE_INPUTS.values() if row[1] == digest]
    if len(data) > 512 * 1024**2 or len(matches) != 1:
        raise ValueError('Viola engine archive differs from the admitted native release')
    return [('upstream-viola/' + matches[0][0], data, 0o644)]


def prepare(root):
    root = root.resolve()
    architecture = platform.machine()
    if architecture not in ENGINE_INPUTS:
        raise ValueError('Unsupported native Viola architecture')
    engine_name, _, engine_release = ENGINE_INPUTS[architecture]
    source = root / 'upstream-viola' / engine_name
    admitted_engine(source)
    target = root / 'viola-engine'
    if target.exists():
        raise ValueError('Viola engine preparation already exists')
    with tarfile.open(source) as archive:
        members = archive.getmembers()
        if not members or len(members) > 4096 or any(
                not Path(member.name).parts or Path(member.name).parts[0] not in {'engine', 'system'}
                for member in members):
            raise ValueError('Unexpected admitted engine archive membership')
        target.mkdir(mode=0o755)
        archive.extractall(target, members=members, filter='data')
    manifest = root / f'src/external/viola/chromium-linux/luma-package/native-{engine_release}-engine.sha256'
    wanted = {}
    for line in manifest.read_text().splitlines():
        digest, name = line.split('  ', 1)
        if len(digest) != 64 or Path(name).is_absolute() or '..' in Path(name).parts or name in wanted:
            raise ValueError('Invalid maintained engine manifest')
        wanted[name] = digest
    files = {str(p.relative_to(target/'engine')):p for p in (target/'engine').rglob('*') if not p.is_dir()}
    if set(files) != set(wanted):
        raise ValueError('Admitted engine member set differs from the canonical native manifest')
    for name, path in files.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe admitted engine member')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != wanted[name]:
                raise ValueError('Admitted engine file differs: ' + name)
    print('Viola admitted native engine preparation PASS:', len(files), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    prepare(parser.parse_args().root)
