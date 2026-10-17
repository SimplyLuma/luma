#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reopen an existing qualified developer workspace and check real OS controls."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from luma_tester.core import Session

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--state', type=Path, required=True)
p.add_argument('--target', choices=('mobile', 'desktop'), default='mobile')
a = p.parse_args()
out = Path(os.environ['DEV_ARTIFACT_DIR'])
manifest = Path(os.environ['LUMA_TESTER_CATALOG']) / (a.target + '.json')
session = Session(manifest, a.state)
assert session.disk.is_file(), 'This check requires a previously qualified workspace.'
key = session.key.read_bytes()
host_pin = (session.directory / 'known_hosts').read_bytes()
with (out / 'launcher.log').open('w') as log:
    child = subprocess.Popen([sys.executable, '-m', 'luma_tester', '--state', str(a.state),
        'start', str(manifest), '--embedded', '--memory', '3072', '--cpus', '2'], stdout=log, stderr=log)
    try:
        session.wait_graphical(120)
        assert session.key.read_bytes() == key
        assert (session.directory / 'known_hosts').read_bytes() == host_pin
        text = subprocess.check_output(session.ssh(['cat', 'Projects/app/message.txt']), text=True).strip()
        assert text == 'Hot fix is live.', text
        session.qmp('stop')
        assert session.status() == 'paused'
        session.qmp('cont')
        assert session.status() == 'running'
        session.qmp('screendump', {'filename': str(out / 'reopened.png'), 'format': 'png'})
        session.shutdown()
        assert child.wait(timeout=45) == 0
        (out / 'qualification.json').write_text(json.dumps({'passed': True,
            'target': a.target, 'project_preserved': True, 'ssh_identity_preserved': True,
            'pause_resume': True, 'graceful_shutdown': True}, indent=2) + '\n')
        print('PASS saved project/SSH identity, real guest pause/resume and second graceful shutdown.')
    finally:
        if child.poll() is None:
            try:
                session.shutdown()
                child.wait(timeout=45)
            except Exception:
                child.terminate()
                child.wait(timeout=15)
