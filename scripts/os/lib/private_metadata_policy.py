#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit private-candidate recovery of inherited build-host ACL metadata.

RPM permissions are authoritative for packaged paths. Unpackaged public data
uses normal read/traverse permissions; private configuration remains private.
This recovery never authorizes public release and never changes file contents.
"""
import json
import os
import stat
from pathlib import Path

DEFAULT = '0200000001000700ffffffff04000700ffffffff10000700ffffffff20000000ffffffff'
ACL_NAMES = {'system.posix_acl_access', 'system.posix_acl_default',
             'trusted.SGI_ACL_FILE', 'trusted.SGI_ACL_DEFAULT'}
PUBLIC_DATA = ('/usr/share/', '/usr/lib/', '/usr/lib64/', '/usr/libexec/', '/usr/bin/', '/usr/etc/dconf/')
PRIVATE = ('/usr/etc/selinux/', '/usr/etc/ssh/', '/usr/etc/NetworkManager/system-connections/')


def load_policy(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Private metadata policy must be a protected root-owned regular file')
    data = json.loads(path.read_text())
    if data.get('schema') != 'org.projectluma.private-rpm-permissions/v1':
        raise ValueError('Unknown private RPM permission policy')
    return data['modes']


def simple_acl(value):
    raw = bytes.fromhex(value)
    if raw[:4] != b'\x02\x00\x00\x00' or (len(raw) - 4) % 8:
        return False
    tags = [int.from_bytes(raw[i:i+2], 'little') for i in range(4, len(raw), 8)]
    return tags == [1, 4, 16, 32]  # No named users or groups.


def normalize(path, record, modes, directory=False):
    attrs = record['xattrs']
    inherited = (attrs.get('system.posix_acl_default') == DEFAULT if directory
                 else simple_acl(attrs.get('system.posix_acl_access', '')))
    if directory and attrs.get('system.posix_acl_access') and not simple_acl(attrs['system.posix_acl_access']):
        inherited = False
    if not inherited:
        return record
    old = record['mode']
    mode = old
    expected = modes.get(path)
    private = path.startswith(PRIVATE) or old & 0o777 == 0o700
    if directory:
        if expected is not None:
            mode = expected
        elif private:
            mode = old & ~0o2000
        elif old in (0o2770, 0o2750, 0o2755):
            mode = 0o755
    elif not old & 0o7000:
        # Restore only missing public read/execute bits, never private files,
        # write permissions, ownership, capabilities or special file modes.
        if expected is not None and old in (0o640, 0o750) and (old, expected) in ((0o640, 0o644), (0o750, 0o755)):
            mode = expected
        elif expected is None and not private and path.startswith(PUBLIC_DATA):
            mode = {0o640: 0o644, 0o750: 0o755}.get(old, old)
    if not directory and mode == old:
        return record
    result = dict(record, mode=mode, xattrs={k:v for k,v in attrs.items() if k not in ACL_NAMES})
    return result
