#!/usr/bin/python3
"""Initialize only the disposable native-CI account's encrypted login keyring."""
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import time


HOME = Path('/home/luma-gate-browser-opens')
SERVICE = 'org.freedesktop.secrets'
assert os.getuid() == 1100
assert os.environ.get('HOME') == str(HOME)
assert os.environ.get('XDG_RUNTIME_DIR') == '/run/user/1100'
assert os.environ.get('DBUS_SESSION_BUS_ADDRESS') == 'unix:path=/run/user/1100/bus'
assert HOME.is_dir() and not HOME.is_symlink() and HOME.stat().st_uid == 1100
keyrings = HOME / '.local/share/keyrings'
for directory in (HOME / '.local', HOME / '.local/share', keyrings):
    assert not directory.is_symlink()
assert not keyrings.exists() or not any(keyrings.iterdir()), 'Fixture keyring must be fresh'
deadline = None


def bus(*arguments):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired('fixture-keyring-readiness', 5)
    result = subprocess.run(['/usr/bin/busctl', '--user', '--timeout=1', *arguments],
                            check=True, capture_output=True, text=True,
                            timeout=min(2, remaining))
    return shlex.split(result.stdout)


def scalar(kind, *arguments):
    reply = bus(*arguments)
    assert len(reply) == 2 and reply[0] == kind, 'Unexpected D-Bus reply type'
    return reply[1]


# The daemon creates an encrypted login keyring on first --unlock. Its random
# password lives only in this process and the daemon's stdin; never print or
# persist it. Do this before GNOME/portals can start a keyring creation prompt.
initialized = subprocess.run(['/usr/bin/gnome-keyring-daemon', '--unlock',
                              '--components=secrets'],
                             input=secrets.token_urlsafe(32).encode(),
                             capture_output=True, timeout=5)
assert initialized.returncode == 0, 'Disposable keyring initialization failed'

deadline = time.monotonic() + 5
while True:
    try:
        owner = scalar('s', 'call', 'org.freedesktop.DBus',
                       '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                       'GetNameOwner', 's', SERVICE)
        assert owner.startswith(':'), 'Secret Service has no unique owner'
        collections = {}
        for alias in ('default', 'login'):
            collection = scalar('o', 'call', SERVICE, '/org/freedesktop/secrets',
                                'org.freedesktop.Secret.Service', 'ReadAlias', 's', alias)
            assert collection.startswith('/org/freedesktop/secrets/collection/')
            locked = scalar('b', 'get-property', SERVICE, collection,
                            'org.freedesktop.Secret.Collection', 'Locked')
            assert locked == 'false', 'Disposable keyring remains locked'
            collections[alias] = {'path': collection, 'locked': False}
        guid = scalar('s', 'call', 'org.freedesktop.DBus',
                      '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetId')
        break
    except (AssertionError, subprocess.SubprocessError):
        if time.monotonic() >= deadline:
            raise RuntimeError('Disposable login/default keyring readiness failed') from None
        time.sleep(.1)

print(json.dumps({'scope': 'DISPOSABLE_NATIVE_CI_KEYRING_READINESS',
                  'service': SERVICE, 'unique_owner': owner, 'bus_guid': guid,
                  'collections': collections}, sort_keys=True))
