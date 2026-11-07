#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Create private staging metadata before OSTree imports any image contents.

A shared parent's setgid/default ACL must not supply ownership for the importer's
working directories. Existing caches without this creation receipt are refused;
quarantine them explicitly and create a fresh repository instead of rewriting
cached objects or their metadata. With --quarantine-refused, a refused cache is
renamed aside unread (`<name>.quarantine-<UTC>`) and a fresh one is created: the
receipt binds the cache's device and inode, so moving the working root or a loop
device renumbered by a reboot would otherwise stop every export.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time

MARKER = '.luma-private-import.json'
SCHEMA = 'org.projectluma.private-import-directory/v1'
ACL_NAMES = ('system.posix_acl_access', 'system.posix_acl_default')


def check_parent(path):
    if os.getuid() or not path.is_absolute() or path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError('Root and an existing nonsymlink import parent are required')


def ensure_private(path):
    path = Path(path)
    check_parent(path)
    fresh = False
    try:
        path.mkdir(mode=0o700)
        fresh = True
    except FileExistsError:
        pass
    if fresh:
        # Only this new empty leaf is changed; never normalize a cached tree.
        for name in ACL_NAMES:
            if name in os.listxattr(path):
                os.removexattr(path, name)
        os.chown(path, 0, 0)
        os.chmod(path, 0o700)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid or info.st_gid or
            stat.S_IMODE(info.st_mode) != 0o700 or any(name in os.listxattr(path) for name in ACL_NAMES)):
        raise ValueError('Unsafe existing OSTree import cache; quarantine it and use a fresh private path')
    receipt = {'schema': SCHEMA, 'path': str(path), 'device': info.st_dev, 'inode': info.st_ino,
               'uid': 0, 'gid': 0, 'mode': '0700', 'no_inherited_acl': True}
    marker = path / MARKER
    body = (json.dumps(receipt, sort_keys=True) + '\n').encode()
    if fresh:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o400)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(path, os.O_DIRECTORY)
        os.fsync(fd)
        os.close(fd)
    else:
        try:
            marker_info = marker.lstat()
        except FileNotFoundError as error:
            raise ValueError('Unproven existing OSTree import cache; quarantine it before retrying') from error
        if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_uid or marker_info.st_gid or
                stat.S_IMODE(marker_info.st_mode) != 0o400 or marker.read_bytes() != body):
            raise ValueError('Existing private import creation receipt differs; refuse cached metadata')
    return receipt


def admit(path, quarantine_refused=False):
    path = Path(path)
    check_parent(path)
    try:
        return ensure_private(path)
    except ValueError:
        if not quarantine_refused or not (path.exists() or path.is_symlink()):
            raise
    # Never read or reuse the refused cache: move it aside whole and start fresh.
    aside = path.with_name(f"{path.name}.quarantine-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    path.rename(aside)
    print(f'quarantined refused import cache: {aside}', file=sys.stderr)
    return ensure_private(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quarantine-refused', action='store_true')
    parser.add_argument('path')
    args = parser.parse_args()
    print(json.dumps(admit(Path(args.path), args.quarantine_refused), sort_keys=True))


if __name__ == '__main__':
    main()
