#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Wrap an assembled Luma Tester directory as an offline Linux AppImage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[2]
RUNTIME_SHA256 = '156f4bdbde9c52d01814600013e0a273f0118dc2de98975f3c8c63427ec79074'
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--portable-dir', type=Path, required=True)
p.add_argument('--runtime', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
assert hashlib.file_digest(a.runtime.open('rb'), 'sha256').hexdigest() == RUNTIME_SHA256
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=False)
app = out / 'AppDir'
# The source is a task-owned immutable build result; hard links avoid duplicating
# the large guest disk during staging. No source file is edited through a link.
shutil.copytree(a.portable_dir, app, symlinks=True, copy_function=os.link)
(app / 'AppRun').write_text('''#!/bin/sh
set -eu
tester_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$tester_dir/Luma Tester" "$@"
''')
(app / 'AppRun').chmod(0o755)
(app / 'io.luma.Tester.desktop').write_text('''[Desktop Entry]
Type=Application
Name=Luma Tester
Comment=Try Luma or build apps for Mobile and Desktop
Exec=AppRun
Icon=io.luma.Tester
Categories=Development;Emulator;
Terminal=false
''')
icon = app / 'usr/share/icons/hicolor/scalable/apps/io.luma.Tester.svg'
assert icon.is_file(), icon
shutil.copy2(icon, app / 'io.luma.Tester.svg')
(app / '.DirIcon').symlink_to('io.luma.Tester.svg')
notice = app / 'usr/share/licenses/appimage-runtime'
notice.mkdir(parents=True, exist_ok=True)
shutil.copy2(REPO / 'packaging/standalone/tide/appimage-runtime.LICENSE', notice / 'LICENSE')
app.chmod(0o755)
subprocess.run(['desktop-file-validate', str(app / 'io.luma.Tester.desktop')], check=True)
payload = out / 'payload.squashfs'
subprocess.run(['mksquashfs', str(app), str(payload), '-noappend', '-no-xattrs',
                '-all-root', '-comp', 'zstd', '-Xcompression-level', '6', '-processors', '2', '-mem', '1G'], check=True)
image = out / 'Luma-Tester-0.1.0-linux-x86_64.AppImage'
with image.open('wb') as target:
    for source in (a.runtime, payload):
        with source.open('rb') as stream:
            shutil.copyfileobj(stream, target)
image.chmod(0o755)
with image.open('rb') as stream:
    sha = hashlib.file_digest(stream, 'sha256').hexdigest()
(out / 'SHA256SUMS').write_text(sha + '  ' + image.name + '\n')
(out / 'appimage-receipt.json').write_text(json.dumps({
    'runtime_sha256': RUNTIME_SHA256, 'sha256': sha, 'bytes': image.stat().st_size,
    'portable_source': json.loads((a.portable_dir / 'build-receipt.json').read_text())['source_commit'],
}, indent=2) + '\n')
print(str(image), sha, flush=True)
