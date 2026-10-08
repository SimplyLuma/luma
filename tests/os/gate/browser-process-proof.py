#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Read the real managed browser process; a Flatpak launcher is not a window."""
import configparser
import json
import os
from pathlib import Path
import sys


def verify(record, ref, commit, url):
    app_id, arch, branch = ref.split('/')[1:]
    if record['name'] != app_id or record['flatpak_id'] != app_id:
        raise ValueError('the window process belongs to another application')
    if not record['exe'].rsplit('/', 1)[-1].startswith('python3'):
        raise ValueError('the window process is not the packaged browser host')
    if record['argv'][1:2] != ['/app/share/viola-browser/luma-host/integrated_window.py']:
        raise ValueError('the process is only a launcher or another executable')
    if '--interactive' not in record['argv'] or url not in record['argv']:
        raise ValueError('the actual browser host did not receive the exact URL')
    expected = ['app', app_id, arch, branch, commit, 'files']
    if record['app_path'].split('/')[-6:] != expected:
        raise ValueError('the window process does not use the authenticated installed commit')


def observe(pid, url):
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    from luma_installer.native_app_roles import installed, required
    if not required('com.rhyme.viola'):
        raise ValueError('a signed browser owner is required for this actor')
    installation = Flatpak.Installation.new_system(None)
    ref = installed(installation, 'com.rhyme.viola')
    process = Path('/proc') / str(pid)
    if process.stat().st_uid != os.getuid():
        raise ValueError('the window does not belong to the gate session')
    metadata = configparser.ConfigParser(interpolation=None)
    metadata.read_string((process/'root/.flatpak-info').read_text())
    environment = dict(part.split(b'=',1) for part in (process/'environ').read_bytes().split(b'\0') if b'=' in part)
    record = {'pid':pid, 'name':metadata['Application']['name'],
              'app_path':metadata['Instance']['app-path'],
              'flatpak_id':environment.get(b'FLATPAK_ID',b'').decode(),
              'exe':os.readlink(process/'exe'),
              'argv':[part.decode() for part in (process/'cmdline').read_bytes().split(b'\0') if part]}
    verify(record, ref.format_ref(), ref.get_commit(), url)
    return {'ok':True, 'ref':ref.format_ref(), 'commit':ref.get_commit(), 'record':record}


if __name__ == '__main__':
    try:
        result = observe(int(sys.argv[1]),sys.argv[2])
    except Exception as error:
        result = {'ok':False,'error':str(error)}
    print(json.dumps(result))
    raise SystemExit(0 if result['ok'] else 1)
