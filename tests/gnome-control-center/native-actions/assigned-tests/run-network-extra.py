#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the actual NM adapter using the existing isolated NM test service."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True, help='Settings shell directory')
parser.add_argument('--upstream', type=Path, help='Full Settings source tree containing panels/network and tests/network')
args = parser.parse_args()
source = args.source.resolve()
upstream = (args.upstream or source.parent).resolve()
service = upstream / 'tests/network/nm-utils/test-networkmanager-service.py'
qrcode = upstream / 'panels/network/qrcodegen.c'
for path in (source / 'cc-luma-live-network.c', source / 'cc-luma-fixture.c', service, qrcode):
    if not path.is_file():
        parser.error(f'Required source is missing: {path}')
with tempfile.TemporaryDirectory(prefix='luma-settings-network-extra-') as directory:
    binary = str(Path(directory) / 'test-network-extra')
    command = ['gcc', '-Wall', '-Werror=implicit-function-declaration',
               '-I' + str(source), '-I' + str(upstream),
               str(Path(__file__).resolve().parent / 'test-network-extra.c'),
               str(source / 'cc-luma-live-network.c'), str(source / 'cc-luma-fixture.c'),
               str(qrcode), f'-DTEST_NM_SERVICE="{service}"', '-lm', '-o', binary]
    command += shlex.split(subprocess.check_output(
        ['pkg-config', '--cflags', '--libs', 'libnm', 'json-glib-1.0', 'gio-2.0'], text=True))
    subprocess.run(command, check=True)
    subprocess.run([binary], env=dict(os.environ, G_DEBUG='fatal-warnings'), check=True)
