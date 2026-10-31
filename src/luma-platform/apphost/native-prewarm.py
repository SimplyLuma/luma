#!/usr/bin/python3 -sP
# SPDX-License-Identifier: Apache-2.0
"""Prepare a bounded set of native app services once in a handheld session."""
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import sysconfig
import uuid


def available_mb():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) // 1024
    return 0


def main():
    # Device identity comes from the session's capability policy, never pixels.
    from luma_appkit.context import AppContext
    if AppContext.from_environment().device_class != 'handheld' or os.environ.get('XDG_SESSION_CLASS') == 'greeter':
        return 0
    if not os.environ.get('WAYLAND_DISPLAY') or os.environ.get('LD_PRELOAD'):
        return 0
    library = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(sysconfig.get_config_var('LIBDIR')) / 'libluma-app-prepare.so'
    manifest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/usr/share/luma-app-host/native.json')
    if not library.is_file() or not manifest.is_file():
        return 0
    root = Path(os.environ['XDG_RUNTIME_DIR']) / 'luma'
    root.mkdir(mode=0o700, exist_ok=True)
    path = root / ('prepare-' + uuid.uuid4().hex[:12] + '.sock')
    children = []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as ready:
            ready.bind(str(path)); os.chmod(path, 0o600)
            for entry in json.loads(manifest.read_text())[:2]:
                if available_mb() < 1024:
                    break
                name, program, bus = entry['id'], entry['program'], entry['bus']
                if not name.replace('-', '').isascii() or not name.replace('-', '').isalnum() or not Path(program).is_absolute():
                    continue
                if not Path(program).is_file():
                    continue
                query = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
                    '--object-path', '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.NameHasOwner', bus],
                    capture_output=True, text=True, timeout=3)
                if 'true' in query.stdout:
                    continue
                command = ['systemd-run', '--user', '--scope', '--collect', '--quiet',
                    '--unit=app-luma-prepared-' + name, '--property=PartOf=graphical-session.target',
                    '/usr/bin/env', 'LD_PRELOAD=' + str(library), 'LUMA_APP_PREPARE=1',
                    'LUMA_APP_PREPARE_LIBRARY=' + str(library), 'LUMA_APP_PREPARE_NOTIFY=' + str(path),
                    program, '--gapplication-service']
                child = subprocess.Popen(command)
                children.append(child)
                if select.select([ready], [], [], 10)[0]:
                    message = ready.recv(64)
                    if message.isdigit():
                        print(f'Prepared {name}', flush=True)
                else:
                    # A process that did not finish preparing must not keep
                    # boot or a background worker alive indefinitely.
                    child.terminate()
    finally:
        path.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
