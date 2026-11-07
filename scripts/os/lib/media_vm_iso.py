#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Give libvirt a disposable inode, never the release medium itself.

The fixture is a same-filesystem reflink (FICLONE) when the working root
supports one. Where it does not (an ext4 loop image), prepare exits 3 without
creating anything; with --copy-budget-gib it instead writes a budgeted copy:
the ISO must fit the budget, the fixture's filesystem must keep the reserve
free afterwards, and the copy's sha256 (re-read from the fixture) must equal
the sha256 of the bytes read from the release ISO and its .sha256 record.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess

FICLONE = 0x40049409
GIB = 1 << 30
CHUNK = 8 << 20
# FICLONE errors that mean "no reflink between these two files", not a fault.
NO_REFLINK = {errno.EOPNOTSUPP, errno.ENOTSUP, errno.EXDEV, errno.ENOTTY, errno.EINVAL, errno.ENOSYS}
NO_REFLINK_EXIT = 3


class NoReflink(OSError):
    """The fixture's filesystem cannot reflink the ISO and no copy was allowed."""


def size_text(size):
    return f'{size / GIB:.1f} GiB' if size >= GIB else f'{size / (1 << 20):.0f} MiB'


def identity(st):
    return {key: getattr(st, 'st_' + key) for key in
            ('dev', 'ino', 'size', 'mtime_ns', 'ctime_ns', 'uid', 'gid', 'mode')}


def clone(source, destination):
    """Kernel-atomic file clone only; an unbudgeted bulk-copy fallback is forbidden."""
    source, destination = Path(source), Path(destination)
    with source.open('rb') as src:
        before = os.fstat(src.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('ISO source is not a regular file')
        # Exclusive creation also prevents touching an earlier attempt's inode.
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.ioctl(fd, FICLONE, src.fileno())
            os.fsync(fd)
            after = os.fstat(src.fileno())
            target = os.fstat(fd)
            if identity(before) != identity(after) or identity(before) != identity(source.stat()):
                raise ValueError('ISO source changed during reflink preflight')
            if (before.st_dev, before.st_ino) == (target.st_dev, target.st_ino) or before.st_size != target.st_size:
                raise ValueError('ISO fixture is not an independent complete clone')
        except BaseException:
            os.close(fd)
            destination.unlink()
            raise
        os.close(fd)
    return {'schema': 1, 'method': 'FICLONE', 'source': str(source.resolve()),
            'source_identity': identity(before), 'fixture': str(destination.resolve()),
            'fixture_device': target.st_dev, 'fixture_inode': target.st_ino,
            'body_sha256_verified': False}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def copy(source, destination, budget, reserve):
    """A budgeted copy, verified by sha256; used only where FICLONE is unsupported."""
    source, destination = Path(source), Path(destination)
    with source.open('rb') as src:
        before = os.fstat(src.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('ISO source is not a regular file')
        if before.st_size > budget:
            raise ValueError(f'the ISO is {size_text(before.st_size)}, over the {size_text(budget)} copy budget; '
                             'raise LUMA_OS_MEDIA_COPY_BUDGET_GIB or put the fixture directory on a '
                             'reflink filesystem (XFS, btrfs)')
        volume = os.statvfs(destination.parent)
        free = volume.f_bavail * volume.f_frsize
        if free < before.st_size + reserve:
            raise ValueError(f'a verified copy needs {size_text(before.st_size + reserve)} free in '
                             f'{destination.parent} (ISO + {size_text(reserve)} reserve), {size_text(free)} is '
                             'available; free space there or put it on a reflink filesystem (XFS, btrfs)')
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            digest = hashlib.sha256()
            while chunk := src.read(CHUNK):
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(fd, view):]
            os.fsync(fd)
            after = os.fstat(src.fileno())
            target = os.fstat(fd)
            if identity(before) != identity(after) or identity(before) != identity(source.stat()):
                raise ValueError('the release ISO changed while it was copied; rerun once nothing writes it')
            if (before.st_dev, before.st_ino) == (target.st_dev, target.st_ino) or before.st_size != target.st_size:
                raise ValueError('ISO fixture is not an independent complete copy')
            body = digest.hexdigest()
            if sha256_file(destination) != body:
                raise ValueError('the ISO fixture does not read back as the release ISO; check the fixture disk')
            record = Path(f'{source}.sha256')
            recorded = record.read_text().split()[0] if record.is_file() else None
            if recorded is not None and recorded != body:
                raise ValueError(f'the release ISO does not match {record.name}; it is not the medium that was built')
        except BaseException:
            os.close(fd)
            destination.unlink()
            raise
        os.close(fd)
    return {'schema': 1, 'method': 'copy', 'source': str(source.resolve()),
            'source_identity': identity(before), 'fixture': str(destination.resolve()),
            'fixture_device': target.st_dev, 'fixture_inode': target.st_ino,
            'body_sha256': body, 'body_sha256_verified': True,
            'release_sha256_record': recorded is not None,
            'copy_budget': budget, 'copy_reserve': reserve}


def fixture(source, destination, copy_budget=0, reserve=2 * GIB):
    """A reflink of SOURCE, or (with a copy budget) a verified copy where reflinks are unsupported."""
    try:
        return clone(source, destination)
    except OSError as error:
        if error.errno not in NO_REFLINK:
            raise
        if copy_budget <= 0:
            raise NoReflink(error.errno, f'{destination.parent} cannot reflink {source} ({error.strerror})') from error
    return copy(source, destination, copy_budget, reserve)


def prepare(source, destination, proof, reader, copy_budget=0, reserve=2 * GIB):
    account = pwd.getpwnam(reader)
    record = fixture(Path(source), Path(destination), copy_budget, reserve)
    try:
        os.chown(destination, account.pw_uid, account.pw_gid)
        os.chmod(destination, 0o400)
        # Test the complete path as the real reader, before allocating or starting a VM.
        try:
            subprocess.run(['runuser', '-u', reader, '--', 'test', '-r', str(destination)],
                           check=True, timeout=10)
        except subprocess.CalledProcessError as error:
            raise ValueError(f'{reader} cannot read {destination}; make each directory above it '
                             f'searchable by {reader} (no change was made to them)') from error
        with open(proof, 'x') as output:
            os.chmod(proof, 0o400)
            json.dump(record, output, sort_keys=True, indent=2)
            output.write('\n')
    except BaseException:
        Path(destination).unlink()
        raise
    return record


def verify(proof):
    record = json.loads(Path(proof).read_text())
    if record.get('schema') != 1 or record.get('method') not in ('FICLONE', 'copy'):
        raise ValueError('invalid ISO fixture custody record')
    if identity(Path(record['source']).stat()) != record['source_identity']:
        raise ValueError('release ISO identity, ownership, mode or timestamps changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('prepare')
    new.add_argument('source', type=Path)
    new.add_argument('destination', type=Path)
    new.add_argument('proof', type=Path)
    new.add_argument('--reader', default='qemu')
    new.add_argument('--copy-budget-gib', type=float, default=0,
                     help='without a reflink, copy an ISO up to this size (0: exit 3 instead)')
    new.add_argument('--reserve-gib', type=float, default=2,
                     help='free space the copy must leave on the fixture filesystem')
    check = sub.add_parser('verify')
    check.add_argument('proof', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.source, args.destination, args.proof, args.reader,
                    int(args.copy_budget_gib * GIB), int(args.reserve_gib * GIB))
        else:
            verify(args.proof)
    except NoReflink as error:
        parser.exit(NO_REFLINK_EXIT, f'ISO fixture preflight: {error.strerror}. Rerun with --copy-budget-gib N '
                    'for a verified copy, or put the fixture directory on a reflink filesystem (XFS, btrfs). '
                    'Nothing was created.\n')
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        if args.command == 'verify':
            parser.exit(1, f'ISO fixture custody check failed: {error}\n')
        parser.exit(1, f'ISO fixture preflight failed: {error}. The release ISO and its directories were '
                    'not changed and no fixture was left behind.\n')


if __name__ == '__main__':
    main()
