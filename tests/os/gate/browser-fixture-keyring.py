#!/usr/bin/python3
"""Initialize only the newly created OS browser gate account's encrypted keyring."""
import argparse
import pwd
import stat
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import time


SERVICE = 'org.freedesktop.secrets'
USER = 'luma-gate-browser-opens'


def validate_context(uid):
    """Refuse existing accounts, alternate homes/buses, and borrowed keyrings."""
    account = pwd.getpwnam(USER)
    home = Path('/home') / USER
    runtime = Path('/run/user') / str(uid)
    assert uid > 0 and os.getuid() == uid == account.pw_uid
    assert account.pw_dir == str(home)
    assert os.environ.get('HOME') == str(home)
    assert os.environ.get('USER') == USER
    assert os.environ.get('XDG_RUNTIME_DIR') == str(runtime)
    assert os.environ.get('DBUS_SESSION_BUS_ADDRESS') == f'unix:path={runtime}/bus'
    for directory in (home, runtime):
        info = directory.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == uid
    info = (runtime / 'bus').lstat()
    assert stat.S_ISSOCK(info.st_mode) and info.st_uid == uid
    keyrings = home / '.local/share/keyrings'
    for directory in (home / '.local', home / '.local/share', keyrings):
        if directory.exists() or directory.is_symlink():
            info = directory.lstat()
            assert stat.S_ISDIR(info.st_mode) and info.st_uid == uid
    assert not keyrings.exists() or not any(keyrings.iterdir()), 'Fixture keyring must be fresh'
    return home


def initialize(uid):
    validate_context(uid)
    deadline = time.monotonic() + 12

    def bus(*arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('fixture-keyring-readiness', 12)
        result = subprocess.run(['/usr/bin/busctl', '--user', '--timeout=1', *arguments],
                                check=True, capture_output=True, text=True,
                                timeout=min(2, remaining))
        return shlex.split(result.stdout)

    def scalar(kind, *arguments):
        reply = bus(*arguments)
        assert len(reply) == 2 and reply[0] == kind, 'Unexpected D-Bus reply type'
        return reply[1]

    # A fresh non-PAM fixture has no login password to unlock Secret Service.
    # Never use an empty password or print/persist this random encrypted secret.
    initialized = subprocess.run(['/usr/bin/gnome-keyring-daemon', '--unlock',
                                  '--components=secrets'],
                                 input=secrets.token_urlsafe(32).encode(),
                                 capture_output=True, timeout=5)
    assert initialized.returncode == 0, 'Disposable keyring initialization failed'

    while True:
        try:
            owner = scalar('s', 'call', 'org.freedesktop.DBus',
                           '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                           'GetNameOwner', 's', SERVICE)
            assert owner.startswith(':'), 'Secret Service has no unique owner'
            owner_uid = scalar('u', 'call', 'org.freedesktop.DBus',
                               '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                               'GetConnectionUnixUser', 's', owner)
            assert owner_uid == str(uid), 'Secret Service belongs to another UID'
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
            assert scalar('s', 'call', 'org.freedesktop.DBus',
                          '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                          'GetNameOwner', 's', SERVICE) == owner, 'Secret Service owner changed'
            return {'scope': 'DISPOSABLE_OS_GATE_KEYRING_READINESS', 'uid': uid,
                    'service': SERVICE, 'unique_owner': owner, 'bus_guid': guid,
                    'collections': collections}
        except (AssertionError, subprocess.SubprocessError):
            if time.monotonic() >= deadline:
                raise RuntimeError('Disposable login/default keyring readiness failed') from None
            time.sleep(.1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(initialize(args.uid), sort_keys=True))
