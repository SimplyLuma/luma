# SPDX-License-Identifier: Apache-2.0
"""Keep host sync from creating an unmanaged independent-app profile."""
import json
import os
from pathlib import Path
import stat


def profile_ready(app_id, environment=None):
    env = dict(os.environ if environment is None else environment)
    if env.get('FLATPAK_ID') == app_id:
        return True  # The maintained sandbox startup owns Prepare first.
    from .app_installs import flatpak_data_home, resolve
    if resolve(app_id, env).data_home != flatpak_data_home(app_id, env):
        return True  # Native installs retain their existing owner and path.
    home = Path(env.get('HOME') or str(Path.home()))
    receipt = home / '.local/state/luma/app-migration' / (app_id+'.ready.json')
    try:
        info = receipt.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode&0o022 or info.st_size>65536:
            return False
        for parent in receipt.parents:
            if parent == home: break
            if parent.is_symlink(): return False
        descriptor = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            current = os.fstat(stream.fileno())
            if ((current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
                    or current.st_uid != os.getuid() or current.st_mode & 0o022
                    or current.st_size > 65536):
                return False
            data = json.loads(stream.read(65537))
        return (isinstance(data, dict)
                and data.get('schema') == 'org.projectluma.app-data-ready/v1'
                and data.get('app_id') == app_id and data.get('result') == 'PASS'
                and isinstance(data.get('receipt_sha256'), str)
                and len(data['receipt_sha256']) == 64
                and all(c in '0123456789abcdef' for c in data['receipt_sha256']))
    except (OSError, ValueError):
        return False
