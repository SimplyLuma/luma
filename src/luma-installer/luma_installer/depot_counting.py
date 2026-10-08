# SPDX-License-Identifier: Apache-2.0
"""Counting without tracking (ADR-028, section 9).

Two things leave this computer, and only when their switch in Depot's settings
is on (both are on by default):

* **An install event** after an install, update or removal finishes:
  ``{app_id, version, arch, kind}``. No device id, no account, no cookie, no
  token, and the user agent names only Depot.
* **A weekly count** on the catalogue fetch, using Fedora's countme scheme:
  once per calendar week the request carries ``countme=<bucket>``, where the
  bucket says only roughly how long this installation has existed (1: its
  first week, 2: its first month, 3: its first six months, 4: longer). The
  state kept to do this is two week numbers on this computer. It never leaves.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request

EVENTS_URL = os.environ.get('LUMA_DEPOT_EVENTS_URL', 'https://hub.simplyluma.com/api/depot/events')
EVENT_KINDS = ('install', 'update', 'remove')
EVENT_TIMEOUT = 10
USER_AGENT = 'Luma-Depot/4'

# libdnf's constants (libdnf/repo/Repo.cpp), so the buckets mean what Fedora's
# do: windows are weeks that start on a Monday at 00:00 UTC.
COUNTME_OFFSET = 345600          # 1970-01-05 00:00:00 UTC, a Monday
COUNTME_WINDOW = 7 * 24 * 60 * 60
COUNTME_BUCKETS = (2, 5, 25)     # window steps: < 2 weeks, < 5, < 25, then older

_APP_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z')
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+~_-]{0,63}\Z')


def _config_home(environment=None) -> Path:
    env = os.environ if environment is None else environment
    return Path(env.get('XDG_CONFIG_HOME') or Path(env.get('HOME', str(Path.home()))) / '.config')


def _state_home(environment=None) -> Path:
    env = os.environ if environment is None else environment
    return Path(env.get('XDG_STATE_HOME') or Path(env.get('HOME', str(Path.home()))) / '.local/state')


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict:
    try:
        with path.open('rb') as stream:
            value = json.loads(stream.read(65536).decode('utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


# ── Settings ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    """Depot's own switches. Every one defaults on and is the person's to turn off."""

    install_events: bool = True
    countme: bool = True
    #: ADR-030 section 7: update apps in the background every six hours.
    app_updates: bool = True


def settings_path(environment=None) -> Path:
    return _config_home(environment) / 'luma/depot/settings.json'


def load_settings(environment=None) -> Settings:
    value = _read_json(settings_path(environment))
    return Settings(
        install_events=value.get('install_events', True) is not False,
        countme=value.get('countme', True) is not False,
        app_updates=value.get('app_updates', True) is not False)


def save_settings(settings: Settings, environment=None) -> None:
    _write_json(settings_path(environment), {
        'install_events': bool(settings.install_events), 'countme': bool(settings.countme),
        'app_updates': bool(settings.app_updates)})


# ── Weekly count ─────────────────────────────────────────────────────────

def window_start(now: float) -> int:
    delta = int(now) - COUNTME_OFFSET
    return delta - (delta % COUNTME_WINDOW) + COUNTME_OFFSET


def bucket(first_window: int, current_window: int) -> int:
    step = max(0, (current_window - first_window) // COUNTME_WINDOW)
    for index, limit in enumerate(COUNTME_BUCKETS):
        if step < limit:
            return index + 1
    return len(COUNTME_BUCKETS) + 1


class Countme:
    """Once-a-week bucket for the catalogue request. Nothing identifying is kept."""

    def __init__(self, *, environment=None, clock=time.time, settings=None) -> None:
        self.path = _state_home(environment) / 'luma/depot/countme.json'
        self.clock = clock
        self.environment = environment
        self._settings = settings

    def _enabled(self) -> bool:
        settings = self._settings if self._settings is not None else load_settings(self.environment)
        return settings.countme

    def pending(self) -> int | None:
        """The bucket to send with this request, or None if this week is counted."""
        if not self._enabled():
            return None
        state = _read_json(self.path)
        current = window_start(self.clock())
        counted = state.get('counted_window')
        if type(counted) is int and counted >= current:
            return None
        first = state.get('first_window')
        if type(first) is not int or first > current:
            first = current
        return bucket(first, current)

    def counted(self) -> None:
        state = _read_json(self.path)
        current = window_start(self.clock())
        first = state.get('first_window')
        if type(first) is not int or first > current:
            first = current
        try:
            _write_json(self.path, {'first_window': first, 'counted_window': current})
        except OSError:
            pass


# ── Install events ───────────────────────────────────────────────────────

def event_payload(app_id: str, version: str, arch: str, kind: str) -> dict:
    if kind not in EVENT_KINDS:
        raise ValueError('Unknown install event kind')
    if not _APP_ID.fullmatch(app_id or ''):
        raise ValueError('Invalid application id')
    if arch not in ('x86_64', 'aarch64'):
        raise ValueError('Unsupported architecture')
    version = version if _VERSION.fullmatch(version or '') else ''
    return {'app_id': app_id, 'version': version, 'arch': arch, 'kind': kind}


def send_install_event(app_id: str, version: str, arch: str, kind: str, *,
                       settings: Settings | None = None, url: str | None = None,
                       opener=urllib.request.urlopen) -> bool:
    """Best effort: an event that cannot be sent is dropped, never retried or queued."""
    settings = settings if settings is not None else load_settings()
    if not settings.install_events:
        return False
    try:
        payload = event_payload(app_id, version, arch, kind)
    except ValueError:
        return False
    request = urllib.request.Request(
        url or EVENTS_URL, data=json.dumps(payload).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
    try:
        with opener(request, timeout=EVENT_TIMEOUT) as response:
            response.read(1024)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False
