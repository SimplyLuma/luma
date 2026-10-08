#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native public notification protocol check on an unactivated test overlay."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--protocol', choices=['fdo', 'gtk'], required=True)
a = p.parse_args()
here = Path(__file__).resolve().parent
stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
suffix = f'wire-{a.protocol}-{stamp}'
subprocess.run(['python3', str(here / 'stage-startup.py'), '--suffix', suffix], check=True)
overlay = Path.home() / '.local/share/luma-shell-live' / f'notifications-startup-0205-{suffix}'
fixture = overlay / 'ui/lumaStudioVisualFixtures.js'
shutil.copyfile(here / 'visual-fixtures.js', fixture)
manifest = overlay / 'STARTUP-TEST.json'
receipt = json.loads(manifest.read_text())
receipt.update(protocol=a.protocol, fixture_module=True, production=False, activated=False)
receipt['source_sha256'][str(fixture.relative_to(overlay))] = hashlib.sha256(fixture.read_bytes()).hexdigest()
manifest.write_text(json.dumps(receipt, indent=2) + '\n')
out = Path('/var/home/nick/Documents/LumaDesign/reports/notifications-quick-options') / f'wire-{a.protocol}-{stamp}'
out.mkdir(parents=True)
scenario = here / ('protocol-scenario.json' if a.protocol == 'fdo' else 'gtk-protocol-scenario.json')
command = ['python3', str(here / 'capture.py'), str(scenario), 'dark', '0', str(out), str(overlay)]
(out / 'COMMAND.json').write_text(json.dumps(command, indent=2) + '\n')
try:
    result = subprocess.run(command, text=True, capture_output=True, timeout=180)
except subprocess.TimeoutExpired as error:
    for key in ['stdout', 'stderr']:
        content = getattr(error, key) or ''
        if isinstance(content, bytes):
            content = content.decode(errors='replace')
        (out / f'{key}.log').write_text(content)
        print(content, end='', flush=True)
    (out / 'RESULTS.json').write_text(json.dumps({'timeout': 180, 'protocol': a.protocol, 'overlay': str(overlay)}) + '\n')
    raise
for key in ['stdout', 'stderr']:
    content = getattr(result, key)
    (out / f'{key}.log').write_text(content)
    print(content, end='', flush=True)
(out / 'RESULTS.json').write_text(json.dumps({'returncode': result.returncode, 'protocol': a.protocol,
    'overlay': str(overlay), 'production': False, 'pixel_acceptance': False}, indent=2) + '\n')
print('PUBLIC PROTOCOL EVIDENCE', out, flush=True)
raise SystemExit(result.returncode)
