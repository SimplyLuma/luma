#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create Lorax's FAT EFI image without a host loop mount.

Retain Lorax's filesystem sizing and reproducible volume ID, and verify every
copied boot file through the FAT filesystem before accepting the image.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create(bootdir, output, label):
    from pylorax.imgutils import estimate_size

    bootdir = bootdir.resolve(strict=True)
    files = sorted(path for path in bootdir.rglob('*') if path.is_file())
    if not files or any(path.is_symlink() for path in bootdir.rglob('*')):
        raise ValueError('EFI boot directory must contain regular files, without symlinks')
    if output.exists():
        raise ValueError('refusing to replace an existing EFI image')
    size = estimate_size(None, graft={'EFI/BOOT': str(bootdir)}, fstype='msdos')
    with output.open('xb') as image:
        image.truncate(size)
    mkfs = ['mkfs.msdos', '--invariant', '-n', label]
    if 'SOURCE_DATE_EPOCH' in os.environ:
        epoch = int(os.environ['SOURCE_DATE_EPOCH'])
        if not 0 <= epoch <= 0xffffffff:
            raise ValueError('SOURCE_DATE_EPOCH does not fit the FAT volume ID')
        mkfs += ['-i', f'{epoch:x}']
    subprocess.run(mkfs + [str(output)], check=True)
    prefix = ['-i', str(output)]
    subprocess.run(['mmd', *prefix, '::/EFI', '::/EFI/BOOT'], check=True)
    directories = sorted((path for path in bootdir.rglob('*') if path.is_dir()),
                         key=lambda path: len(path.parts))
    for path in directories:
        subprocess.run(['mmd', *prefix, '::/EFI/BOOT/' + path.relative_to(bootdir).as_posix()], check=True)
    for path in files:
        target = '::/EFI/BOOT/' + path.relative_to(bootdir).as_posix()
        subprocess.run(['mcopy', '-m', *prefix, str(path), target], check=True)
    with tempfile.TemporaryDirectory(prefix='efi-readback-', dir=output.parent) as temporary:
        for index, path in enumerate(files):
            target = '::/EFI/BOOT/' + path.relative_to(bootdir).as_posix()
            copy = Path(temporary) / str(index)
            subprocess.run(['mcopy', *prefix, target, str(copy)], check=True)
            if digest(path) != digest(copy):
                raise ValueError(f'EFI image readback differs: {target}')
    subprocess.run(['fsck.fat', '-n', str(output)], check=True)
    with output.open('rb') as image:
        os.fsync(image.fileno())
    print(f'EFI FAT image verified: {len(files)} files, {size} bytes')


def replace_image(bootdir, output, label):
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError('replacement destination must be a regular file')
    with tempfile.TemporaryDirectory(prefix='efi-replace-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'efiboot.img'
        create(bootdir, staged, label)
        os.replace(staged, output)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='EFI')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--replace', action='store_true', help='replace an existing image atomically after verification')
    parser.add_argument('bootdir', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.replace:
        replace_image(args.bootdir, args.output, args.label)
    else:
        create(args.bootdir, args.output, args.label)
