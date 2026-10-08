# SPDX-License-Identifier: Apache-2.0
"""Phone's explicit shared user call history and local blocking policy."""
import os
from pathlib import Path
import re

FAMILIES = frozenset({'prairie/phone', 'luma/phone'})

def directory(family, environment=None, *, check_mounts=True):
    if family not in FAMILIES:
        raise ValueError('Unsupported Phone datastore.')
    env = dict(os.environ if environment is None else environment)
    home = Path(env.get('HOME') or str(Path.home()))
    if env.get('FLATPAK_ID') != 'org.projectluma.Phone':
        return Path(env.get('XDG_DATA_HOME') or home/'.local/share')/family
    base = Path(env.get('HOST_XDG_DATA_HOME') or home/'.local/share')
    target = base/family
    if check_mounts:
        mounted = set()
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            if len(fields) >= 5:
                mounted.add(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
        if str(target) not in mounted:
            raise PermissionError('Phone requires its shared call-history and policy folders.')
    return target
