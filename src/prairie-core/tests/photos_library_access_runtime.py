# SPDX-License-Identifier: Apache-2.0
"""Qualify shared-library admission in installed Photos and Camera sandboxes.

Run as a non-root user on an isolated installed guest with both signed system
applications. This opens no SQLite database and changes no persistent Flatpak
permission. Each denial uses a command-local revocation of the narrow grant.
"""
import json
import os
import subprocess

assert os.getuid() != 0, 'Run as the isolated guest test user, not root'

allowed = '''
import json,os
from pathlib import Path
from prairie_apps.photos_backend import default_database_path,_shared_library_mounted
expected = Path(os.environ.get('HOST_XDG_DATA_HOME') or str(Path.home()/'.local/share'))/'luma-photos'
database = default_database_path()
assert database.name == 'library.sqlite3'
assert database.parent.resolve() == expected.resolve()
assert _shared_library_mounted(expected)
print(json.dumps({'app':os.environ['FLATPAK_ID'],'granted_library_accepted':True}))
'''

denied = '''
import json,os
from prairie_apps.photos_backend import default_database_path
try:
    default_database_path()
except PermissionError:
    print(json.dumps({'app':os.environ['FLATPAK_ID'],'ungranted_library_refused':True}))
else:
    raise AssertionError('An ungranted synthetic home was accepted as the shared Photos library')
'''

results = []
for app in ('org.projectluma.Photos', 'org.projectluma.Camera'):
    for code, extra, field in (
        (allowed, [], 'granted_library_accepted'),
        (denied, ['--nofilesystem=xdg-data/luma-photos'], 'ungranted_library_refused'),
    ):
        result = subprocess.run(
            ['flatpak', 'run', '--system', '--command=python3', *extra, app, '-c', code],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        row = json.loads(result.stdout)
        assert row['app'] == app and row[field] is True
        results.append(row)

print(json.dumps({'check': 'installed-photos-camera-library-access',
                  'result': 'pass', 'sqlite_opened': False,
                  'persistent_permissions_changed': False, 'sandboxes': results}))
