#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read only portal metadata in the fresh native CI account; never read secrets."""
import json
import os
from pathlib import Path
import subprocess


def main():
    home = Path('/home/luma-gate-browser-opens')
    assert os.getuid() == 1100 and Path.home() == home
    assert os.environ.get('XDG_RUNTIME_DIR') == '/run/user/1100'
    assert os.environ.get('DBUS_SESSION_BUS_ADDRESS') == 'unix:path=/run/user/1100/bus'
    result = {'scope': 'READ_ONLY_PORTAL_METADATA_NOT_BROWSER_ACCEPTANCE',
              'uid': os.getuid(), 'queries': {}, 'configuration': {},
              'environment': {name: os.environ.get(name) for name in
                              ('XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE', 'WAYLAND_DISPLAY')}}

    def query(label, *arguments):
        try:
            reply = subprocess.run(['/usr/bin/busctl', '--user', '--timeout=1', *arguments],
                                   capture_output=True, text=True, timeout=2)
            result['queries'][label] = {'status': reply.returncode,
                                        'stdout': reply.stdout[:2048], 'stderr': reply.stderr[:2048]}
        except subprocess.TimeoutExpired:
            result['queries'][label] = {'error': 'bounded metadata query timed out'}

    services = ('org.freedesktop.portal.Desktop', 'org.freedesktop.secrets',
                'org.freedesktop.impl.portal.Secret', 'org.freedesktop.impl.portal.desktop.gnome')
    for service in services:
        query(service + '.owner', 'call', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
              'org.freedesktop.DBus', 'GetNameOwner', 's', service)
    query('public-secret-version', 'get-property', 'org.freedesktop.portal.Desktop',
          '/org/freedesktop/portal/desktop', 'org.freedesktop.portal.Secret', 'version')
    query('keyring-backend-secret-version', 'get-property', 'org.freedesktop.secrets',
          '/org/freedesktop/portal/desktop', 'org.freedesktop.impl.portal.Secret', 'version')
    query('keyring-backend-interface', 'call', 'org.freedesktop.secrets',
          '/org/freedesktop/portal/desktop', 'org.freedesktop.DBus.Introspectable', 'Introspect')
    # These are public installed service-selection files, never keyring files,
    # portal secret requests, permission databases, or item/collection contents.
    patterns = (('/usr/share/xdg-desktop-portal/portals', '*.portal'),
                ('/usr/share/xdg-desktop-portal', '*portals.conf'),
                ('/etc/xdg/xdg-desktop-portal', '*portals.conf'),
                (str(home / '.config/xdg-desktop-portal'), '*portals.conf'))
    for directory, pattern in patterns:
        for path in sorted(Path(directory).glob(pattern))[:32]:
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536:
                result['configuration'][str(path)] = path.read_text()
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
