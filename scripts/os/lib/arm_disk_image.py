#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The Luma ARM disk image of a nightly, beside its installer ISO.

One generic disk image per ARM build (scripts/mobile/build-phone-userdata.sh):
the build's aarch64 commit on LumaRoot and an empty LumaBoot that a device kit
writes. It is published once, next to the ISO in media/aarch64/<channel>/, and
the ISO's entry in that channel's signed latest.json names it under
"disk_image", so a page links to latest.disk_image.download_url. The shared
media index carries the entry unchanged: the disk image is never copied.

  arm_disk_image.py unsparse SPARSE RAW
      Android sparse to a raw image (holes for DONT_CARE and zero FILL).
  arm_disk_image.py entry IMAGE BASE_URL GATE_JSON
      The "disk_image" object for an index entry, as JSON. IMAGE.manifest
      (from build-phone-userdata.sh) and IMAGE.sha256 must sit beside IMAGE.
"""
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

NAME = re.compile(r'luma-[a-z]+-[0-9]{8}\.[0-9]+-aarch64-disk\.sparse')
SHA = re.compile(r'[a-f0-9]{64}')
RAW, FILL, DONT_CARE, CRC = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
LAYOUT = {'sector_bytes': 4096, 'boot_label': 'LumaBoot', 'boot_volume_id': '032D-57AD',
          'root_label': 'LumaRoot', 'root_uuid': '03797c05-cf87-4eda-adc4-6ce111baf2d3'}


def unsparse(src, dst):
    with open(src, 'rb') as f, open(dst, 'wb') as out:
        magic, major, _, hdr, chdr, bs, blocks, chunks, _ = struct.unpack('<IHHHHIIII', f.read(28))
        if magic != 0xED26FF3A or major != 1:
            raise SystemExit(f'{src}: not an Android sparse image')
        f.seek(hdr)
        for _ in range(chunks):
            kind, _, count, total = struct.unpack('<HHII', f.read(12))
            f.seek(chdr - 12, 1)
            body, length = total - chdr, count * bs
            if kind == RAW:
                if body != length:
                    raise SystemExit(f'{src}: RAW chunk size differs')
                for left in range(length, 0, -(64 << 20)):
                    out.write(f.read(min(left, 64 << 20)))
            elif kind == FILL:
                value = f.read(4)
                if value == bytes(4):
                    out.seek(length, 1)
                else:
                    out.write(value * (length // 4))
            elif kind == DONT_CARE:
                out.seek(length, 1)
            elif kind == CRC:
                f.seek(body, 1)
            else:
                raise SystemExit(f'{src}: unknown chunk type {kind:#x}')
        out.truncate(blocks * bs)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(16 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def entry(image, base, gate):
    image = Path(image)
    if not NAME.fullmatch(image.name):
        raise SystemExit(f'{image.name}: not a Luma ARM disk image name')
    if not base.startswith('https://') or not base.endswith('/'):
        raise SystemExit('BASE_URL must be an https directory URL')
    recorded = Path(f'{image}.sha256').read_text().split()
    actual = sha256(image)
    if recorded != [actual, image.name]:
        raise SystemExit(f'{image}: does not match its .sha256')
    manifest = dict(line.split('=', 1) for line in Path(f'{image}.manifest').read_text().splitlines()
                    if '=' in line and not line.startswith('origin'))
    gate = json.loads(Path(gate).read_text())
    if gate.get('result') != 'pass' or gate.get('commit') != manifest['commit']:
        raise SystemExit('disk image gate did not pass for this commit')
    manifest_name = image.name[:-len('.sparse')] + '.manifest.txt'
    return {'file': image.name, 'download_file': image.name, 'url': base + image.name,
            'download_url': base + image.name, 'sha256': actual, 'sha256_url': base + image.name + '.sha256',
            'size_bytes': image.stat().st_size, 'format': 'android-sparse', 'manifest_file': manifest_name,
            'manifest_url': base + manifest_name, 'commit': manifest['commit'], 'ref': manifest['ref'],
            'version': manifest['version'], 'layout': LAYOUT, 'gate': gate.get('checks', {})}


def main(argv):
    if argv[:1] == ['unsparse'] and len(argv) == 3:
        unsparse(argv[1], argv[2])
    elif argv[:1] == ['entry'] and len(argv) == 4:
        print(json.dumps(entry(*argv[1:]), indent=2, sort_keys=True))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
