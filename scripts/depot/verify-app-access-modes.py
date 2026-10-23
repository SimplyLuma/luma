#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify a distributable app is readable and its declared entrypoint executable."""
import argparse
import configparser
import json
import posixpath
import shlex
import subprocess


def verify(repo, ref):
    def ostree(*args):
        return subprocess.check_output(['ostree', '--repo=' + str(repo), *args], text=True)
    commit = ostree('rev-parse', ref).strip()
    rows = ostree('ls', '-R', commit, '/').splitlines()
    entries = {}
    for row in rows:
        kind_mode, _, _, _, pathname = row.split(maxsplit=4)
        kind, mode = kind_mode[0], int(kind_mode[1:], 8)
        pathname, _, target = pathname.partition(' -> ')
        entries[pathname] = (kind, mode, target)
        if kind == 'd' and (mode & 0o005 != 0o005 or mode & 0o022):
            raise ValueError('Unsearchable or publicly writable app directory: ' + row)
        if kind == '-' and (not mode & 0o004 or mode & 0o022):
            raise ValueError('Unreadable or publicly writable app file: ' + row)
        if kind not in {'d', '-', 'l'}:
            raise ValueError('Unsupported distributable app object: ' + row)
    metadata = configparser.ConfigParser(interpolation=None)
    metadata.read_string(ostree('cat', commit, '/metadata'))
    app_id = ref.split('/')[1]
    section = 'Application' if ref.startswith('app/') else 'Runtime'
    if section == 'Runtime' and not (ref.startswith('runtime/') and app_id.endswith('.Debug')):
        raise ValueError('Only an application or its debug companion can be audited')
    if metadata.get(section, 'name') != app_id:
        raise ValueError('App metadata identity differs from its ref')
    if section == 'Runtime':
        return dict(result='PASS', ref=ref, commit=commit, checked_paths=len(rows), entrypoint=None, policy='world-readable files, searchable directories, no group/other writes; debug companion has no entrypoint')
    command = shlex.split(metadata.get('Application', 'command'))
    if not command:
        raise ValueError('App entrypoint is empty')
    name = command[0]
    if name.startswith('/app/'):
        entrypoint = '/files/' + name[len('/app/'):]
    elif '/' not in name:
        entrypoint = '/files/bin/' + name
    else:
        raise ValueError('App entrypoint must be installed under /app')
    entrypoint = posixpath.normpath(entrypoint)
    for _ in range(16):
        if not entrypoint.startswith('/files/') or entrypoint not in entries:
            raise ValueError('Missing app entrypoint: ' + entrypoint)
        kind, mode, target = entries[entrypoint]
        if kind == '-':
            if not mode & 0o001:
                raise ValueError('App entrypoint is not executable: ' + entrypoint)
            break
        if kind != 'l':
            raise ValueError('App entrypoint is not a regular executable')
        if target.startswith('/app/'):
            entrypoint = '/files/' + target[len('/app/'):]
        elif target.startswith('/'):
            raise ValueError('App entrypoint symlink escapes its installed payload')
        else:
            entrypoint = posixpath.normpath(posixpath.join(posixpath.dirname(entrypoint), target))
    else:
        raise ValueError('App entrypoint symlink loop')
    return dict(result='PASS', ref=ref, commit=commit, checked_paths=len(rows),
                entrypoint=entrypoint, policy='world-readable files, searchable directories, no group/other writes, executable app entrypoint')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--ref', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.repo, args.ref), sort_keys=True))


if __name__ == '__main__':
    main()
