#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build unactivated before/after overlays on the host, for INT/S only.

Run through an existing permitted host route while the capsule SSH hold applies.
This does not activate an overlay, edit live settings, or restart any Shell.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--scratch', type=Path, default=Path('/var/home/nick/Documents/.luma-dev/notifications-quick-options'))
p.add_argument('--suffix', default='')
a = p.parse_args()
if a.suffix and not all(c.isalnum() or c == '-' for c in a.suffix):
    raise RuntimeError('overlay suffix must contain only letters, digits or hyphens')
repo = Path(__file__).resolve().parents[3]
installed = subprocess.check_output(['rpm', '-q', 'gnome-shell'], text=True).strip()
expected = 'gnome-shell-50.3-1.luma.99.surfacepreview20260914.147.fc44.x86_64'
if installed != expected:
    raise RuntimeError(f'reconcile this test against installed {installed}; expected {expected}')
files = ['lumaNotificationBeacon.js', 'messageTray.js', 'messageList.js', 'notificationDaemon.js',
         'lumaSurfaceMaterials.js', 'quickSettings.js', 'panel.js', 'status/system.js',
         'status/doNotDisturb.js', 'status/powerProfiles.js', 'status/bluetooth.js',
         'status/network.js', 'status/volume.js']
for label in ['before-0205', '0205']:
    out = Path.home() / '.local/share/luma-shell-live' / f'notifications-startup-{label}{"-" + a.suffix if a.suffix else ""}'
    if out.exists(): raise RuntimeError(f'refusing to overwrite existing {out}')
    out.mkdir(parents=True)
    for name in files:
        source = a.scratch / 'edited/js/ui' / name
        if name == 'lumaNotificationBeacon.js' and label == 'before-0205':
            source = a.scratch / 'base0205/js/ui' / name
        dest = out / 'ui' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    # Exact existing candidate baseline, not changes to Shelf-owned source.
    for name in ['shelf.js', 'shelfArrangement.js', 'shelfMetrics.js', 'liveActivity.js', 'dash.js']:
        shutil.copyfile(a.scratch / 'applied/js/ui' / name, out / 'ui' / name)
    for source in (a.scratch / 'edited/data/theme').glob('*.css'):
        dest = out / 'theme' / source.name
        dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, dest)
    icons = out / 'icons/scalable/status'
    icons.mkdir(parents=True)
    for source in (a.scratch / 'edited/data/icons/hicolor/scalable/status').glob('*.svg'):
        shutil.copyfile(source, icons / source.name)
    for name in ['check', 'moon', 'rotate-cw', 'power', 'log-out', 'headphones', 'speaker', 'monitor', 'keyboard', 'mouse']:
        filename = f'lumaui-{name}-symbolic.svg'
        shutil.copyfile(repo / 'assets/icon-theme/Prairie/symbolic/actions' / filename, icons / filename)
    schemas = out / 'schemas'; schemas.mkdir()
    for source in (repo / 'src/luma-shell-state').glob('*.xml'):
        shutil.copyfile(source, schemas / source.name)
    subprocess.run(['glib-compile-schemas', str(schemas)], check=True)
    (out / 'STAMP').write_text(installed + '\n')
    (out / 'STARTUP-TEST.json').write_text(json.dumps({
        'branch': 'codex/notifications-options-152-20260926',
        'patch': '0204+0206+0207+0208+0209+0210 baseline' if label == 'before-0205' else '0205+0206+0207+0208+0209+0210 allocation fix',
        'candidate_release': '.151', 'installed_nvr': installed,
        'fix_commit': '890c7ee0',
        'source_sha256': {str(source.relative_to(out)): hashlib.sha256(source.read_bytes()).hexdigest()
                          for folder in ['ui', 'theme', 'icons', 'schemas'] for source in (out / folder).rglob('*') if source.is_file()},
        'shelf_baseline': '0189-0191; integration with S0194+ remains separate',
        'activated': False, 'production': False, 'pixel_acceptance': False,
        'pending_kit_glyphs': ['plane', 'hotspot'],
    }, indent=2) + '\n')
    print(out)
