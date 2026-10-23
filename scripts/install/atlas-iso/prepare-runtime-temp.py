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
from pathlib import Path


def clear_defaults(paths):
    for path in paths:
        path = Path(path)
        before = path.lstat()
        if not stat.S_ISDIR(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o1777:
            raise ValueError(f'builder temporary directory must be a sticky public directory: {path}')
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


if __name__ == '__main__':
    clear_defaults(('/tmp', '/var/tmp'))
    print('Disposable builder temporary directories have no inherited default ACL')
