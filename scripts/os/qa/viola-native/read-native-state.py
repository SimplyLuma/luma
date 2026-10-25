#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read a directly launched native browser's shipped identity diagnostics."""
import argparse
import configparser
import re
import hashlib
import json
import math
import os
from pathlib import Path
import stat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pid', type=int)
    parser.add_argument('url')
    args = parser.parse_args()
    process = Path('/proc') / str(args.pid)

    def identity():
        return (process.stat().st_uid,
                (process / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                (process / 'cmdline').read_bytes())

    before = identity()
    assert before[0] == os.getuid() == 1100
    argv = [v.decode() for v in before[2].split(b'\0') if v]
    host = '/usr/share/viola-browser/luma-host/integrated_window.py'
    home = Path('/home/luma-gate-browser-opens')
    root = home / '.local/state/viola-luma/sessions'
    deployment_path = os.environ.get('BROWSER_GATE_FLATPAK_DEPLOYMENT')
    deployment = None
    if deployment_path:
        deployment = json.loads(Path(deployment_path).read_text())
        assert deployment['scope'] == 'EXACT_SIGNED_FLATPAK_INSTALLATION_NOT_RUNTIME_ACCEPTANCE'
        assert deployment['app_ref'] == 'app/com.rhyme.viola/aarch64/beta'
        assert re.fullmatch('[0-9a-f]{64}', deployment['app_commit'])
        info = configparser.ConfigParser(interpolation=None, strict=True)
        text = (process / 'root/.flatpak-info').read_text()
        info.read_string(text)
        assert info['Application']['name'] == 'com.rhyme.viola'
        assert info['Application']['runtime'] == 'org.projectluma.Platform/aarch64/44'
        assert info['Instance']['app-commit'] == deployment['app_commit']
        assert info['Instance']['runtime-commit'] == deployment['runtime_commit']
        assert info['Instance']['arch'] == 'aarch64' and info['Instance']['branch'] == 'beta'
        instance_id = info['Instance']['instance-id']
        assert instance_id.isdecimal()
        assert (Path('/run/user/1100/.flatpak') / instance_id / 'info').read_text() == text
        from luma_installer.app_data_broker import authenticate_deployment
        authenticate_deployment(info, 'com.rhyme.viola', args.pid)
        import gi
        gi.require_version('Flatpak', '1.0')
        from gi.repository import Flatpak
        instances = [item for item in Flatpak.Instance.get_all() if item.get_id() == instance_id]
        assert len(instances) == 1
        instance = instances[0]
        assert instance.is_running() and instance.get_app() == 'com.rhyme.viola'
        assert instance.get_commit() == deployment['app_commit']
        # Bind the compositor PID to the actual running Flatpak process tree.
        ancestor = args.pid
        for _ in range(128):
            if ancestor == instance.get_child_pid():
                break
            fields = Path(f'/proc/{ancestor}/stat').read_text().rsplit(')', 1)[1].split()
            ancestor = int(fields[1])
            assert ancestor > 1
        else:
            raise AssertionError('Browser window does not descend from the signed sandbox')
        environment = dict(item.split(b'=', 1) for item in
                           (process / 'environ').read_bytes().split(b'\0') if b'=' in item)
        app_data = home / '.var/app/com.rhyme.viola'
        assert environment[b'HOME'].decode() == str(home)
        assert environment[b'FLATPAK_ID'] == b'com.rhyme.viola'
        assert environment[b'XDG_DATA_HOME'].decode() == str(app_data / 'data')
        assert environment[b'XDG_STATE_HOME'].decode() == str(app_data / '.local/state')
        root = Path(deployment['state_root'])
        assert root.is_relative_to(home / '.var/app/com.rhyme.viola')
        assert Path(deployment['profile']).is_relative_to(home / '.var/app/com.rhyme.viola')
        assert argv == ['/usr/bin/python3', '/app/share/viola-browser/luma-host/integrated_window.py',
                        '--engine', '/app/bin/viola-engine', '--profile', deployment['profile'],
                        '--work-root', str(root), '--interactive', '--start-url', args.url]
    else:
        assert argv == ['/usr/bin/python3', host, '--engine', '/opt/viola/browser/chrome',
                        '--profile', str(home / '.local/share/viola-luma/profile'),
                        '--work-root', str(root), '--interactive', '--start-url', args.url]
    path = root / 'presentation-live.json'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        initial = os.fstat(fd)
        assert stat.S_ISREG(initial.st_mode) and initial.st_uid == os.getuid()
        assert not initial.st_mode & 0o022 and initial.st_size <= 65536
        data = os.read(fd, 65537)
        final = os.fstat(fd)
        assert len(data) == initial.st_size
        assert (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns) == (
            final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
    finally:
        os.close(fd)
    state = json.loads(data)
    native = state['native_identity_geometry']
    frame = native['displayed_frame']
    assert state['window_active'] is True and state['imported_frames'] > 0
    assert native['bounds_valid'] is True and native['input_identity_valid'] is True
    assert native['scale'] == 1 and native['active_url'] == args.url
    assert frame['transport'] == 'shm' and frame['version'] == 2 and frame['id'] > 0
    assert frame['target_id'] == native['target'] and frame['tab_id'] == native['tab_id']
    assert frame['window_id'] == native['window_description']['window_id']
    x, y, w, h = native['page_bounds']
    width, height = native['window_size']
    assert all(type(v) in (int, float) and math.isfinite(v) for v in (x,y,w,h,width,height))
    assert x >= 0 and y >= 0 and w >= 80 and h >= 80
    assert x+w <= width+1 and y+h <= height+1 and width <= 1600 and height <= 1000
    assert identity() == before
    print(json.dumps(dict(native=native, imported_frames=state['imported_frames'],
                          pending_leases=state['pending_leases'], pid_start=before[1],
                          input_metrics=state['input_metrics'],
                          presentation_sha256=hashlib.sha256(data).hexdigest(),
                          signed_deployment=deployment)))


if __name__ == '__main__':
    main()
