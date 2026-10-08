"""Bounded, on-demand facts and per-capsule settings for Relay's native UI."""
from __future__ import annotations
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from .registry import capsule_root, read_manifest
from .sandbox import sandbox_command
from .errors import RelayError


def bounded_size(root: Path, limit: int = 50000, seconds: float = .4) -> int | None:
    """Never follow application-controlled links or report an incomplete sum."""
    try:
        if root.is_symlink() or not root.is_dir(): return None
        deadline = time.monotonic() + seconds
        total = count = 0
        for parent, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(parent) / name).is_symlink()]
            for name in files:
                count += 1
                if count > limit or time.monotonic() > deadline: return None
                info = (Path(parent) / name).lstat()
                if stat.S_ISREG(info.st_mode): total += info.st_size
        return total
    except OSError: return None


def read_bounded(path: Path, limit: int = 8 * 1024 * 1024) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit: return ''
            raw = stream.read(limit + 1)
            return raw.decode('utf-8', errors='replace') if len(raw) <= limit else ''
    except OSError: return ''


def registry_values(text: str, section: str) -> dict[str, str]:
    values = {}; active = False
    for line in text.splitlines():
        if line.startswith('['):
            active = line.partition(']')[0][1:].replace('\\\\', '\\') == section
        elif active:
            match = re.fullmatch(r'"([^"\n]+)"=(?:"([^"\n]*)"|dword:([0-9a-fA-F]{8}))', line)
            if match: values[match[1]] = match[2] if match[2] is not None else str(int(match[3], 16))
    return values


def installed_components(prefix: Path) -> set[str]:
    # Winetricks itself writes this log after a completed verb.
    return {line.strip() for line in read_bounded(prefix / 'winetricks.log', 1024*1024).splitlines()
            if re.fullmatch('[a-zA-Z0-9_-]{1,80}', line.strip())}


def capsule_facts(app_id: str) -> dict:
    manifest = read_manifest(app_id)
    prefix = capsule_root(app_id) / 'prefix'
    user = read_bounded(prefix / 'user.reg')
    wine = registry_values(user, r'Software\Wine')
    desktop = registry_values(user, r'Control Panel\Desktop')
    overrides = registry_values(user, r'Software\Wine\DllOverrides')
    return {'manifest': manifest, 'prefix': str(prefix), 'bytes': bounded_size(prefix),
            'components': installed_components(prefix), 'windows_version': wine.get('Version'),
            'dpi': desktop.get('LogPixels'), 'overrides': overrides}


def apply_registry_setting(app_id: str, kind: str, value: str) -> None:
    read_manifest(app_id)  # Confirm ownership before constructing a capsule command.
    if kind == 'windows-version':
        if value not in {'win11','win10','win81','win7'}: raise RelayError('Unsupported Windows version.')
        path, field, typ, data = r'HKCU\Software\Wine', 'Version', 'REG_SZ', value
    elif kind == 'scale':
        if value not in {'96','120','144','192'}: raise RelayError('Unsupported display scale.')
        path, field, typ, data = r'HKCU\Control Panel\Desktop', 'LogPixels', 'REG_DWORD', value
    else: raise RelayError('Unsupported runtime setting.')
    command, environment = sandbox_command(capsule_root(app_id),
        ['/usr/bin/wine','reg','add',path,'/v',field,'/t',typ,'/d',data,'/f'], network=False)
    try:
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise RelayError('The application settings could not be updated. Close the app and try again.') from error
    if result.returncode: raise RelayError('Wine could not apply this setting. Close the app and try again.')


def readable_size(value: int | None) -> str:
    if value is None: return 'Size unavailable'
    for unit in ('B','KB','MB','GB'):
        if value < 1000 or unit == 'GB': return f'{value:.1f} {unit}' if unit != 'B' else f'{value} B'
        value /= 1000
    return 'Size unavailable'
