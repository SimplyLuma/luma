#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch only the hash-pinned tools for Luma's source-owned test APK."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / 'config/android/acceptance-tools.json').read_text())
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, record in manifest['artifacts'].items():
        if name not in ('android.jar', 'aapt2.jar', 'r8.jar'):
            raise ValueError('Unexpected fixture tool name')
        url, size = record['url'], record['size']
        with urllib.request.urlopen(url, timeout=60) as response:
            maximum = (size * 4 + 2) // 3 + 4096 if name == 'android.jar' else size
            data = response.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('Fixture tool exceeded its download bound: ' + name)
        if name == 'android.jar':
            data = base64.b64decode(b''.join(data.split()), validate=True)
        if len(data) != size or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('Fixture tool failed verification: ' + name)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=args.output, delete=False) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, args.output / name)
            temporary = None
        finally:
            if temporary is not None:
                os.unlink(temporary)
        print('Verified ' + name)


if __name__ == '__main__':
    main()
