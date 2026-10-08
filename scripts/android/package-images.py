#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Admit pinned official archive bytes and package only the named image pair."""
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(root, architecture):
    manifest = json.loads((root / 'luma-images.json').read_text())
    if manifest['architecture'] != architecture or set(manifest['images']) != {'system.img', 'vendor.img'}:
        raise ValueError('Wrong architecture or incomplete Android image pair')
    for name, record in manifest['images'].items():
        image = root / name
        if image.is_symlink() or image.stat().st_size != record['size'] or digest(image) != record['sha256']:
            raise ValueError(f'Android image verification failed: {name}')


def prepare(pin, archives, output, architecture):
    android_arch = {'x86_64': 'x86_64', 'aarch64': 'arm64'}.get(architecture)
    if android_arch is None:
        raise ValueError('Unsupported Android image architecture')
    values = dict(line.split('=', 1) for line in pin.read_text().splitlines()
                  if line.startswith('LUMA_ANDROID_') and '=' in line)
    manifest = {'architecture': architecture, 'images': {}, 'archives': {}}
    origin = values.get('LUMA_ANDROID_IMAGE_ORIGIN', 'official-waydroid')
    if origin == 'luma-native-builder':
        source_sha = values.get('LUMA_ANDROID_SOURCE_MANIFEST_SHA256', '')
        invocation = values.get('LUMA_ANDROID_COMPILER_INVOCATION_ID', '')
        if not re.fullmatch('[a-f0-9]{64}', source_sha) or not re.fullmatch('[a-f0-9]{32}', invocation):
            raise ValueError('Luma image pair needs its exact source/terminal producer identity')
        manifest['origin'] = {'kind': origin, 'source_manifest_sha256': source_sha,
                              'compiler_invocation_id': invocation,
                              'public_source_offer_qualified': False}
    elif origin != 'official-waydroid':
        raise ValueError('Unsupported image producer')
    for kind, source in zip(('SYSTEM', 'VENDOR'), archives, strict=True):
        prefix = f'LUMA_ANDROID_{kind}_'
        if f'waydroid_{android_arch}' not in values[prefix + 'FILENAME']:
            raise ValueError('Pinned Android archive does not match the RPM architecture')
        if (source.is_symlink() or source.stat().st_size != int(values[prefix + 'SIZE'])
                or digest(source) != values[prefix + 'SHA256']):
            raise ValueError(f'Unverified pinned {kind.lower()} archive')
        name = kind.lower() + '.img'
        with zipfile.ZipFile(source) as archive:
            members = [member for member in archive.infolist() if member.filename == name]
            if len(members) != 1 or members[0].is_dir() or members[0].file_size <= 0:
                raise ValueError(f'Expected exactly one regular image: {name}')
            with archive.open(members[0]) as incoming, (output / name).open('xb') as target:
                shutil.copyfileobj(incoming, target, 1024 * 1024)
        image = output / name
        manifest['images'][name] = {'size': image.stat().st_size, 'sha256': digest(image)}
        if origin == 'luma-native-builder':
            if (str(manifest['images'][name]['size']) != values.get(prefix + 'IMAGE_SIZE')
                    or manifest['images'][name]['sha256'] != values.get(prefix + 'IMAGE_SHA256')):
                raise ValueError('Archive does not contain the terminal compiler image')
        manifest['archives'][kind.lower()] = {
            'file': values[prefix + 'FILENAME'], 'url': values[prefix + 'URL'],
            'sha256': values[prefix + 'SHA256'], 'size': int(values[prefix + 'SIZE'])}
    (output / 'luma-images.json').write_text(json.dumps(manifest, indent=2) + '\n')
    verify(output, architecture)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--verify':
        verify(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 6:
        prepare(Path(sys.argv[1]), [Path(sys.argv[2]), Path(sys.argv[3])],
                Path(sys.argv[4]), sys.argv[5])
    else:
        raise SystemExit('usage: package-images.py PIN SYSTEM VENDOR OUTPUT ARCH | --verify OUTPUT ARCH')
