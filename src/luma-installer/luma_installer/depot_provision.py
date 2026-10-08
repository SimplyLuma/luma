# SPDX-License-Identifier: Apache-2.0
"""First-boot app provisioning (ADR-028, section 14).

The installer records the person's choice -- collection ids and application
ids, nothing else -- in ``/etc/luma/first-boot-apps.json``. On the first
graphical login, ``luma-depot-provision.service`` asks Depot to install those
applications through Depot's own install path, so progress is visible in Depot
and every source check an install from the window gets applies here too.

This module is the state machine only: what to install, what is done, when to
try again. It installs nothing itself; the caller passes the install function.
It is written to be interrupted at any moment -- a logout, a crash, a reboot
halfway through a download -- and to pick up where it was, never installing
anything twice and never blocking the session it runs in.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

PLAN_PATH = Path(os.environ.get('LUMA_FIRST_BOOT_APPS', '/etc/luma/first-boot-apps.json'))
PLAN_MAX_BYTES = 64 * 1024
MAX_ITEMS = 200
#: Seconds to wait before attempt n+1 after n failures. The last value repeats.
BACKOFF = (30, 60, 120, 300, 600, 1800)
MAX_ATTEMPTS = 8
#: Logins that may retry what failed before provisioning stops asking. An
#: application that never becomes available must not cost every future login.
MAX_RUNS = 14
_ID = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')

PENDING, INSTALLING, INSTALLED, FAILED, UNAVAILABLE = (
    'pending', 'installing', 'installed', 'failed', 'unavailable')


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Plan:
    collections: tuple[str, ...]
    applications: tuple[str, ...]
    digest: str


def state_directory(environment=None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get('XDG_STATE_HOME') or os.path.join(env.get('HOME', str(Path.home())), '.local/state')
    return Path(base) / 'luma/depot'


def done_marker(environment=None) -> Path:
    return state_directory(environment) / 'first-boot-done'


def read_plan(path: Path | None = None) -> Plan:
    path = Path(path or PLAN_PATH)
    try:
        with path.open('rb') as stream:
            content = stream.read(PLAN_MAX_BYTES + 1)
    except FileNotFoundError:
        raise PlanError('No apps were chosen during installation.') from None
    except OSError as error:
        raise PlanError('The first-boot app list cannot be read.') from error
    if len(content) > PLAN_MAX_BYTES:
        raise PlanError('The first-boot app list is too large.')
    try:
        value = json.loads(content.decode('utf-8'))
    except (UnicodeError, ValueError) as error:
        raise PlanError('The first-boot app list is not valid JSON.') from error
    if not isinstance(value, dict):
        raise PlanError('The first-boot app list is not an object.')
    lists = []
    for name in ('collections', 'applications'):
        items = value.get(name, [])
        if (not isinstance(items, list) or len(items) > MAX_ITEMS
                or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in items)):
            raise PlanError(f'The first-boot {name} are not valid.')
        lists.append(tuple(dict.fromkeys(items)))
    return Plan(lists[0], lists[1], hashlib.sha256(content).hexdigest())


def resolve(plan: Plan, catalog) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Catalogue ids to install in order, and chosen ids the catalogue lacks."""
    known = {entry.id for entry in catalog.applications}
    collections = {collection.id: collection for collection in getattr(catalog, 'collections', ())}
    wanted = []
    missing = []
    for identifier in plan.collections:
        collection = collections.get(identifier)
        if collection is None:
            missing.append('collection:' + identifier)
            continue
        wanted.extend(collection.applications)
    wanted.extend(plan.applications)
    ordered = tuple(dict.fromkeys(wanted))
    missing.extend(identifier for identifier in ordered if identifier not in known)
    return tuple(identifier for identifier in ordered if identifier in known), tuple(missing)


class Provisioner:
    """Decides the next step. The caller performs it and reports back.

    ``is_installed(app_id)`` answers whether a catalogue application is already
    on this computer, so a person who installed an app by hand before the
    service got to it is not asked to download it again.
    """

    def __init__(self, plan: Plan, catalog, *, environment=None, clock=time.time,
                 is_installed=lambda _app_id: False) -> None:
        self.plan = plan
        self.catalog = catalog
        self.environment = environment
        self.clock = clock
        self.is_installed = is_installed
        self.path = state_directory(environment) / 'first-boot.json'
        self.state = self._load()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            with self.path.open('rb') as stream:
                value = json.loads(stream.read(PLAN_MAX_BYTES * 4).decode('utf-8'))
        except (OSError, ValueError, UnicodeError):
            value = {}
        if not isinstance(value, dict) or value.get('plan') != self.plan.digest \
                or not isinstance(value.get('apps'), dict):
            value = {'version': 1, 'plan': self.plan.digest, 'apps': {}}
        # A new run: an install that was in flight when the session ended did
        # not finish, and what failed last time gets a fresh set of attempts.
        value['runs'] = int(value.get('runs', 0)) + 1 if type(value.get('runs', 0)) is int else 1
        for record in value['apps'].values():
            if isinstance(record, dict) and record.get('state') in (INSTALLING, FAILED):
                record.update(state=PENDING, attempts=0, next_attempt=0)
        ordered, missing = resolve(self.plan, self.catalog)
        apps = value['apps']
        for identifier in ordered:
            record = apps.get(identifier)
            if not isinstance(record, dict) or record.get('state') not in (
                    PENDING, INSTALLED, FAILED, UNAVAILABLE):
                apps[identifier] = {'state': PENDING, 'attempts': 0, 'next_attempt': 0}
            elif record['state'] == UNAVAILABLE:
                # It is in the catalogue now; try it.
                apps[identifier] = {'state': PENDING, 'attempts': 0, 'next_attempt': 0}
        for identifier in missing:
            if not isinstance(apps.get(identifier), dict) or apps[identifier].get('state') != INSTALLED:
                apps[identifier] = {'state': UNAVAILABLE, 'attempts': 0, 'next_attempt': 0}
        value['order'] = list(ordered)
        return value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(self.path.parent), prefix='.first-boot')
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                json.dump(self.state, stream, indent=2, sort_keys=True)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    # ── Decisions ────────────────────────────────────────────────────────

    def record(self, app_id: str) -> dict:
        return self.state['apps'][app_id]

    def next_action(self):
        """('install', app_id), ('wait', seconds), or ('finished', summary)."""
        now = self.clock()
        soonest = None
        for app_id in self.state['order']:
            record = self.record(app_id)
            if record['state'] != PENDING:
                continue
            if self.is_installed(app_id):
                record.update(state=INSTALLED, last_error='')
                self.save()
                continue
            if record.get('next_attempt', 0) <= now:
                return ('install', app_id)
            soonest = min(soonest or record['next_attempt'], record['next_attempt'])
        if soonest is not None:
            return ('wait', max(1, int(soonest - now)))
        return ('finished', self.summary())

    def started(self, app_id: str) -> None:
        self.record(app_id).update(state=INSTALLING)
        self.save()

    def succeeded(self, app_id: str) -> None:
        self.record(app_id).update(state=INSTALLED, last_error='')
        self.save()

    def failed(self, app_id: str, error: str, *, permanent: bool = False) -> None:
        record = self.record(app_id)
        attempts = int(record.get('attempts', 0)) + 1
        delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
        exhausted = permanent or attempts >= MAX_ATTEMPTS
        record.update(state=FAILED if exhausted else PENDING, attempts=attempts,
                      next_attempt=0 if exhausted else self.clock() + delay,
                      last_error=str(error)[:500])
        self.save()

    def summary(self) -> dict:
        counts = {INSTALLED: [], FAILED: [], UNAVAILABLE: [], PENDING: []}
        for app_id, record in self.state['apps'].items():
            counts.setdefault(record.get('state', PENDING), []).append(app_id)
        return {key: sorted(value) for key, value in counts.items()}

    def finish(self) -> bool:
        """Mark provisioning done when nothing is left to try. Returns whether it is."""
        summary = self.summary()
        complete = not summary[PENDING] and not summary.get(INSTALLING)
        clean = not summary[FAILED] and not summary[UNAVAILABLE]
        if complete and (clean or self.state.get('runs', 1) >= MAX_RUNS):
            marker = done_marker(self.environment)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(self.plan.digest + '\n', encoding='utf-8')
            return True
        return False


def already_done(environment=None) -> bool:
    return done_marker(environment).is_file()
