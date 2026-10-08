# SPDX-License-Identifier: Apache-2.0
"""Background app updates, and the ones held for a person (ADR-030, section 7).

Every six hours Depot updates the apps it installed from the Luma remote and
Flathub, unless "Update apps automatically" is off (it is on by default), the
connection is metered, power saving is on, or the computer runs on a low
battery. Running apps are never stopped: Flatpak keeps the version they run
until they are next opened. An app a person took back to its previous version
is paused until a newer version than the one taken back appears
(:mod:`luma_installer.depot_app_history`). An update whose computed
permissions widen is not installed in the background: it waits in the Updates
tab until the person has looked at what changes, Manual or deferred ordinary updates are announced once per build; a successful
background run announces its completed updates together. Permission holds are
announced separately, also once per build.

A person pressing Update on a held release is the review; after that the same
release is no longer held, in the background or anywhere else.

This module decides. It installs nothing and has no GLib.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
import tempfile

INTERVAL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class Pending:
    app_id: str
    name: str
    release: str          # the version, or the commit when there is no version
    widens: bool
    commit: str = ""      # the build the update installs, when known
    installed_commit: str = ""  # the baseline whose permissions were compared


@dataclass(frozen=True)
class Plan:
    install: tuple[str, ...]
    held: tuple[str, ...]
    notify: tuple[Pending, ...]
    skipped_reason: str = ''
    available: tuple[Pending, ...] = ()


def state_path(environment=None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get('XDG_STATE_HOME') or os.path.join(env.get('HOME', str(Path.home())), '.local/state')
    return Path(base) / 'luma/depot/held-updates.json'


def _key(pending: Pending) -> str:
    # Bind permission consent to the exact immutable build, not a publisher's label.
    return 'v3:' + json.dumps([pending.app_id, pending.release, pending.commit, pending.installed_commit], separators=(',', ':'))


def load(environment=None) -> dict:
    try:
        with state_path(environment).open('rb') as stream:
            value = json.loads(stream.read(262144).decode('utf-8'))
    except (OSError, ValueError, UnicodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    for name in ('approved', 'notified', 'available_notified', 'completed_notified'):
        if not isinstance(value.get(name), list):
            value[name] = []
        value[name] = [item for item in value[name] if isinstance(item, str)][-200:]
    return value


def save(state: dict, environment=None) -> None:
    path = state_path(environment)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Bounded: only the most recent releases matter.
    state = {name: state.get(name, [])[-200:] for name in
             ('approved', 'notified', 'available_notified', 'completed_notified')}
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.held')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def approve(pending: Pending, environment=None) -> None:
    if not all(re.fullmatch(r'[0-9a-f]{64}', value or '') for value in (pending.commit, pending.installed_commit)):
        raise ValueError('Refresh this update before approving its permissions.')
    state = load(environment)
    if _key(pending) not in state['approved']:
        state['approved'].append(_key(pending))
    save(state, environment)


def is_held(pending: Pending, state: dict) -> bool:
    return pending.widens and (not all(re.fullmatch(r'[0-9a-f]{64}', value or '')
                                     for value in (pending.commit, pending.installed_commit))
                              or _key(pending) not in state.get('approved', []))


LOW_BATTERY_PERCENT = 30


def plan(pending: list[Pending] | tuple[Pending, ...], state: dict, *, enabled: bool,
         metered: bool = False, power_saver: bool = False, low_battery: bool = False,
         history=None) -> Plan:
    if history is not None:
        from .depot_app_history import holds
        pending = tuple(item for item in pending if not holds(history, item.app_id, item.release, item.commit))
    held = tuple(item.app_id for item in pending if is_held(item, state))
    notify = tuple(item for item in pending if is_held(item, state) and _key(item) not in state['notified'])
    reason = ('Automatic app updates are off.' if not enabled else
              'This connection is metered.' if metered else
              'Power saving is on.' if power_saver else
              'The battery is low.' if low_battery else '')
    available = tuple(item for item in pending if reason and not is_held(item, state)
                      and _key(item) not in state.get('available_notified', []))
    install = () if reason else tuple(item.app_id for item in pending if not is_held(item, state))
    return Plan(install, held, notify, reason, available)


def mark_notified(items, environment=None) -> None:
    state = load(environment)
    for item in items:
        if _key(item) not in state['notified']:
            state['notified'].append(_key(item))
    save(state, environment)


def unannounced(items, kind: str, environment=None) -> tuple[Pending, ...]:
    if kind not in ('available', 'completed'):
        raise ValueError('Unknown app update announcement.')
    known = load(environment)[kind + '_notified']
    return tuple(item for item in items if _key(item) not in known)


def mark_announced(items, kind: str, environment=None) -> None:
    if kind not in ('available', 'completed'):
        raise ValueError('Unknown app update announcement.')
    state = load(environment)
    values = state[kind + '_notified']
    for item in items:
        if _key(item) not in values:
            values.append(_key(item))
    save(state, environment)
