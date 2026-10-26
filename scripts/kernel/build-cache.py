#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reuse a completed local kernel build without repeating make's dependency walk.

The producer records this receipt only after configure and compilation succeed.
Boot artifacts and configuration are content-hashed. Module freshness uses the
same size/mtime model as make; this is a task-owned local cache, not an untrusted
binary import. Final packaging still strips/signs and checks every module.
"""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import stat

ARTIFACTS = ('.config', 'arch/arm64/boot/Image',
             'arch/arm64/boot/dts/qcom/x1p42100-microsoft-sp12in.dtb',
             'System.map', 'modules.order', 'include/config/kernel.release')
RECEIPT = '.luma-compiled.json'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(build):
    rows = []
    seen = set()
    checked_parents = set()
    for line in (build/'modules.order').read_text().splitlines():
        relative = PurePosixPath(posixpath.normpath(line))
        if (not re.fullmatch(r'[A-Za-z0-9_+./-]+', line) or relative.is_absolute()
                or '..' in relative.parts
                or relative.suffix not in ('.o', '.ko')):
            raise ValueError('Unsafe module inventory path')
        name = str(relative.with_suffix('.ko'))
        if name in seen:
            raise ValueError('Duplicate module inventory path')
        seen.add(name)
        path = build/name
        if path.parent not in checked_parents:
            if not path.parent.resolve().is_relative_to(build):
                raise ValueError('Module cache path escapes output directory')
            checked_parents.add(path.parent)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Module cache path escapes output directory')
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
            raise ValueError('Module cache is incomplete')
        rows.append({'path': name, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns})
    if not rows:
        raise ValueError('Module cache is empty')
    return rows


def snapshot(repository, build, base):
    lock = repository/'config/kernel/surface-pro12-7.3.json'
    release = json.loads(lock.read_text())['release']
    if (build/'include/config/kernel.release').read_text().strip() != release:
        raise ValueError('Prepared kernel release differs from source lock')
    return {'schema': 'org.projectluma.local-kernel-cache/v1',
            'source_lock_sha256': sha(lock),
            'config_fragment_sha256': sha(repository/'config/kernel/surface-pro12.config'),
            'base_config_sha256': sha(base),
            'artifacts': {name: sha(build/name) for name in ARTIFACTS},
            'modules': inventory(build)}


def record(repository, build, base):
    destination = build/RECEIPT
    if destination.exists():
        raise ValueError('Compiled-build receipt already exists')
    destination.write_text(json.dumps(snapshot(repository, build, base), indent=2)+'\n')


def verify(repository, build, base):
    expected = json.loads((build/RECEIPT).read_text())
    actual = snapshot(repository, build, base)
    if expected != actual:
        raise ValueError('Prepared kernel cache changed or belongs to different inputs')
    print('Verified local compiled kernel cache:', len(actual['modules']), 'modules')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('record', 'verify'))
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--base-config', type=Path, required=True)
    args = parser.parse_args()
    globals()[args.action](args.repository.resolve(), args.build.resolve(), args.base_config.resolve())
