#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Give libvirt a disposable CoW inode, never the release medium itself."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess

FICLONE = 0x40049409


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


def prepare(source, destination, proof, reader):
    account = pwd.getpwnam(reader)
    record = clone(source, destination)
    try:
        os.chown(destination, account.pw_uid, account.pw_gid)
        os.chmod(destination, 0o400)
        # Test the complete path as the real reader, before allocating or starting a VM.
        subprocess.run(['runuser', '-u', reader, '--', 'test', '-r', str(destination)],
                       check=True, timeout=10)
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
    if record.get('schema') != 1 or record.get('method') != 'FICLONE':
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
    check = sub.add_parser('verify')
    check.add_argument('proof', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.source, args.destination, args.proof, args.reader)
        else:
            verify(args.proof)
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        parser.exit(1, f'ISO fixture preflight failed: {error}. A same-filesystem reflink and reader traversal are required; no bulk copy or parent permission changes were attempted.\n')


if __name__ == '__main__':
    main()
