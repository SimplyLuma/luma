#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the actual AppImage entry point under Xvfb, without a host install."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('appimage', type=Path)
a = p.parse_args()
out = Path(os.environ['DEV_ARTIFACT_DIR'])
image = a.appimage.resolve()
env = dict(os.environ, GSETTINGS_BACKEND='memory', XDG_CACHE_HOME=str(out / 'cache'))
for name in ('PYTHONPATH', 'GI_TYPELIB_PATH', 'LD_LIBRARY_PATH', 'LUMA_TESTER_CATALOG'):
    env.pop(name, None)
with (out / 'appimage.log').open('w') as log:
    child = subprocess.Popen([image, '--appimage-extract-and-run', '--state', str(out / 'state'), 'gui'],
                             env=env, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 45
        window = None
        while time.monotonic() < deadline:
            assert child.poll() is None, 'AppImage exited before showing its window.'
            found = subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^Luma Tester$'],
                                   text=True, capture_output=True)
            if found.returncode == 0:
                window = found.stdout.splitlines()[-1]
                break
            time.sleep(0.25)
        assert window, 'AppImage did not show its real LumaUI window.'
        time.sleep(1)
        subprocess.run(['import', '-window', window, str(out / 'appimage-welcome.png')], check=True)
        subprocess.run(['xdotool', 'windowfocus', window], check=True)
        subprocess.run(['xdotool', 'key', 'ctrl+q'], check=True)
        assert child.wait(timeout=15) == 0, 'AppImage did not quit cleanly.'
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=15)
manifest = next((out / 'cache/luma-tester/portable').glob('*/desktop.json'))
with (out / 'doctor.json').open('w') as log:
    subprocess.run([image, '--appimage-extract-and-run', 'doctor', manifest], env=env,
                   stdout=log, stderr=subprocess.PIPE, check=True, timeout=45)
report = json.loads((out / 'doctor.json').read_text())
(out / 'qualification.json').write_text(json.dumps({'passed': True, 'appimage': str(image),
    'gui_opened': True, 'keyboard_quit': True, 'doctor': report}, indent=2) + '\n')
print('PASS AppImage extraction, real LumaUI entry point, keyboard quit and bundled tool discovery.')
