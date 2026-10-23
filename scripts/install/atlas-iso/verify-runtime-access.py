#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Check archived runtime paths remain readable/traversable after dropping root.

Accepts lsinitrd or unsquashfs -ll output without extracting or changing files.
Checking archive metadata avoids accidentally testing an extractor's own umask.
"""
import posixpath
import re
import sys
from pathlib import Path

DIRECTORIES = ('usr/lib', 'usr/lib64', 'usr/share', 'usr/share/dbus-1',
               'usr/share/dbus-1/system.d', 'etc/dbus-1')
FILES = ('etc/passwd', 'etc/group', 'etc/machine-id', 'usr/share/dbus-1/system.conf')
EXECUTABLES = ('usr/bin/dbus-broker', 'usr/bin/dbus-broker-launch', 'usr/bin/NetworkManager')
PUBLIC_CACHES = ('usr/share/glib-2.0/schemas/gschemas.compiled',
                 'usr/share/mime/mime.cache',
                 'usr/share/gnome-kiosk/gnomekiosk.dconf.compiled')


def parse(text):
    entries = {}
    for line in text.splitlines():
        if not re.match(r'^[dl-][rwxstST-]{9}\s', line):
            continue
        left, arrow, target = line.partition(' -> ')
        if ' squashfs-root' in left:
            path = left.split(' squashfs-root', 1)[1].lstrip('/') or '.'
        else:
            fields = left.split(None, 8)
            if len(fields) != 9:
                raise ValueError(f'malformed runtime archive listing: {line}')
            path = fields[8].removeprefix('./')
        if path == 'squashfs-root':
            path = '.'
        if path in entries:
            raise ValueError(f'duplicate runtime archive path: {path}')
        entries[path] = (line[:10], target if arrow else None)
    return entries


def check(text):
    entries = parse(text)

    def verify(path, kind, visited=()):
        if path in visited:
            raise ValueError(f'runtime archive symlink cycle: {path}')
        if path not in entries:
            raise ValueError(f'runtime archive lacks required path: {path}')
        mode, target = entries[path]
        if target is not None:
            resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else
                                         posixpath.join(posixpath.dirname(path), target))
            if resolved == '..' or resolved.startswith('../'):
                raise ValueError(f'runtime archive symlink escapes root: {path}')
            verify(resolved, kind, (*visited, path))
            return
        if kind == 'directory':
            allowed = mode[0] == 'd' and mode[7] == 'r' and mode[9] in 'xt'
        else:
            allowed = mode[0] == '-' and mode[7] == 'r' and (kind != 'executable' or mode[9] in 'xt')
        if not allowed:
            raise ValueError(f'runtime path inaccessible to an ordinary service user: {mode} {path}')
        parent = posixpath.dirname(path)
        if parent:
            verify(parent, 'directory', (*visited, path))

    checks = [(path, 'directory') for path in DIRECTORIES]
    checks.extend((path, 'file') for path in FILES)
    checks.extend((path, 'executable') for path in EXECUTABLES)
    # Package scriptlets and Lorax create these after RPM restores file modes.
    # They are public service/UI inputs; do not relax intentional private data
    # such as shadow, keys, SELinux policy or fontconfig's CACHEDIR.TAG.
    for path, (mode, _) in entries.items():
        public_cache = path in PUBLIC_CACHES or (
            mode[0] == '-' and path.startswith(('usr/share/mime/', 'etc/dconf/db/'))
        ) or re.fullmatch(r'(?:usr/lib(?:64)?/fontconfig/cache|var/cache/fontconfig)/[^/]+\.cache-\d+', path)
        if public_cache:
            checks.append((path, 'file'))
    errors = []
    for path, kind in checks:
        try:
            verify(path, kind)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError('\n'.join(dict.fromkeys(errors)))


if __name__ == '__main__':
    check(Path(sys.argv[1]).read_text())
    print('Installer runtime ordinary-user access verified from archive metadata')
