# SPDX-License-Identifier: Apache-2.0
"""App and runtime updates that travel with a Luma update.

Luma updates through rpm-ostree; first-party apps are system Flatpaks from
the ``luma`` remote. One update brings both:

``prefetch``  luma-system-app-updates-prefetch.service, started by
              luma-updated once it has staged a Luma update. Downloads the
              pending ``luma`` app and runtime updates of the system
              installation without deploying them (``flatpak update
              --no-deploy``) and records them, with the commits they move
              from and to, for the staged deployment.
``deploy``    luma-system-app-updates.service, at boot before the display
              manager. On the first boot of that deployment, deploys what was
              downloaded from the local repository only (``--no-pull``).
              Booting any other deployment deploys nothing.
``pending``   luma-depot-app-updates-login.service, at sign-in. Starts
              Depot's background app update at once when an app Depot
              updates has an update waiting.

An update that asks for more (a permission added or widened, as Depot judges
it), an app the person took back to its previous version, and an app whose
signed metadata needs a newer host than the booted one are left for Depot,
where the person reviews them. Nothing here changes a remote, a key or a
deployment of the OS, and no failure stops a boot.
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import time

REMOTE = 'luma'
INSTALLATION = Path(os.environ.get('LUMA_SYSTEM_FLATPAK', '/var/lib/flatpak'))
STATE = Path(os.environ.get('LUMA_SYSTEM_APP_UPDATES_STATE', '/var/lib/luma/system-app-updates'))
SCHEMA = 'org.projectluma.system-app-updates/v1'
#: A record older than this belongs to a deployment nobody booted; Depot has long caught up.
KEEP_SECONDS = 30 * 86400
PULL_SECONDS = 3 * 3600
#: The boot unit's own TimeoutStartSec is longer; this one leaves time to record what happened.
DEPLOY_SECONDS = 100
DEPOT_UNIT = 'luma-depot-app-updates.service'

COMMIT = re.compile(r'[0-9a-f]{64}')
REF = re.compile(r'(app|runtime)/[A-Za-z0-9_][A-Za-z0-9_.-]*/(x86_64|aarch64)/[A-Za-z0-9_][A-Za-z0-9_.-]*')


def log(message: str) -> None:
    print(f'luma-system-app-updates: {message}', file=sys.stderr, flush=True)


def _run(runner, argv, timeout=60):
    try:
        result = runner(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)
    if result.returncode:
        return None, (result.stderr or '').strip()[-400:]
    return result.stdout, ''


# ── the OS deployments ──────────────────────────────────────────────────

def _identity(deployment: dict) -> str:
    """A deployment's OS commit: its base when packages are added on top, else its own."""
    value = deployment.get('base-checksum') or deployment.get('checksum') or ''
    return value if isinstance(value, str) and COMMIT.fullmatch(value) else ''


def deployments(runner=subprocess.run) -> list[dict]:
    out, error = _run(runner, ('rpm-ostree', 'status', '--json'), timeout=60)
    if out is None:
        log(f'cannot read the deployments: {error}')
        return []
    try:
        found = json.loads(out).get('deployments', [])
    except (ValueError, AttributeError):
        return []
    return [d for d in found if isinstance(d, dict)]


def staged_identity(found: list[dict]) -> str:
    staged = next((d for d in found if d.get('staged')), None)
    if staged is None and found and not found[0].get('booted'):
        staged = found[0]  # finalized already: the next boot's default
    return _identity(staged) if staged else ''


def booted_identity(found: list[dict]) -> str:
    booted = next((d for d in found if d.get('booted')), None)
    return _identity(booted) if booted else ''


# ── the system installation ────────────────────────────────────────────

def remote_trusted(installation: Path = INSTALLATION) -> bool:
    """The luma remote exists at its compiled address, enabled, with commit and summary signatures checked."""
    from .depot_flatpak import REMOTES
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        parser.read(installation / 'repo/config', encoding='utf-8')
    except (configparser.Error, OSError, UnicodeDecodeError):
        return False
    section = f'remote "{REMOTE}"'
    if not parser.has_section(section):
        return False
    value = dict(parser.items(section))
    return (value.get('url', '') in REMOTES[REMOTE].urls
            and value.get('gpg-verify', 'true').strip().lower() == 'true'
            and value.get('gpg-verify-summary', 'false').strip().lower() == 'true'
            and value.get('xa.disable', 'false').strip().lower() != 'true')


def _read_commit(path: Path) -> str:
    try:
        value = path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeDecodeError):
        return ''
    return value if COMMIT.fullmatch(value) else ''


def installed_commit(ref: str, installation: Path = INSTALLATION) -> str:
    """The deployed commit: the name of the directory ``active`` points to."""
    kind, name, arch, branch = ref.split('/')
    try:
        target = os.readlink(installation / kind / name / arch / branch / 'active')
    except OSError:
        return ''
    return target if COMMIT.fullmatch(target) else ''


def installed_metadata(ref: str, installation: Path = INSTALLATION) -> str:
    kind, name, arch, branch = ref.split('/')
    try:
        return (installation / kind / name / arch / branch / 'active/metadata').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return ''


def local_commit(ref: str, installation: Path = INSTALLATION, runner=subprocess.run) -> str:
    """What the local repository holds for the remote's ref: what ``--no-pull`` deploys."""
    found = _read_commit(installation / 'repo/refs/remotes' / REMOTE / ref)
    if found:
        return found
    out, _error = _run(runner, ('ostree', f'--repo={installation / "repo"}', 'rev-parse', f'{REMOTE}:{ref}'))
    value = (out or '').strip()
    return value if COMMIT.fullmatch(value) else ''


def commit_metadata(commit: str, installation: Path = INSTALLATION, runner=subprocess.run) -> str:
    out, _error = _run(runner, ('ostree', f'--repo={installation / "repo"}', 'cat', commit, '/metadata'))
    return out or ''


def pending_refs(runner=subprocess.run) -> list[str] | None:
    """The system installation's refs with a newer commit on the luma remote; None when it can't be read."""
    out, error = _run(runner, ('flatpak', 'remote-ls', '--system', '--updates', '--columns=ref', REMOTE), timeout=300)
    if out is None:
        log(f'cannot list the updates on {REMOTE}: {error}')
        return None
    return sorted({line.strip() for line in out.splitlines() if REF.fullmatch(line.strip())})


def remote_info(ref: str, what: str, runner=subprocess.run) -> str:
    out, _error = _run(runner, ('flatpak', 'remote-info', '--system', f'--show-{what}', REMOTE, ref), timeout=120)
    return (out or '').strip() if what == 'commit' else (out or '')


# ── what Depot leaves for the person ───────────────────────────────────

def asks_for_more(before: str, after: str, app_id: str) -> bool:
    """Depot's rule: an update that adds or widens a permission waits; one that can't be compared waits too."""
    from . import depot_permissions
    try:
        old = depot_permissions.from_metadata(before, app_id, strict=True)
        new = depot_permissions.from_metadata(after, app_id, strict=True)
    except ValueError:
        return True
    if not before or not after:
        return True
    return depot_permissions.widens(depot_permissions.diff(old, new))


def needs_newer_host(metadata: str) -> bool:
    from .app_host_compatibility import require_host_compatibility
    from .depot_flatpak import SourceUnavailable
    try:
        require_host_compatibility(metadata)
    except SourceUnavailable:
        return True
    return False


def people(passwd=None) -> list[dict]:
    """Each sign-in account's environment, for reading its Depot settings and history."""
    found = []
    for entry in (pwd.getpwall() if passwd is None else passwd):
        if 1000 <= entry.pw_uid < 60000 and entry.pw_dir and os.path.isdir(entry.pw_dir):
            found.append({'HOME': entry.pw_dir})
    return found


def updates_off(environments) -> bool:
    """Someone turned automatic app updates off in Depot: their apps update when they choose."""
    from .depot_counting import load_settings
    return any(not load_settings(environment).app_updates for environment in environments)


def taken_back(environments) -> set[str]:
    """Flatpak ids of apps someone went back to the previous version of (Depot pauses those)."""
    from . import depot_app_history
    paused = set()
    for environment in environments:
        try:
            paused |= set(depot_app_history.load(environment).paused)
        except (OSError, ValueError):
            continue
    if not paused:
        return set()
    try:
        from .depot_catalog import local_catalog
        sources = {'catalog:' + entry.id: entry.source_id for entry in local_catalog().applications
                   if entry.backend == 'flatpak'}
    except Exception:  # noqa: BLE001 - without a catalogue, the ids themselves
        sources = {}
    return {sources.get(app_id, app_id) for app_id in paused}


# ── the record ─────────────────────────────────────────────────────────

def manifest_path(state: Path = STATE) -> Path:
    return state / 'manifest.json'


def load(state: Path = STATE) -> dict | None:
    path = manifest_path(state)
    try:
        if path.is_symlink() or path.stat().st_size > 1048576:
            return None
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(doc, dict) or doc.get('schema') != SCHEMA
            or not COMMIT.fullmatch(str(doc.get('deployment', '')))
            or not isinstance(doc.get('refs'), list)):
        return None
    refs = [r for r in doc['refs'] if isinstance(r, dict) and REF.fullmatch(str(r.get('ref', '')))
            and COMMIT.fullmatch(str(r.get('from', ''))) and COMMIT.fullmatch(str(r.get('to', '')))]
    return {**doc, 'refs': refs}


def save(doc: dict, state: Path = STATE) -> None:
    state.mkdir(parents=True, exist_ok=True, mode=0o755)
    data = (json.dumps(doc, sort_keys=True, indent=2) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.manifest-', dir=state)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, manifest_path(state))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def forget(state: Path = STATE) -> None:
    try:
        manifest_path(state).unlink()
    except FileNotFoundError:
        pass


# ── prefetch ───────────────────────────────────────────────────────────

def prefetch(*, runner=subprocess.run, installation: Path = INSTALLATION, state: Path = STATE,
             environments=None, now=time.time) -> dict:
    found = deployments(runner)
    deployment = staged_identity(found)
    if not deployment or deployment == booted_identity(found):
        forget(state)
        return {'result': 'nothing-staged'}
    if not remote_trusted(installation):
        forget(state)
        return {'result': 'remote-not-trusted'}
    environments = people() if environments is None else environments
    if updates_off(environments):
        forget(state)
        return {'result': 'app-updates-off'}
    refs = pending_refs(runner)
    if refs is None:
        return {'result': 'unavailable'}
    paused = taken_back(environments)
    wanted, held = [], []
    for ref in refs:
        kind, name = ref.split('/')[:2]
        before = installed_commit(ref, installation)
        after = remote_info(ref, 'commit', runner)
        if not before or not COMMIT.fullmatch(after) or before == after:
            continue
        if kind == 'app' and name in paused:
            held.append({'ref': ref, 'reason': 'taken-back'})
            continue
        if kind == 'app' and asks_for_more(installed_metadata(ref, installation),
                                           remote_info(ref, 'metadata', runner), name):
            held.append({'ref': ref, 'reason': 'asks-for-more'})
            continue
        wanted.append({'ref': ref, 'from': before, 'to': after})
    # One ref at a time, recorded as each lands: a restart part-way still brings what arrived.
    # Runtimes first, so an app's new runtime is in place before the app.
    wanted.sort(key=lambda entry: (entry['ref'].startswith('app/'), entry['ref']))
    doc = {'schema': SCHEMA, 'deployment': deployment, 'prepared_unix': int(now()), 'refs': [], 'held': held}
    save(doc, state)
    if wanted:
        log(f'downloading {len(wanted)} update(s) for the staged Luma update')
    for entry in wanted:
        _out, error = _run(runner, ('flatpak', 'update', '--system', '--noninteractive', '--assumeyes',
                                    '--no-deploy', entry['ref']), timeout=PULL_SECONDS)
        if error:
            log(f'{entry["ref"]} was not downloaded: {error}')
        if local_commit(entry['ref'], installation, runner) == entry['to']:
            doc['refs'].append(entry)
            save(doc, state)
    ready = doc['refs']
    return {'result': 'prepared', 'refs': len(ready), 'held': len(held), 'missed': len(wanted) - len(ready)}


# ── deploy (boot) ──────────────────────────────────────────────────────

def deploy(*, runner=subprocess.run, installation: Path = INSTALLATION, state: Path = STATE, now=time.time) -> dict:
    doc = load(state)
    if doc is None:
        forget(state)
        return {'result': 'none'}
    found = deployments(runner)
    booted = booted_identity(found)
    if not booted:
        return {'result': 'unknown-deployment'}  # rpm-ostree didn't answer: try again next boot
    if booted != doc['deployment']:
        # Another deployment booted: the previous one chosen for this boot, or a rollback.
        # Nothing is deployed. The record waits while its deployment can still boot.
        alive = any(_identity(d) == doc['deployment'] for d in found)
        if not alive or now() - int(doc.get('prepared_unix', 0) or 0) > KEEP_SECONDS:
            forget(state)
            return {'result': 'dropped'}
        return {'result': 'waiting'}
    ready, skipped = [], []
    for entry in doc['refs']:
        ref = entry['ref']
        if installed_commit(ref, installation) != entry['from']:
            skipped.append({'ref': ref, 'reason': 'changed'})  # Depot updated it, or it went away
            continue
        if local_commit(ref, installation, runner) != entry['to']:
            skipped.append({'ref': ref, 'reason': 'not-downloaded'})
            continue
        if ref.startswith('app/'):
            metadata = commit_metadata(entry['to'], installation, runner)
            name = ref.split('/')[1]
            if asks_for_more(installed_metadata(ref, installation), metadata, name):
                skipped.append({'ref': ref, 'reason': 'asks-for-more'})
                continue
            if needs_newer_host(metadata):
                skipped.append({'ref': ref, 'reason': 'needs-newer-host'})
                continue
        ready.append(entry)
    # One attempt per deployment: whatever is left, Depot updates in the session.
    forget(state)
    if not ready:
        return {'result': 'nothing-to-deploy', 'skipped': skipped}
    log(f'deploying {len(ready)} update(s) downloaded with this Luma update')
    _out, error = _run(runner, ('flatpak', 'update', '--system', '--noninteractive', '--assumeyes', '--no-pull',
                                *(entry['ref'] for entry in ready)), timeout=DEPLOY_SECONDS)
    if error:
        log(f'not everything was deployed: {error}')
    deployed = [entry['ref'] for entry in ready if installed_commit(entry['ref'], installation) == entry['to']]
    return {'result': 'deployed', 'deployed': deployed,
            'failed': [entry['ref'] for entry in ready if entry['ref'] not in deployed], 'skipped': skipped}


# ── at sign-in ─────────────────────────────────────────────────────────

def _remote_configured(scope: str, runner) -> bool:
    out, _error = _run(runner, ('flatpak', 'remotes', scope, '--columns=name'))
    return out is not None and REMOTE in {line.strip() for line in out.splitlines()}


def waiting_for_depot(runner=subprocess.run) -> list[str] | None:
    """Apps Depot updates that have an update on the luma remote; None when it can't be read (offline)."""
    from .depot_flatpak import _catalogued
    managed = {source for source, remote, _branch in _catalogued().values() if remote == REMOTE}
    waiting, readable = set(), False
    for scope in ('--system', '--user'):
        if not _remote_configured(scope, runner):
            continue
        out, _error = _run(runner, ('flatpak', 'remote-ls', scope, '--updates', '--app', '--columns=application',
                                    REMOTE), timeout=120)
        if out is None:
            continue
        readable = True
        waiting |= {line.strip() for line in out.splitlines() if line.strip() in managed}
    return sorted(waiting) if readable else None


def pending(*, runner=subprocess.run, attempts=8, wait=15, sleep=time.sleep) -> dict:
    for attempt in range(attempts):
        waiting = waiting_for_depot(runner)
        if waiting is not None:
            break
        if attempt + 1 < attempts:
            sleep(wait)  # the network often comes up a little after sign-in
    else:
        return {'result': 'unavailable'}
    if not waiting:
        return {'result': 'up-to-date'}
    _out, error = _run(runner, ('systemctl', '--user', 'start', '--no-block', DEPOT_UNIT))
    return {'result': 'started' if not error else 'not-started', 'apps': waiting}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else ''
    actions = {'prefetch': prefetch, 'deploy': deploy, 'pending': pending}
    if action not in actions:
        print('usage: luma-system-app-updates prefetch|deploy|pending', file=sys.stderr)
        return 2
    try:
        outcome = actions[action]()
    except Exception as error:  # noqa: BLE001 - never fail a boot or a sign-in over app updates
        log(f'{action} did not finish: {type(error).__name__}: {error}')
        return 0
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
