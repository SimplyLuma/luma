#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Clear inherited default ACLs only in a disposable builder's temp folders.

Lorax binds its container /var/tmp into the runtime while running dracut.
A default ACL can mask archive permissions even when the process umask is 022.
Keep existing access ACLs, ownership, sticky bits and cached tool layers unchanged.
"""
import errno
import os
import stat
import sys
import tempfile
from pathlib import Path


def clear_default_acl(path):
    before = path.lstat()
    access_acl = {name: os.getxattr(path, name) for name in os.listxattr(path)
                  if name == 'system.posix_acl_access'}
    try:
        os.removexattr(path, 'system.posix_acl_default')
    except OSError as error:
        if error.errno not in (errno.ENODATA, errno.ENOTSUP):
            raise
    after = path.lstat()
    if (before.st_mode, before.st_uid, before.st_gid) != (after.st_mode, after.st_uid, after.st_gid):
        raise ValueError(f'default ACL cleanup changed temporary directory access: {path}')
    if access_acl != {name: os.getxattr(path, name) for name in os.listxattr(path)
                      if name == 'system.posix_acl_access'}:
        raise ValueError(f'default ACL cleanup changed the access ACL: {path}')
    if 'system.posix_acl_default' in os.listxattr(path):
        raise ValueError(f'builder temporary directory still inherits an ACL: {path}')


def clear_defaults(paths):
    for path in paths:
        path = Path(path)
        before = path.lstat()
        if not stat.S_ISDIR(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o1777:
            raise ValueError(f'builder temporary directory must be a sticky public directory: {path}')
        clear_default_acl(path)


def create_scratch(parent):
    """Create a clean private Lorax parent without changing the shared work tree."""
    parent = Path(parent)
    if not stat.S_ISDIR(parent.lstat().st_mode):
        raise ValueError(f'Lorax scratch parent must be a directory, not a symlink: {parent}')
    child = Path(tempfile.mkdtemp(prefix='luma-lorax-', dir=parent))
    try:
        if child.lstat().st_uid != os.geteuid():
            raise ValueError('new Lorax scratch is not owned by its builder')
        # Only this newly created directory is initialized. Shared parents can
        # carry SGID and named ACL entries: do not propagate either to the
        # runtime root that Lorax creates below it.
        child.chmod(0o700)
        clear_default_acl(child)
        if stat.S_IMODE(child.lstat().st_mode) != 0o700:
            raise ValueError('new Lorax scratch must be private and have no SGID')
        return child
    except Exception:
        child.rmdir()
        raise


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--scratch-parent':
        print(create_scratch(sys.argv[2]))
    elif len(sys.argv) == 1:
        clear_defaults(('/tmp', '/var/tmp'))
        print('Disposable builder temporary directories have no inherited default ACL')
    else:
        raise SystemExit('usage: prepare-runtime-temp.py [--scratch-parent DIRECTORY]')
