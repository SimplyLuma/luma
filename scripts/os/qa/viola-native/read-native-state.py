#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read a directly launched native browser's shipped identity diagnostics."""
import argparse
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
    assert x+w <= width+1 and y+h <= height+1 and width <= 1280 and height <= 800
    assert identity() == before
    print(json.dumps(dict(native=native, imported_frames=state['imported_frames'],
                          pending_leases=state['pending_leases'], pid_start=before[1],
                          input_metrics=state['input_metrics'],
                          presentation_sha256=hashlib.sha256(data).hexdigest())))


if __name__ == '__main__':
    main()
