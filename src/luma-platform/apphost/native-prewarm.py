#!/usr/bin/python3 -sP
# SPDX-License-Identifier: Apache-2.0
"""Prepare a bounded set of native app services once in a handheld session.

On a TV (Posture "tv"), the apps on the TV home are kept prepared instead:
each runs as luma-app-prepared@<app id>.service, loaded with its window
built but not shown (LUMA_APP_PREPARE: libluma-app-prepare.so for C apps,
luma_appkit.prepare for Python ones), so the remote's OK is a present, not
a cold start. A prepared app that is closed is prepared again.
"""
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


def name_owned(bus):
    query = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
        '--object-path', '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.NameHasOwner', bus],
        capture_output=True, text=True, timeout=3)
    return 'true' in query.stdout


def tv_posture():
    """The shell's posture is TV (org.project_luma.ShellState1 Posture)."""
    try:
        query = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.project_luma.ShellState1',
            '--object-path', '/org/project_luma/ShellState1', '--method', 'org.freedesktop.DBus.Properties.Get',
            'org.project_luma.ShellState1', 'Posture'], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    return "<'tv'>" in query.stdout


def valid_app_id(app_id):
    parts = app_id.split('.')
    return len(parts) >= 3 and all(part and part.replace('_', '').replace('-', '').isalnum() and part.isascii()
                                   for part in parts)


def desktop_keys(path):
    """The [Desktop Entry] keys of a desktop file."""
    keys, main = {}, False
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return keys
    for line in lines:
        if line.startswith('['):
            main = line.strip() == '[Desktop Entry]'
        elif main and '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            keys.setdefault(key.strip(), value.strip())
    return keys


def application_dirs():
    """applications/ folders, most important first (user, Flatpak exports, system)."""
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    data_dirs = os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
    return [data_home / 'applications'] + [Path(d) / 'applications' for d in data_dirs.split(':') if d]


def flatpak_deploys(app_id):
    """The app's own deployed desktop files: the user's installation, then the system's."""
    user = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'flatpak'
    return [root / 'app' / app_id / 'current/active/files/share/applications' / f'{app_id}.desktop'
            for root in (user, Path('/var/lib/flatpak'))]


def tv_launcher(app_id, dirs=None, deploys=None):
    """The TV-home launcher's keys for app_id, or None when it is not on the TV home.

    Flatpak's export keeps only the keys on its own allowlist, so the X-Luma-TV
    keys of a Flatpak are read from its deployed desktop file, as the TV home
    reads them (docs/developer-platform/tv-mode.md)."""
    for folder in dirs or application_dirs():
        path = folder / f'{app_id}.desktop'
        if not path.is_file():
            continue
        keys = desktop_keys(path)
        if 'X-Luma-TV' not in keys and keys.get('X-Flatpak') == app_id:
            deployed = next((d for d in (deploys or flatpak_deploys)(app_id) if d.is_file()), None)
            if deployed is not None:
                keys = dict(keys, **{k: v for k, v in desktop_keys(deployed).items() if k.startswith('X-Luma-TV')})
        return keys if keys.get('X-Luma-TV') == 'true' else None
    return None


def tv_apps(manifest, dirs=None):
    """The TV apps to keep prepared: the manifest's order, each one installed
    and on the TV home (X-Luma-TV=true). A launch reaches the prepared app by
    D-Bus activation, or by its own single-instance handoff."""
    try:
        listed = json.loads(Path(manifest).read_text())
    except (OSError, ValueError):
        return []
    return [app for app in listed if isinstance(app, str) and valid_app_id(app) and tv_launcher(app, dirs)]


def prepare_tv_apps(manifest, wait=20, minimum_mb=2048):
    """Start each TV app prepared, one at a time: the next waits until the last
    owns its name (it is loaded), so sign-in is never a stampede."""
    import time
    for app in tv_apps(manifest):
        if available_mb() < minimum_mb:
            break
        if name_owned(app):
            continue
        subprocess.run(['systemctl', '--user', 'start', '--no-block', f'luma-app-prepared@{app}.service'],
                       capture_output=True, timeout=10)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not name_owned(app):
            time.sleep(0.25)
        print(f'Prepared {app}' if name_owned(app) else f'Still preparing {app}', flush=True)
    return 0


def prepared_command(app_id, launchers='/usr/libexec/luma-app-launchers'):
    """The command that runs app_id prepared: its Flatpak, or its own program."""
    keys = tv_launcher(app_id)
    if keys is None or not valid_app_id(app_id):
        return None
    if keys.get('X-Flatpak') == app_id:
        return ['flatpak', 'run', '--env=LUMA_APP_PREPARE=1', app_id, '--gapplication-service']
    program = (keys.get('Exec') or '').split()
    if not program:
        return None
    # A native app launched through the app host (luma-app-launch) is prepared
    # as itself: its own launcher, which the app host would otherwise fork.
    direct = Path(launchers) / Path(program[0]).name
    command = str(direct) if direct.is_file() else program[0]
    return ['/usr/bin/env', 'LUMA_APP_PREPARE=1', command, '--gapplication-service']


def prepared(app_id):
    """luma-app-prepared@.service: exec the app prepared (75: nothing to prepare)."""
    command = prepared_command(app_id)
    if command is None:
        print(f'{app_id} is not a TV app', file=sys.stderr)
        return 75
    os.execvp(command[0], command)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--prepared':
        return prepared(sys.argv[2])
    if len(sys.argv) == 3 and sys.argv[1] == '--wanted':
        # Not already running (opened normally), and still a TV: worth preparing.
        return 0 if tv_posture() and not name_owned(sys.argv[2]) else 1
    # Device identity comes from the session's capability policy, never pixels.
    from luma_appkit.context import AppContext
    if os.environ.get('XDG_SESSION_CLASS') != 'greeter' and os.environ.get('WAYLAND_DISPLAY') and tv_posture():
        return prepare_tv_apps(os.environ.get('LUMA_TV_PREPARE_MANIFEST') or '/usr/share/luma-app-host/tv.json')
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
