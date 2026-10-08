# SPDX-License-Identifier: Apache-2.0
"""Install, update and remove apps from their publisher's official channel.

Runs inside ``/usr/libexec/luma-installer-system`` as root, reached only
through pkexec and the ``org.projectluma.application-installer.system`` polkit
action -- the same boundary, prompt and lock as every other system change
Depot makes. Commands take a catalogue id and nothing else:

``snap-install ID`` / ``snap-refresh ID`` / ``snap-remove ID``
    The listing's ``sources.snap`` through snapd's REST API. Before installing,
    the Snap Store is asked who publishes the snap now: the publisher must be
    the one the catalogue pins and the store must have verified it, and the
    snap must be strictly confined. A classic (unconfined) snap is refused.

``repo-install ID`` / ``repo-remove ID``
    The listing's ``sources.rpm_repository``. For a publisher repository the
    signing key is downloaded over HTTPS and accepted only when every primary
    key in it has a fingerprint the catalogue pins; it is written, armored, to
    ``/etc/pki/rpm-gpg/luma-depot-ID.asc`` and the repository to
    ``/etc/yum.repos.d/luma-depot-ID.repo`` with ``gpgcheck=1``. The package
    is then added to the system image with rpm-ostree and applied to the
    running system (ADR-038), exactly as ``sudo dnf install`` does on Luma, so
    it is updated with Luma's own updates from then on. Fedora's repositories
    are already configured and trusted; a Fedora listing names only a package.

The catalogue is the newest of the root-owned seed and the invoking person's
cached copy once its minisign signature verifies (see system_overrides): a
listing's addresses, keys and publisher are signed data, never arguments.
Progress is reported as ``progress: <fraction> <text>`` lines, refusals as
``refused: <sentence>`` (exit 3) and failures as ``error: <sentence>`` (exit 1).
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import urllib.request

from . import depot_catalog
from .system_overrides import Refused, trusted_catalogs, _moment

SNAPD_SOCKET = '/run/snapd.socket'
SNAP = '/usr/bin/snap'
SYSTEMCTL = '/usr/bin/systemctl'
RPM_OSTREE = '/usr/bin/rpm-ostree'
RPM = '/usr/bin/rpm'
GPG = '/usr/bin/gpg'
REPOS_DIRECTORY = Path('/etc/yum.repos.d')
KEYS_DIRECTORY = Path('/etc/pki/rpm-gpg')
KEY_LIMIT = 256 * 1024
SNAP_TIMEOUT = 45 * 60
ARCHITECTURES = {'x86_64': 'amd64', 'aarch64': 'arm64'}


def say(fraction: float, text: str) -> None:
    print(f'progress: {max(0.0, min(1.0, fraction)):.2f} {text}', flush=True)


def machine() -> str:
    return os.uname().machine


def listing(identifier: str, invoking_uid: int | None, *, catalogs=None):
    """The newest trusted catalogue's entry for ``identifier``."""
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', identifier or ''):
        raise Refused('That is not an app Depot lists.')
    catalogs = trusted_catalogs(invoking_uid) if catalogs is None else catalogs
    dated = sorted((c for c in catalogs if c.schema_version == 4 and _moment(c) is not None),
                   key=_moment, reverse=True)
    for catalog in dated[:1]:
        entry = next((e for e in catalog.applications if e.id == identifier), None)
        if entry is not None:
            if entry.architectures and machine() not in entry.architectures:
                raise Refused(f'{entry.name} is not published for this computer’s processor.')
            return entry
    raise Refused('Depot’s catalogue does not list that app.')


# ── snapd ────────────────────────────────────────────────────────────────

class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 60):
        super().__init__('localhost', timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class Snapd:
    """snapd's REST API over its root socket. ``request`` is replaceable in tests."""

    def __init__(self, path: str = SNAPD_SOCKET):
        self.path = path

    def request(self, method: str, target: str, body: dict | None = None) -> dict:
        connection = _UnixConnection(self.path)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            connection.request(method, target, body=payload,
                               headers={'Content-Type': 'application/json'} if payload else {})
            response = connection.getresponse()
            document = json.loads(response.read() or b'{}')
        finally:
            connection.close()
        if document.get('type') == 'error':
            result = document.get('result') or {}
            raise RuntimeError(result.get('message') or f'snapd answered {document.get("status-code")}')
        return document

    def ensure_running(self) -> None:
        if not Path(self.path).exists():
            subprocess.run([SYSTEMCTL, 'enable', '--now', 'snapd.socket'], check=True,
                           capture_output=True, text=True, timeout=120)
        for _ in range(60):
            try:
                self.request('GET', '/v2/system-info')
                return
            except (OSError, RuntimeError, ValueError):
                time.sleep(1)
        raise RuntimeError('The snap service did not start.')

    def wait_seeded(self, *, timeout=300) -> None:
        """snapd refuses installs until its first-boot seeding finished."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = self.request('GET', '/v2/snaps/system/conf?keys=seed.loaded').get('result') or {}
                if result.get('seed.loaded'):
                    return
            except (OSError, RuntimeError, ValueError):
                pass
            say(0.03, 'Setting up snaps')
            time.sleep(2)
        raise RuntimeError('The snap service is still setting itself up. Try again in a minute.')

    def connect(self, name: str, plugs) -> list[str]:
        """Connect ``plugs`` of snap ``name`` to the system; the ones that failed."""
        failed = []
        for plug in plugs:
            try:
                change = self.request('POST', '/v2/interfaces', {
                    'action': 'connect', 'plugs': [{'snap': name, 'plug': plug}],
                    'slots': [{'snap': 'snapd', 'slot': plug}]}).get('change')
                if change:
                    self.wait(change, 'Allowing what it needs')
            except (OSError, RuntimeError, ValueError):
                failed.append(plug)
        return failed

    def installed(self, name: str) -> dict | None:
        try:
            return self.request('GET', f'/v2/snaps/{name}').get('result')
        except RuntimeError:
            return None

    def store_info(self, name: str) -> dict:
        results = self.request('GET', f'/v2/find?name={name}').get('result') or []
        found = next((item for item in results if item.get('name') == name), None)
        if found is None:
            raise Refused('The Snap Store no longer offers this app.')
        return found

    def wait(self, change: str, label: str, *, clock=time.monotonic, sleep=time.sleep) -> None:
        started = clock()
        while True:
            document = self.request('GET', f'/v2/changes/{change}').get('result') or {}
            tasks = document.get('tasks') or []
            done = sum((task.get('progress') or {}).get('done', 0) for task in tasks)
            total = sum((task.get('progress') or {}).get('total', 0) for task in tasks) or 1
            current = next((task for task in tasks if task.get('status') == 'Doing'), None)
            text = label
            if current is not None and 'download' in (current.get('summary') or '').lower():
                text = 'Downloading'
            say(0.05 + 0.9 * done / total, text)
            if document.get('ready'):
                if document.get('status') != 'Done':
                    raise RuntimeError(document.get('err') or 'The snap service could not finish.')
                return
            if clock() - started > SNAP_TIMEOUT:
                raise RuntimeError('The snap service took too long.')
            sleep(0.5)


def check_snap_publisher(entry, info: dict) -> None:
    """The store must still name the pinned, verified publisher and strict confinement."""
    publisher = info.get('publisher') or {}
    if publisher.get('username') != entry.snap.publisher:
        raise Refused(f'{entry.name} on the Snap Store is no longer published by '
                      f'{entry.channel.publisher if entry.channel and entry.channel.publisher else entry.snap.publisher}, '
                      'so Depot will not install it.')
    if publisher.get('validation') not in ('verified', 'starred'):
        raise Refused(f'The Snap Store has not verified who publishes {entry.name}.')
    if info.get('confinement') != 'strict':
        raise Refused(f'{entry.name} is not a sandboxed snap, so Depot will not install it.')


def snap_install(identifier: str, invoking_uid: int | None, *, snapd=None, catalogs=None) -> str:
    entry = listing(identifier, invoking_uid, catalogs=catalogs)
    if entry.snap is None:
        raise Refused(f'{entry.name} is not a snap.')
    snapd = snapd or Snapd()
    say(0.02, 'Preparing')
    snapd.ensure_running()
    if hasattr(snapd, 'wait_seeded'):
        snapd.wait_seeded()
    if snapd.installed(entry.snap.name) is not None:
        return 'unchanged'
    check_snap_publisher(entry, snapd.store_info(entry.snap.name))
    change = snapd.request('POST', f'/v2/snaps/{entry.snap.name}',
                           {'action': 'install', 'channel': entry.snap.channel}).get('change')
    snapd.wait(change, 'Installing')
    if entry.snap.connect and hasattr(snapd, 'connect'):
        # Reviewed in the signed catalogue: what the snap needs to work that
        # its store does not connect by itself (a VPN's network control).
        missing = snapd.connect(entry.snap.name, entry.snap.connect)
        if missing:
            print(f'note: could not connect {", ".join(missing)}', flush=True)
    installed = snapd.installed(entry.snap.name) or {}
    if (installed.get('publisher') or {}).get('username') != entry.snap.publisher:
        # Belt and braces: what landed must be the publisher's own.
        remove = snapd.request('POST', f'/v2/snaps/{entry.snap.name}', {'action': 'remove'}).get('change')
        snapd.wait(remove, 'Removing')
        raise Refused('The installed snap did not come from its publisher; Depot removed it again.')
    return 'installed'


def snap_refresh(identifier: str, invoking_uid: int | None, *, snapd=None, catalogs=None) -> str:
    entry = listing(identifier, invoking_uid, catalogs=catalogs)
    if entry.snap is None:
        raise Refused(f'{entry.name} is not a snap.')
    snapd = snapd or Snapd()
    snapd.ensure_running()
    if snapd.installed(entry.snap.name) is None:
        raise Refused(f'{entry.name} is not installed.')
    check_snap_publisher(entry, snapd.store_info(entry.snap.name))
    try:
        change = snapd.request('POST', f'/v2/snaps/{entry.snap.name}', {'action': 'refresh'}).get('change')
    except RuntimeError as error:
        # snapd answers a refresh with nothing newer as an error; for a
        # person it is simply up to date.
        if 'no updates available' in str(error).lower():
            return 'unchanged'
        raise
    if not change:
        return 'unchanged'
    snapd.wait(change, 'Updating')
    return 'updated'


def snap_remove(identifier: str, invoking_uid: int | None, *, snapd=None, catalogs=None) -> str:
    entry = listing(identifier, invoking_uid, catalogs=catalogs)
    if entry.snap is None:
        raise Refused(f'{entry.name} is not a snap.')
    snapd = snapd or Snapd()
    snapd.ensure_running()
    if snapd.installed(entry.snap.name) is None:
        return 'unchanged'
    change = snapd.request('POST', f'/v2/snaps/{entry.snap.name}', {'action': 'remove'}).get('change')
    snapd.wait(change, 'Removing')
    return 'removed'


# ── Publisher RPM repositories ───────────────────────────────────────────

def expand(address: str) -> str:
    release = '44'
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if line.startswith('VERSION_ID='):
                release = line.split('=', 1)[1].strip().strip('"').split('.')[0] or release
    except OSError:
        pass
    # Luma follows Fedora's release numbering for its package base.
    try:
        result = subprocess.run([RPM, '-E', '%fedora'], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip().isdigit():
            release = result.stdout.strip()
    except OSError:
        pass
    return address.replace('$basearch', machine()).replace('$releasever', release)


def download_key(address: str, *, opener=urllib.request.urlopen) -> bytes:
    if not address.startswith('https://'):
        raise Refused('The publisher’s key must come over HTTPS.')
    request = urllib.request.Request(address, headers={'User-Agent': 'Luma-Depot/4'})
    with opener(request, timeout=60) as response:
        content = response.read(KEY_LIMIT + 1)
    if len(content) > KEY_LIMIT or not content:
        raise RuntimeError('The publisher’s signing key could not be read.')
    return content


def pinned_key(content: bytes, fingerprints, *, gpg=GPG) -> bytes:
    """The key, armored, when every primary key in it is one the catalogue pins."""
    pinned = {value.upper() for value in fingerprints}
    with tempfile.TemporaryDirectory(prefix='luma-depot-key-') as home:
        os.chmod(home, 0o700)
        environment = {'GNUPGHOME': home, 'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
        imported = subprocess.run([gpg, '--batch', '--quiet', '--import'], input=content,
                                  capture_output=True, env=environment, timeout=60)
        if imported.returncode != 0:
            raise RuntimeError('The publisher’s signing key could not be read.')
        listing_ = subprocess.run([gpg, '--batch', '--with-colons', '--fingerprint', '--list-keys'],
                                  capture_output=True, text=True, env=environment, timeout=60)
        primaries, expect = [], False
        for line in listing_.stdout.splitlines():
            fields = line.split(':')
            if fields[0] == 'pub':
                expect = True
            elif fields[0] == 'fpr' and expect:
                primaries.append(fields[9].upper())
                expect = False
            elif fields[0] == 'sub':
                expect = False
        if not primaries or any(value not in pinned for value in primaries):
            raise Refused('The publisher’s signing key is not the one Luma has on record, '
                          'so nothing was installed.')
        armored = subprocess.run([gpg, '--batch', '--armor', '--export', *primaries],
                                 capture_output=True, env=environment, timeout=60)
        if armored.returncode != 0 or b'BEGIN PGP PUBLIC KEY BLOCK' not in armored.stdout:
            raise RuntimeError('The publisher’s signing key could not be read.')
        return armored.stdout


def repo_text(entry) -> str:
    source = entry.rpm_repository
    return '\n'.join([
        f'# Added by Depot for {entry.name} from the publisher’s own repository.',
        '# Its key was checked against the fingerprint in Luma’s signed catalogue.',
        f'[luma-depot-{source.id}]',
        f'name={source.name} (added by Depot)',
        f'baseurl={source.baseurl}',
        'enabled=1',
        'gpgcheck=1',
        f'repo_gpgcheck={1 if source.repo_gpgcheck else 0}',
        f'gpgkey=file://{KEYS_DIRECTORY}/luma-depot-{source.id}.asc',
        '',
    ])


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)
    os.replace(temporary, path)


def _rpm_installed(package: str, runner=subprocess.run) -> bool:
    return runner([RPM, '-q', '--', package], capture_output=True, text=True, timeout=60).returncode == 0


def _last_line(result) -> str:
    text = (result.stderr or '') + (result.stdout or '')
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), '')


def repo_install(identifier: str, invoking_uid: int | None, *, runner=subprocess.run, catalogs=None,
                 fetch_key=download_key, check_key=pinned_key, apply=None,
                 repos=REPOS_DIRECTORY, keys=KEYS_DIRECTORY) -> str:
    entry = listing(identifier, invoking_uid, catalogs=catalogs)
    source = entry.rpm_repository
    if source is None:
        raise Refused(f'{entry.name} does not come from a package repository.')
    say(0.02, 'Preparing')
    if _rpm_installed(source.package, runner):
        return 'unchanged'
    arguments = [RPM_OSTREE, 'install', '--idempotent', source.package]
    if source.id != 'fedora':
        say(0.08, 'Checking the publisher’s key')
        armored = check_key(fetch_key(expand(source.gpgkey)), source.fingerprints)
        _write(keys / f'luma-depot-{source.id}.asc', armored)
        _write(repos / f'luma-depot-{source.id}.repo', repo_text(entry).encode())
    say(0.2, 'Downloading')
    result = runner(arguments, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        if source.id != 'fedora' and not _rpm_installed(source.package, runner):
            _forget_repository(source.id, repos, keys)
        raise RuntimeError(_last_line(result) or 'The package could not be added to the system.')
    say(0.85, 'Finishing')
    if apply is None:
        from .system_helper import apply_live
        apply = apply_live
    live = apply(replacement=False)
    return 'installed' if live else 'installed-restart'


def _forget_repository(identifier: str, repos: Path, keys: Path) -> None:
    for path in (repos / f'luma-depot-{identifier}.repo', keys / f'luma-depot-{identifier}.asc'):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def repo_remove(identifier: str, invoking_uid: int | None, *, runner=subprocess.run, catalogs=None,
                apply=None, repos=REPOS_DIRECTORY, keys=KEYS_DIRECTORY) -> str:
    entry = listing(identifier, invoking_uid, catalogs=catalogs)
    source = entry.rpm_repository
    if source is None:
        raise Refused(f'{entry.name} does not come from a package repository.')
    say(0.05, 'Removing')
    result = runner([RPM_OSTREE, 'uninstall', '--idempotent', source.package],
                    capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        line = _last_line(result)
        if 'not currently requested' in line or 'not layered' in line:
            raise Refused(f'{entry.name} was not added by Depot, so Depot does not remove it.')
        raise RuntimeError(line or 'The package could not be removed.')
    if source.id != 'fedora':
        _forget_repository(source.id, repos, keys)
    say(0.8, 'Finishing')
    if apply is None:
        from .system_helper import apply_live
        apply = apply_live
    return 'removed' if apply(replacement=True) else 'removed-restart'


COMMANDS = {
    'snap-install': snap_install, 'snap-refresh': snap_refresh, 'snap-remove': snap_remove,
    'repo-install': repo_install, 'repo-remove': repo_remove,
}


def main(values, *, lock) -> int:
    import sys
    command, identifier = values
    invoking = os.environ.get('PKEXEC_UID', '')
    uid = int(invoking) if invoking.isdigit() else None
    try:
        with lock():
            outcome = COMMANDS[command](identifier, uid)
        print(f'result: {outcome}', flush=True)
        return 0
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr, flush=True)
        return 3
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f'error: {error}', file=sys.stderr, flush=True)
        return 1

