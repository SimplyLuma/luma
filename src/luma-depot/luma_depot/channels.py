# SPDX-License-Identifier: Apache-2.0
"""Apps Depot installs from their publisher's official channel, not a Flatpak.

Two kinds, both named by the signed catalogue and both carried out by the
system helper after polkit asks the person (luma_installer.depot_channels):

* ``snap``: the publisher's own snap, from a publisher the Snap Store has
  verified, strictly confined. snapd keeps it up to date by itself.
* ``repository``: the publisher's signed RPM repository (its key pinned by
  fingerprint in the catalogue) or Fedora's own. The package is added to the
  system image and applied at once, and updates with Luma's own updates.

This module answers what the window needs to know: whether an entry can be
installed here, whether it is installed, which desktop files are its, how its
source reads, and how to run the helper with progress. It never decides what
is trusted; the helper checks everything again as root.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from gi.repository import GLib

from .providers import Progress, ProviderError

HELPER = '/usr/libexec/luma-installer-system'
SNAP = Path('/usr/bin/snap')
RPM_OSTREE = Path('/usr/bin/rpm-ostree')
SNAPD_SOCKET = '/run/snapd.socket'
VERIFIERS = {'luma': 'Luma', 'flathub': 'Flathub', 'snap': 'the Snap Store'}


def kind(entry) -> str:
    """'snap', 'repository', 'deb' or '' for one catalogue entry."""
    if entry.backend == 'snap' and getattr(entry, 'snap', None) is not None:
        return 'snap'
    if entry.backend == 'deb' and getattr(entry, 'deb_repository', None) is not None:
        return 'deb'
    if entry.backend == 'rpm' and getattr(entry, 'rpm_repository', None) is not None:
        return 'repository'
    return ''


def available(entry, architecture: str, *, snap_path=SNAP, rpm_ostree=RPM_OSTREE,
              helper=Path(HELPER)) -> tuple[bool, str]:
    """(Depot can install it here, the sentence saying why not)."""
    channel = kind(entry)
    if not channel:
        return False, ''
    if entry.architectures and architecture not in entry.architectures:
        return False, 'Not available for this computer’s processor'
    if not helper.exists():
        return False, 'This version of Luma cannot install it yet'
    if channel == 'snap' and not snap_path.exists():
        return False, 'Snaps are not set up on this computer'
    if channel == 'repository' and not rpm_ostree.exists():
        return False, 'This computer cannot add system packages'
    if channel == 'deb' and not (shutil.which('podman') and shutil.which('gpg') and shutil.which('dpkg-deb')):
        return False, 'Debian apps are not set up on this computer'
    return True, ''


def presentation(entry, *, installable: bool) -> dict:
    """How the listing's source reads: label, verified mark, exception reason."""
    channel = getattr(entry, 'channel', None)
    out = {'source_label': '', 'source_verified': False, 'source_verifier': '',
           'publisher_reason': '', 'web_app': False, 'sandbox': getattr(entry, 'sandbox', ''),
           'update_note': ''}
    if channel is None:
        return out
    title = channel.title
    if channel.kind == 'flathub':
        suffix = ' · Verified' if channel.publisher_verified else (' · Community package' if channel.community else '')
        out['source_label'] = f'From Flathub{suffix}'
        out['source_verified'] = channel.publisher_verified
        out['source_verifier'] = VERIFIERS['flathub']
        out['update_note'] = 'From Flathub'
    elif channel.kind == 'snap':
        out['source_label'] = f'From {title}' + (' · Verified' if channel.publisher_verified else '')
        out['source_verified'] = channel.publisher_verified
        out['source_verifier'] = VERIFIERS['snap']
        out['update_note'] = 'Automatic, from the Snap Store'
    elif channel.kind == 'rpm-repository':
        out['source_label'] = f'From {title}'
        out['update_note'] = 'With Luma’s system updates'
    elif channel.kind == 'deb-repository':
        out['source_label'] = f'From {title}'
        out['update_note'] = 'When you choose Update'
        out['sandbox'] = out['sandbox'] or 'deb-capsule'
    elif channel.kind == 'fedora':
        out['source_label'] = 'From Fedora'
        out['update_note'] = 'With Luma’s system updates'
    elif channel.kind == 'luma':
        out['source_label'] = 'From Luma'
        out['source_verified'] = True
        out['source_verifier'] = VERIFIERS['luma']
    elif channel.kind == 'publisher':
        out['source_label'] = f'From {title}'
        out['publisher_reason'] = channel.reason
        out['web_app'] = channel.web
    return out


#: Snap interfaces in plain words: (title, detail, level). Only the ones a
#: listing asks Luma to connect are shown; everything else a strict snap may
#: do is what every strictly confined snap may do.
SNAP_INTERFACES = {
    'network-control': ('Changes network settings', 'It can add network connections and change routing.', 'high'),
    'firewall-control': ('Controls the firewall', 'It can add and remove firewall rules.', 'high'),
    'network-observe': ('Sees network activity', 'It can read network interfaces and connections.', 'sensitive'),
    'network-manager': ('Manages network connections', 'It can ask NetworkManager to change connections.', 'high'),
    'system-observe': ('Sees running programs', 'It can read the list of processes and system details.', 'sensitive'),
    'hardware-observe': ('Sees hardware details', 'It can read what hardware this computer has.', 'standard'),
    'log-observe': ('Reads system logs', 'It can read the system journal.', 'sensitive'),
    'login-session-observe': ('Sees who is signed in', 'It can read sign-in sessions on this computer.', 'sensitive'),
    'home': ('Your home folder', 'It can read and change files in your home folder, not hidden ones.', 'high'),
    'removable-media': ('USB drives and memory cards', 'It can read and change files on them.', 'sensitive'),
    'camera': ('Camera', 'It can use the camera.', 'sensitive'),
    'audio-record': ('Microphone', 'It can record audio.', 'sensitive'),
    'password-manager-service': ('Your saved passwords', 'It can store and read its own passwords in the system keyring.', 'sensitive'),
}


def snap_permissions(entry):
    """The interfaces Luma connects for a snap listing, as permission rows."""
    from .providers import Permission
    snap = getattr(entry, 'snap', None)
    rows = []
    for plug in (snap.connect if snap is not None else ()):
        title, detail, level = SNAP_INTERFACES.get(
            plug, (f'Uses the {plug} interface', 'Ask its developer what it is for.', 'high'))
        rows.append(Permission(f'snap.{plug}', title, detail, notable=level != 'standard', level=level))
    return tuple(rows)


# ── What is installed ────────────────────────────────────────────────────

def _snapd_get(target: str, path: str = SNAPD_SOCKET) -> dict | list | None:
    """A read from snapd, which any user may make. None when snapd is absent."""
    if not Path(path).exists():
        return None
    import http.client
    import socket

    class Connection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect(path)

    connection = Connection('localhost', timeout=10)
    try:
        connection.request('GET', target)
        document = json.loads(connection.getresponse().read() or b'{}')
    except (OSError, ValueError):
        return None
    finally:
        connection.close()
    if document.get('type') == 'error':
        return None
    return document.get('result')


def installed_snaps(*, reader=_snapd_get) -> dict:
    """Installed snaps by name: {'version', 'publisher', 'desktop_files'}."""
    result = reader('/v2/snaps')
    found = {}
    for item in result or []:
        if not isinstance(item, dict) or not item.get('name'):
            continue
        desktop = [app.get('desktop-file') for app in item.get('apps') or [] if app.get('desktop-file')]
        found[item['name']] = {
            'version': str(item.get('version') or ''),
            'publisher': (item.get('publisher') or {}).get('username', ''),
            'installed_bytes': int(item.get('installed-size') or 0),
            'desktop_files': tuple(desktop),
        }
    return found


def installed_packages(names, *, runner=subprocess.run) -> dict:
    """Installed RPMs among ``names``: name -> (version, desktop files)."""
    names = sorted({name for name in names if name})
    if not names:
        return {}
    try:
        result = runner(['rpm', '-q', '--qf', '%{NAME}\\t%{VERSION}\\n', '--', *names],
                        capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in (result.stdout or '').splitlines():
        name, _, version = line.partition('\t')
        if name in names and version:
            found[name] = version
    out = {}
    for name, version in found.items():
        try:
            listing = runner(['rpm', '-ql', '--', name], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ''
        desktop = tuple(line for line in listing.splitlines()
                        if line.startswith('/usr/share/applications/') and line.endswith('.desktop'))
        out[name] = (version, desktop)
    return out


class State:
    """One reading of which channel apps are installed, for one catalogue."""

    def __init__(self, catalog, *, snaps=None, packages=None, records=None) -> None:
        entries = [entry for entry in catalog.applications if kind(entry)]
        self.entries = {entry.id: entry for entry in entries}
        wanted = [entry.rpm_repository.package for entry in entries if kind(entry) == 'repository']
        self.snaps = installed_snaps() if snaps is None and any(kind(e) == 'snap' for e in entries) else (snaps or {})
        self.packages = installed_packages(wanted) if packages is None else packages
        if records is None and any(kind(e) == 'deb' for e in entries):
            from luma_installer.desktop import iter_records
            try:
                records = iter_records()
            except OSError:
                records = []
        self.records = records or []
        self.installed = {}
        self.desktop_files = {}
        for entry in entries:
            if kind(entry) == 'snap':
                snap = self.snaps.get(entry.snap.name)
                if snap is None:
                    continue
                self.installed[entry.id] = {'version': snap['version'], 'bytes': snap['installed_bytes'],
                                            'managed': snap['publisher'] == entry.snap.publisher}
                files = snap['desktop_files']
            elif kind(entry) == 'deb':
                from luma_installer.depot_deb_repository import installed_records
                found = installed_records(entry.deb_repository.package, records=self.records)
                if not found:
                    continue
                record = found[-1]
                self.installed[entry.id] = {'version': str(record.get('version') or ''), 'bytes': 0,
                                            'managed': True, 'record': record}
                files = tuple(str(Path.home() / '.local/share/applications'
                                  / f"org.projectluma.Installed.{r['application_id']}.desktop") for r in found)
            else:
                package = self.packages.get(entry.rpm_repository.package)
                if package is None:
                    continue
                self.installed[entry.id] = {'version': package[0], 'bytes': 0, 'managed': True}
                files = package[1]
            self.desktop_files[entry.id] = files

    def identities(self) -> dict:
        """Desktop file id -> catalogue identity, for every installed channel app."""
        out = {}
        for identifier, files in self.desktop_files.items():
            for path in files:
                out[Path(path).name] = 'catalog:' + identifier
        return out


# ── Running the helper ───────────────────────────────────────────────────

VERBS = {('snap', 'install'): 'snap-install', ('snap', 'update'): 'snap-refresh',
         ('snap', 'remove'): 'snap-remove', ('repository', 'install'): 'repo-install',
         ('repository', 'remove'): 'repo-remove'}
STAGES = {'install': 'Installing', 'update': 'Updating', 'remove': 'Removing'}


class HelperFailure(Exception):
    """The helper's own error text, classified by depot_errors like any other."""


def run(channel: str, action: str, entry, app_id: str, on_progress, *, popen=None) -> str:
    """Run the helper for ``action`` on ``entry``; returns its ``result:`` word."""
    verb = VERBS.get((channel, action))
    if verb is None:
        raise ProviderError('Not available', hint='Updates for this app arrive with Luma’s system updates.')
    popen = popen or subprocess.Popen
    stage = STAGES[action]
    GLib.idle_add(on_progress, Progress(app_id, 0.01, 0, 0, 'Waiting for permission'))
    process = popen(['pkexec', HELPER, verb, entry.id], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for raw in process.stdout:
        line = raw.strip()
        if not line:
            continue
        lines.append(line)
        if line.startswith('progress: '):
            fraction, _, text = line[len('progress: '):].partition(' ')
            try:
                value = max(0.0, min(0.99, float(fraction)))
            except ValueError:
                continue
            GLib.idle_add(on_progress, Progress(app_id, value, 0, 0, text.strip() or stage))
    code = process.wait()
    if code == 0:
        return next((line[len('result: '):] for line in reversed(lines) if line.startswith('result: ')), 'done')
    from luma_installer.depot_errors import explain, journal
    detail = '\n'.join(lines[-12:])
    if code in (126, 127):
        error = HelperFailure('Not authorized: the person did not give permission (polkit).')
    else:
        refused = next((line[len('refused: '):] for line in reversed(lines) if line.startswith('refused: ')), '')
        if refused:
            journal_error = HelperFailure(refused)
            explanation = explain(journal_error, name=entry.name, action=action)
            journal(explanation, app_id=app_id, name=entry.name, action=action, error=journal_error)
            raise ProviderError(refused, hint=refused, detail=detail)
        text = next((line[len('error: '):] for line in reversed(lines) if line.startswith('error: ')),
                    'Luma’s system helper stopped without saying why.')
        error = HelperFailure(text)
    explanation = explain(error, name=entry.name, action=action)
    journal(explanation, app_id=app_id, name=entry.name, action=action, error=error)
    raise ProviderError(explanation.message, hint=explanation.message, detail=detail or str(error))


# ── Debian apps from a publisher's signed APT repository ─────────────────

def run_deb(action: str, entry, app_id: str, on_progress, *, machine=None) -> str:
    """Install, update or remove a Debian-capsule app as the person; no helper.

    The repository, index and package are verified by depot_deb_repository
    before Valet inspects and installs the file; an update carries the app's
    capsule home over to the new build, then retires the old one.
    """
    import os
    from dataclasses import replace
    from luma_installer import depot_deb_repository as apt, workflow
    from luma_installer.depot_errors import explain, journal
    from luma_installer.errors import InstallerError
    from luma_installer.inspectors import inspect_package
    from luma_installer.progress import Transaction, current

    source = entry.deb_repository

    def say(fraction, text, got=0, total=0):
        GLib.idle_add(on_progress, Progress(app_id, max(0.0, min(0.99, fraction)), got, total, text))

    try:
        existing = apt.installed_records(source.package)
        if action == 'remove':
            say(0.1, 'Removing')
            for record in existing:
                workflow.remove(record, keep_data=True)
            return 'removed' if existing else 'unchanged'
        say(0.02, 'Checking the publisher’s repository')
        resolved = apt.resolve(source, machine or os.uname().machine)
        if existing and str(existing[-1].get('version') or '') == resolved.version:
            return 'unchanged'
        size = resolved.size
        path = apt.download(resolved, progress=lambda got, total: say(
            0.05 + 0.55 * got / max(1, size or total), 'Downloading', got, size or total))
        report = inspect_package(path)
        if report.kind != 'deb' or report.title != source.package:
            raise apt.RepositoryError('The downloaded file is not the package the listing names.')
        report = replace(report, verified=True, version=resolved.version,
                         publisher=entry.channel.publisher if entry.channel else source.name,
                         identity=f'Signed by {source.name}’s repository; checked by Depot')
        transaction = Transaction(lambda event: say(0.6 + 0.39 * event.fraction, 'Installing'),
                                  title='Installing')
        token = current.set(transaction)
        try:
            record = workflow.install(report)
        finally:
            current.reset(token)
        for old in existing:
            if old.get('application_id') == record.get('application_id'):
                continue
            apt.carry_data(old, record)
            workflow.remove(old, keep_data=True)
        try:
            path.unlink()
        except OSError:
            pass
        return 'updated' if existing else 'installed'
    except (apt.RepositoryError, InstallerError, OSError, subprocess.SubprocessError) as error:
        explanation = explain(error, name=entry.name, action=action)
        journal(explanation, app_id=app_id, name=entry.name, action=action, error=error)
        raise ProviderError(explanation.message, hint=explanation.message, detail=str(error)) from error
