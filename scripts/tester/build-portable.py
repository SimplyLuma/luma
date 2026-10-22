#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble a Linux x86_64 developer preview using a qualified LumaUI runtime.

Run under devctl. All tools and libraries go into the output; no host install.
The runtime input must be an existing qualified portable LumaUI directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parents[2]
# The final checked-in location is scripts/tester/build-portable.py.
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--runtime', type=Path, required=True)
p.add_argument('--gvnc-prefix', type=Path, required=True)
p.add_argument('--gvnc-source', type=Path, required=True, help='Matching Fedora gtk-vnc source RPM')
p.add_argument('--gvnc-license', type=Path, required=True, help='Upstream COPYING.LIB')
p.add_argument('--catalog', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
assert platform.machine() == 'x86_64', 'This producer currently assembles x86_64 Linux hosts.'
runtime, dest = a.runtime.resolve(), a.output.resolve()
dest.mkdir(parents=True, exist_ok=False)

def copy(source, relative):
    target = dest / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    else:
        shutil.copy2(source, target)

# Reuse the qualified native closure, Python, schemas, fonts and license notices.
copy(runtime / 'usr', 'usr')
site = dest / 'usr/lib64/python3.14/site-packages'
for name in ('prairie_apps', 'luma_appkit', 'sitecustomize.py'):
    path = site / name
    if path.is_dir(): shutil.rmtree(path)
    elif path.exists(): path.unlink()
copy(REPO / 'src/luma-platform/appkit/luma_appkit', site.relative_to(dest) / 'luma_appkit')
for glyph in (REPO / 'assets/icon-theme/Prairie/symbolic/actions').glob('lumaui-*.svg'):
    copy(glyph, site.relative_to(dest) / 'luma_appkit/icons' / glyph.name)
copy(REPO / 'src/luma-tester/luma_tester', site.relative_to(dest) / 'luma_tester')
copy(REPO / 'src/luma-tester/luma_tester/io.luma.Tester.svg', 'usr/share/icons/hicolor/scalable/apps/io.luma.Tester.svg')
copy(REPO / 'src/luma-tester/io.luma.Tester.desktop', 'usr/share/applications/io.luma.Tester.desktop')
copy(runtime / 'fonts.conf', 'fonts.conf')
loaders = (runtime / 'pixbuf-loaders.cache').read_text()
# Existing standalone runtime producers use an application-specific path token.
loaders = re.sub(r'@[A-Z_]+ROOT@', '@TESTER_ROOT@', loaders)
(dest / 'pixbuf-loaders.cache').write_text(loaders)
for relative in ('lib64/libgvnc-1.0.so.0', 'lib64/girepository-1.0/GVnc-1.0.typelib'):
    copy(a.gvnc_prefix / relative, Path('usr') / relative)
copy(REPO / 'packaging/standalone/tester/bootstrap.py', 'bootstrap.py')
copy(REPO / 'packaging/standalone/tester/README.md', 'README.md')
copy(a.gvnc_source, 'sources/gtk-vnc.src.rpm')
copy(a.gvnc_license, 'usr/share/licenses/gvnc/COPYING.LIB')
copy(REPO / 'src/luma-tester/THIRD-PARTY.md', 'THIRD-PARTY.md')
copy(REPO / 'docs/developer-platform/tester.md', 'DEVELOPING.md')
copy(REPO / 'examples/tester-hello', 'examples/Hello Luma')

# Keep the tool ABI/loader independent of the LumaUI ABI. ldd operates only on
# the explicitly selected installed distribution binaries/modules.
tools = ['qemu-system-x86_64', 'qemu-img', 'ssh', 'ssh-keygen', 'rsync', 'xorriso']
seeds = [Path(shutil.which(name) or '/missing/' + name).resolve() for name in tools]
modules = [Path('/usr/lib64/qemu') / name for name in
           ('hw-display-virtio-gpu.so', 'hw-display-virtio-gpu-pci.so',
            'hw-display-virtio-gpu-gl.so', 'hw-display-virtio-gpu-pci-gl.so',
            'ui-egl-headless.so', 'ui-gtk.so', 'ui-opengl.so')]
# EGL/GBM loads its Mesa drivers dynamically, beyond ldd's dependency graph.
# Keep these in the tools ABI, separate from the UI's qualified native runtime.
graphics = [Path('/usr/lib64/libEGL_mesa.so.0'), Path('/usr/lib64/libvulkan.so.1')]
graphics += list(Path('/usr/lib64').glob('libgallium-*.so'))
for driver in ('intel', 'intel_hasvk', 'radeon', 'nouveau', 'lvp', 'virtio'):
    graphics.append(Path('/usr/lib64/libvulkan_' + driver + '.so'))
    metadata = Path('/usr/share/vulkan/icd.d') / (driver + '_icd.x86_64.json')
    data = json.loads(metadata.read_text())
    data['ICD']['library_path'] = Path(data['ICD']['library_path']).name
    target = dest / 'tools/vulkan' / metadata.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data) + '\n')
for source in Path('/usr/lib64/dri').glob('*_dri.so'):
    copy(source, Path('tools/dri') / source.name)
    graphics.append(source)
copy(Path('/usr/share/glvnd/egl_vendor.d/50_mesa.json'), 'tools/egl/50_mesa.json')
packages = {}
copied_dependencies = set()
for source in seeds + modules + graphics:
    assert source.is_file(), source
    text = subprocess.check_output(['ldd', str(source)], text=True)
    if 'not found' in text: raise RuntimeError('Missing tool dependency: ' + text)
    dependencies = re.findall(r'(?:=>\s+|^\s*)(/[^\s]+)', text, flags=re.M)
    for name in dependencies:
        path = Path(name)
        if path in copied_dependencies:
            continue
        copied_dependencies.add(path)
        copy(path, Path('tools/lib') / path.name)
        packages[str(path)] = subprocess.check_output(
            ['rpm', '-qf', '--qf', '%{NEVRA}\t%{SOURCERPM}\t%{LICENSE}\n', str(path)], text=True).strip()
    packages[str(source)] = subprocess.check_output(
        ['rpm', '-qf', '--qf', '%{NEVRA}\t%{SOURCERPM}\t%{LICENSE}\n', str(source)], text=True).strip()
    if source in graphics:
        # DRI entry points also retain their conventional driver filenames.
        copy(source, Path('tools/lib') / source.name)
    elif source in modules:
        copy(source, Path('tools/modules') / source.name)
    else:
        copy(source, Path('tools/raw') / source.name)
for name in tools:
    real = Path(shutil.which(name)).resolve().name
    extra = ' -L "$tester_dir/tools/share/qemu"' if name.startswith('qemu-system-') else ''
    wrapper = dest / 'tools/bin' / name
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text('''#!/bin/sh
set -eu
tester_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export QEMU_MODULE_DIR="$tester_dir/tools/modules"
export __EGL_VENDOR_LIBRARY_FILENAMES="$tester_dir/tools/egl/50_mesa.json"
export LIBGL_DRIVERS_PATH="$tester_dir/tools/dri"
export VK_DRIVER_FILES=$(printf '%s:' "$tester_dir"/tools/vulkan/*.json)
exec "$tester_dir/tools/lib/ld-linux-x86-64.so.2" --inhibit-cache --library-path "$tester_dir/tools/lib" "$tester_dir/tools/raw/''' + real + '"' + extra + ' "$@"\n')
    wrapper.chmod(0o755)
for name in ('qemu', 'seabios', 'ipxe'):
    copy(Path('/usr/share') / name, Path('tools/share') / name)
    if name != 'qemu':
        copy(Path('/usr/share') / name, 'tools/share/qemu')
# Fedora keeps network ROM aliases in a separate search directory.
# Dereference those aliases into QEMU's one relocatable firmware directory.
for path in Path('/usr/share/ipxe/qemu').glob('*.rom'):
    copy(path, Path('tools/share/qemu') / path.name)
    packages[str(path.resolve())] = subprocess.check_output(
        ['rpm', '-qf', '--qf', '%{NEVRA}\t%{SOURCERPM}\t%{LICENSE}\n', str(path.resolve())], text=True).strip()
# Retain the exact installed tool packages' upstream notices.
for line in set(packages.values()):
    nevra = line.split('\t')[0]
    files = subprocess.check_output(['rpm', '-ql', nevra], text=True).splitlines()
    for name in files:
        path = Path(name)
        if name.startswith('/usr/share/licenses/') and path.is_file():
            copy(path, Path('tools/licenses') / path.relative_to('/usr/share/licenses'))
(dest / 'tool-packages.json').write_text(json.dumps(packages, indent=2) + '\n')

# Embed one verified common disk, with relocatable target manifests.
sys.path.insert(0, str(REPO / 'src/luma-tester'))
from luma_tester.core import digest, read_manifest
for path in sorted(a.catalog.glob('*.json')):
    manifest = read_manifest(path)
    assert manifest['arch'] == 'x86_64', 'Host package currently contains x86_64 guests.'
    url = urlparse(manifest['image']['url'])
    assert url.scheme == 'file' and not url.netloc, 'This preview producer embeds local qualified disks.'
    source = Path(unquote(url.path))
    assert source.stat().st_size == manifest['image']['bytes'] and digest(source) == manifest['image']['sha256']
    relative = 'images/' + manifest['image']['sha256'] + '.qcow2'
    if not (dest / relative).exists(): copy(source, relative)
    manifest['image']['url'] = ''
    manifest['bundled_image'] = relative
    (dest / 'catalog').mkdir(exist_ok=True)
    (dest / 'catalog' / path.name).write_text(json.dumps(manifest, indent=2) + '\n')

(dest / 'python').write_text('''#!/bin/sh
set -eu
tester_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH
exec "$tester_dir/usr/lib64/ld-linux-x86-64.so.2" --inhibit-cache --library-path "$tester_dir/usr/lib64" "$tester_dir/usr/bin/python3.14" "$tester_dir/bootstrap.py" "$@"
''')
(dest / 'Luma Tester').write_text('''#!/bin/sh
set -eu
tester_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -eq 0 ]; then set -- gui; fi
exec "$tester_dir/python" -m luma_tester "$@"
''')
for name in ('python', 'Luma Tester'): (dest / name).chmod(0o755)
# Public bundle content must be readable when AppImage stores root ownership.
for path in dest.rglob('*'):
    if not path.is_symlink():
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
dest.chmod(0o755)
receipt = {'schema': 1, 'host': 'linux-x86_64',
    'source_commit': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
    'runtime_input': str(runtime), 'files': []}
for path in sorted(dest.rglob('*')):
    if path.is_file() and not path.is_symlink():
        receipt['files'].append({'path': str(path.relative_to(dest)), 'sha256': digest(path), 'bytes': path.stat().st_size})
(dest / 'build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print('Assembled:', dest, flush=True)
