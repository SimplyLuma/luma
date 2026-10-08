#!/usr/bin/env python3
"""Stage approved fixture artwork and run the actual closed native fixture app.

Call inside toolbox, with the private kit prefix supplied by the caller.
Each native run receives its own private XDG/bus/Xvfb through smoke-app.sh.
No installed application, real data or system service is contacted.
"""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[3]
build = Path(sys.argv[1]).resolve()
pages = sys.argv[2:]
if not pages:
    raise SystemExit("Usage: verify-smoke.py <meson-build-dir> <page> [page...]")
scenario = json.loads((root / 'tools/lumaui-conform/scenarios/settings.json').read_text())
binary = build / 'shell/gnome-control-center'
if not binary.is_file():
    raise SystemExit(f"Missing native binary: {binary}")
with tempfile.TemporaryDirectory(prefix='luma-settings-smoke-assets-') as directory:
    staged = Path(directory)
    for name in scenario['gtk']['fixtures']:
        source = root / name
        if not source.is_file():
            raise SystemExit(f"Missing declared fixture artwork: {source}")
        destination = staged / source.name
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise SystemExit(f"Conflicting staged fixture basename: {source.name}")
        shutil.copyfile(source, destination)
    fixture = staged / 'settings-v70.json'
    failures = []
    for page in pages:
        for presentation in ('desktop', 'phone'):
            print(f"Native smoke page={page} presentation={presentation}", flush=True)
            result = subprocess.run(['sh', str(Path(__file__).with_name('smoke-app.sh')),
                                     str(binary), str(fixture), page, presentation],
                                    check=False, timeout=30)
            if result.returncode:
                failures.append(f'{page}/{presentation}: exit {result.returncode}')
    if failures:
        raise SystemExit('FAIL: ' + '; '.join(failures))
    print('PASS: actual native mapped/artwork/width/closed-window smoke assertions')
