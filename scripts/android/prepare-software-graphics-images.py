#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive a pinned emulator-only vendor image; preserve the coherent system pair.

Offline preparation only, never edits installed images. The downstream allocator
is source-built by build-software-gralloc.py. Deploy through the normal image
transaction after stopping Android. Physical devices do not consume this variant.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

BASE = {'system.img': '0e9f27f5ecc7b8029a1bde0976693e29f73cc29e7ca77ec699b507e38cdbcfbf',
        'vendor.img': '96c130d2c44df2fe577f8de914ac2d9b0168f9bed4b70d9a075a52afd28632ae'}
MODULE = '2106130c991a6c4c111de31f79154c3d1a58200bb2b70bab85171bb1563c0ef3'
PATCH = '8530911941bff568679cca9f890f05d531dec1b38cf73fda224f68a0da89f646'
LIBRARY = '/lib64/hw/gralloc.default.so'
LABEL = b'u:object_r:same_process_hal_file:s0\x00'

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def validate_inputs(images, artifact):
    for name, expected in BASE.items():
        if digest(images / name) != expected:
            raise ValueError('Unrecognized Android base image: ' + name)
    info = json.loads((artifact / 'build-info.json').read_text())
    if digest(artifact / 'gralloc.default.so') != MODULE or info.get('module_sha256') != MODULE or info.get('patch_sha256') != PATCH:
        raise ValueError('Allocator artifact does not match the reviewed source pin')
    return info

def prepare(images, artifact, output):
    info = validate_inputs(images, artifact)
    if output.exists():
        raise ValueError('Refusing to overwrite existing output')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.software-images-', dir=output.parent) as tmp:
        stage = Path(tmp)
        for name in BASE:
            shutil.copyfile(images / name, stage / name)
        vendor = stage / 'vendor.img'
        # Official vendor images have no spare blocks; grow only the staged copy
        # for the source-built module, keeping the installed pair untouched.
        with vendor.open('r+b') as file:
            file.truncate(vendor.stat().st_size + 16 * 1024 * 1024)
        subprocess.run(['resize2fs', str(vendor)], check=True, stdout=subprocess.DEVNULL)
        def debug(command, write=False):
            args = ['debugfs'] + (['-w'] if write else []) + ['-R', command, str(vendor)]
            result = subprocess.run(args, check=True, capture_output=True, timeout=30)
            if write:
                print(command.split()[0], result.stderr.decode(), flush=True)
            return result
        # Relative temporary paths avoid debugfs command quoting/path injection.
        # Read and restore the exact SELinux EA, rather than inventing a label.
        label = stage / 'label'
        module = stage / 'module'
        shutil.copyfile(artifact / 'gralloc.default.so', module)
        # debugfs accepts quoted paths. Reject quotes/backslashes before using them.
        for p in (label, module):
            if any(c in str(p) for c in ('"', '\\', '\n')):
                raise ValueError('Unsupported build path')
        debug('ea_get -f "' + str(label) + '" ' + LIBRARY + ' security.selinux')
        original_label = label.read_bytes()
        if original_label.rstrip(b'\x00') != LABEL.rstrip(b'\x00'):
            raise ValueError('Unexpected allocator security context')
        debug('rm ' + LIBRARY, True)
        debug('write "' + str(module) + '" ' + LIBRARY, True)
        debug('set_inode_field ' + LIBRARY + ' mode 0100644', True)
        debug('ea_set -f "' + str(label) + '" ' + LIBRARY + ' security.selinux', True)
        if hashlib.sha256(debug('cat ' + LIBRARY).stdout).hexdigest() != MODULE:
            raise ValueError('Written allocator differs from admitted artifact')
        label.unlink()
        debug('ea_get -f "' + str(label) + '" ' + LIBRARY + ' security.selinux')
        if label.read_bytes() != original_label:
            raise ValueError('Allocator label was not preserved')
        subprocess.run(['e2fsck', '-fn', str(vendor)], check=True, stdout=subprocess.DEVNULL)
        label.unlink(); module.unlink()
        manifest = ''.join(digest(stage / name) + '  ' + name + '\n' for name in BASE)
        (stage / 'SHA256SUMS').write_text(manifest)
        (stage / 'luma-software-graphics.json').write_text(json.dumps({'base_images': BASE, 'allocator': info, 'profile': 'emulator-software-graphics'}, indent=2) + '\n')
        stage.rename(output)
    print(output)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('images', type=Path)
    p.add_argument('artifact', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    prepare(a.images.resolve(), a.artifact.resolve(), a.output.resolve())

if __name__ == '__main__':
    main()
