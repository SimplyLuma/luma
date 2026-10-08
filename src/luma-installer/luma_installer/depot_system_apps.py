# SPDX-License-Identifier: Apache-2.0
"""ADR-031: what became of an app that Luma's image ships.

Depot lists every first-party app, including the ones already on the computer.
For a listing with a ``luma_system`` source this module answers, from the
booted system and never from the catalogue, which of these is true:

``installed``        the package is in the booted deployment's base: "Installed with Luma"
``layered``          the package is here, but was added to this computer, not shipped in its image
``removed``          the package is in the base commit and this deployment removes it: "Removed"
``removal-pending``  a removal is staged: it is gone after a restart
``restore-pending``  a restore is staged: it is back after a restart
``absent``           this image does not ship it: Depot may offer the Flatpak ("Get")

The removal set is what rpm-ostree records in a deployment's origin
(``requested-base-removals``, ``rpm-ostree override remove``); the packages
actually removed from the base are ``base-removals``. Both are read from
rpm-ostree's D-Bus API (``org.projectatomic.rpmostree1.Sysroot.Deployments``),
or from ``rpm-ostree status --json`` when the bus cannot be reached. Presence
is read from the booted rpm database with ``rpm -q``, which covers packages
replaced by a local override as well.

Nothing here changes the system. Removing and restoring go through the
polkit-guarded system helper (:mod:`luma_installer.system_overrides`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess

INSTALLED = 'installed'
LAYERED = 'layered'
REMOVED = 'removed'
REMOVAL_PENDING = 'removal-pending'
RESTORE_PENDING = 'restore-pending'
ABSENT = 'absent'
#: States in which the image's package is, or will again be, the app on this
#: computer. Depot never offers the Flatpak in these: the same app would
#: appear twice in the app grid.
SYSTEM_OWNED = (INSTALLED, LAYERED, REMOVAL_PENDING, RESTORE_PENDING)

PACKAGE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9+._-]{0,127}\Z')
_NEVRA = re.compile(r'(?P<name>.+)-(?:\d+:)?[^-]+-[^-]+\.[A-Za-z0-9_]+\Z')

RPM_OSTREE_BUS = 'org.projectatomic.rpmostree1'
SYSROOT_PATH = '/org/projectatomic/rpmostree1/Sysroot'
SYSROOT_IFACE = 'org.projectatomic.rpmostree1.Sysroot'
OS_RELEASE = Path('/etc/os-release')


def valid_package(name) -> bool:
    return isinstance(name, str) and bool(PACKAGE_NAME.fullmatch(name)) and not name.startswith('-')


def package_name(value, *, nevra: bool = False) -> str:
    """A package name from a name, a NEVRA, or rpm-ostree's package tuple.

    rpm-ostree lists removals and replacements as ``[nevra, name, epoch,
    version, release, arch]``, requested packages and requested removals as
    names, and local packages as NEVRAs. A name is never parsed as a NEVRA:
    ``gnome-shell-extension-appindicator`` has dashes too.
    """
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and isinstance(value[1], str) and value[1]:
            return value[1]
        return package_name(value[0], nevra=True) if value else ''
    if not isinstance(value, str):
        return ''
    match = _NEVRA.fullmatch(value) if nevra else None
    return match.group('name') if match else value


def _names(values, *, nevra: bool = False) -> frozenset:
    if not isinstance(values, (list, tuple)):
        return frozenset()
    return frozenset(name for name in (package_name(value, nevra=nevra) for value in values) if name)


def _replacement_names(values) -> frozenset:
    """Names from ``base-local-replacements``: pairs of (new, old) package tuples."""
    names = set()
    for pair in values if isinstance(values, (list, tuple)) else ():
        if isinstance(pair, (list, tuple)) and pair:
            names.add(package_name(pair[0], nevra=True))
    return frozenset(name for name in names if name)


@dataclass(frozen=True)
class Deployment:
    id: str = ''
    checksum: str = ''
    base_checksum: str = ''
    origin: str = ''
    booted: bool = False
    staged: bool = False
    #: Packages this deployment's origin asks to remove from the base.
    requested_removals: frozenset = frozenset()
    #: Base packages this deployment actually removed.
    removals: frozenset = frozenset()
    #: Packages layered on the base: requested by name or as local RPMs.
    layered: frozenset = frozenset()
    replaced: frozenset = frozenset()
    luma_metadata: bool = False

    @property
    def base_commit(self) -> str:
        return self.base_checksum or self.checksum


@dataclass(frozen=True)
class SystemView:
    booted: Deployment | None
    #: The deployment the next boot uses when it is not the booted one: a
    #: staged deployment, or a finalized pending one.
    pending: Deployment | None = None
    #: rpm-ostree is running a transaction; the helper refuses to start another.
    transaction: bool = False
    deployments: tuple = field(default=(), compare=False)

    @property
    def restart_pending(self) -> bool:
        return self.pending is not None


def _unpack(value):
    unpack = getattr(value, 'unpack', None)
    return unpack() if callable(unpack) else value


def parse_deployment(values: dict) -> Deployment:
    values = {key: _unpack(value) for key, value in values.items()}
    meta = values.get('base-commit-meta') if isinstance(values.get('base-commit-meta'), dict) else {}
    layered = (_names(values.get('requested-packages')) | _names(values.get('packages'))
               | _names(values.get('requested-local-packages'), nevra=True))
    return Deployment(
        id=str(values.get('id', '')),
        checksum=str(values.get('checksum', '')),
        base_checksum=str(values.get('base-checksum', '') or ''),
        origin=str(values.get('origin', '') or ''),
        booted=bool(values.get('booted', False)),
        staged=bool(values.get('staged', False)),
        requested_removals=_names(values.get('requested-base-removals')),
        removals=_names(values.get('base-removals')),
        layered=layered,
        replaced=_replacement_names(values.get('base-local-replacements')),
        luma_metadata=any(isinstance(key, str) and key.startswith('org.projectluma.') for key in meta),
    )


def view_from_deployments(rows, transaction=False) -> SystemView:
    deployments = tuple(parse_deployment(row) for row in rows if isinstance(row, dict))
    booted = next((d for d in deployments if d.booted), None)
    first = deployments[0] if deployments else None
    pending = first if first is not None and booted is not None and not first.booted else None
    return SystemView(booted, pending, bool(transaction), deployments)


def parse_status(status) -> SystemView:
    """``rpm-ostree status --json`` as a :class:`SystemView`."""
    if not isinstance(status, dict):
        raise ValueError('not an rpm-ostree status document')
    return view_from_deployments(status.get('deployments') or (), status.get('transaction'))


def read_dbus(timeout_ms: int = 10000) -> SystemView:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def get(name):
        reply = bus.call_sync(RPM_OSTREE_BUS, SYSROOT_PATH, 'org.freedesktop.DBus.Properties', 'Get',
                              GLib.Variant('(ss)', (SYSROOT_IFACE, name)), GLib.VariantType('(v)'),
                              Gio.DBusCallFlags.NONE, timeout_ms, None)
        return reply.unpack()[0]

    deployments = get('Deployments')
    active = get('ActiveTransaction')
    busy = isinstance(active, (tuple, list)) and any(active)
    return view_from_deployments(deployments, busy)


def read_cli(timeout: int = 60) -> SystemView:
    result = subprocess.run(['rpm-ostree', 'status', '--json'], check=False, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or 'rpm-ostree status failed')
    return parse_status(json.loads(result.stdout))


def read_system() -> SystemView | None:
    """The booted system, or None when this computer is not an rpm-ostree system."""
    if not Path('/run/ostree-booted').exists() and 'LUMA_DEPOT_RPM_OSTREE_STATUS' not in os.environ:
        return None
    fixture = os.environ.get('LUMA_DEPOT_RPM_OSTREE_STATUS')
    if fixture:
        # Tests and renders only: a captured status document instead of the bus.
        return parse_status(json.loads(Path(fixture).read_text()))
    try:
        return read_dbus()
    except Exception:
        pass
    try:
        return read_cli()
    except Exception:
        return None


def installed_packages(names, *, runner=subprocess.run) -> frozenset:
    """Which of ``names`` the booted rpm database has."""
    wanted = sorted({name for name in names if valid_package(name)})
    if not wanted:
        return frozenset()
    fixture = os.environ.get('LUMA_DEPOT_INSTALLED_PACKAGES')
    if fixture is not None:
        return frozenset(wanted) & frozenset(fixture.split(','))
    try:
        result = runner(['rpm', '-q', '--qf', '%{NAME}\\n', '--', *wanted], check=False,
                        capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return frozenset(line.strip() for line in result.stdout.splitlines()) & frozenset(wanted)


def required_by(package: str, *, runner=subprocess.run) -> tuple[str, ...]:
    """Installed packages that need ``package``: removing it would fail to resolve."""
    if not valid_package(package):
        return ()
    if os.environ.get('LUMA_DEPOT_INSTALLED_PACKAGES') is not None:
        # Renders only: "package:needs,needs;package:needs".
        for item in os.environ.get('LUMA_DEPOT_REQUIRED_BY', '').split(';'):
            name, _, needs = item.partition(':')
            if name == package:
                return tuple(sorted(filter(None, needs.split(','))))
        return ()
    try:
        result = runner(['rpm', '-q', '--whatrequires', '--qf', '%{NAME}\\n', '--', package], check=False,
                        capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    return tuple(sorted({line.strip() for line in result.stdout.splitlines()
                         if valid_package(line.strip()) and line.strip() != package}))


BASE_DB = Path('/usr/lib/sysimage/rpm-ostree-base-db')


def in_base_database(package: str, *, runner=subprocess.run, base_db: Path = BASE_DB,
                     fixtures: bool = True) -> bool | None:
    """Whether rpm-ostree counts ``package`` as part of the booted base image.

    rpm-ostree decides what ``override remove`` may remove from the base
    package database it keeps beside the image's rpm database. An image built
    by installing packages into a base container without refreshing that
    database lists only the base container's packages, and rpm-ostree then
    records a removal of anything else as inactive and changes nothing.
    None when the database is not there to ask.
    """
    if not valid_package(package):
        return False
    fixture = os.environ.get('LUMA_DEPOT_BASE_PACKAGES') if fixtures else None
    if fixture is not None:
        return package in fixture.split(',')
    if not base_db.is_dir():
        return None
    try:
        result = runner(['rpm', '-q', '--dbpath', str(base_db), '--', package], check=False,
                        capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode == 0


def app_state(view: SystemView | None, installed: frozenset, package: str) -> str:
    """One of the module's states for ``package`` on this computer."""
    if view is None or view.booted is None or not valid_package(package):
        return INSTALLED if package in installed else ABSENT
    booted, pending = view.booted, view.pending
    # What rpm-ostree actually removed (base-removals) decides, not what an
    # origin asks for: rpm-ostree records a removal it cannot apply as an
    # "inactive base removal" and changes nothing.
    removed_now = package in booted.removals or (package in booted.requested_removals
                                                 and package not in installed)
    if removed_now:
        if pending is not None and package not in pending.removals and package not in pending.requested_removals:
            return RESTORE_PENDING
        return REMOVED
    if package in installed:
        if pending is not None and package in pending.removals:
            return REMOVAL_PENDING
        if package in booted.layered:
            return LAYERED
        return INSTALLED
    return ABSENT


def inactive_removal(view: SystemView | None, package: str) -> bool:
    """A removal is requested for the next boot but rpm-ostree did not apply it.

    Seen on images whose ``/usr/lib/sysimage/rpm-ostree-base-db`` does not list
    the package: rpm-ostree then does not consider it part of the base.
    """
    if view is None or view.booted is None:
        return False
    target = view.pending or view.booted
    return package in target.requested_removals and package not in target.removals


def is_luma(view: SystemView | None, os_release: Path = OS_RELEASE) -> bool:
    """Whether the ``luma_system`` half of a listing applies to this computer.

    A Luma image says so in os-release (``ID=luma``). Machines deployed from
    Luma's earlier private commits still say Fedora there, but their base
    commit carries Luma's build metadata. Anything else is another
    distribution, where only Flatpak sources apply.
    """
    forced = os.environ.get('LUMA_DEPOT_SYSTEM')
    if forced in ('luma', 'other'):
        return forced == 'luma'
    try:
        fields = dict(line.split('=', 1) for line in os_release.read_text().splitlines() if '=' in line)
    except OSError:
        fields = {}
    identity = fields.get('ID', '').strip().strip('"\'')
    like = fields.get('ID_LIKE', '').strip().strip('"\'').split()
    if identity == 'luma' or 'luma' in like:
        # Even when rpm-ostree cannot be read right now: this is Luma, and
        # saying "Available on Luma" here would be wrong.
        return True
    return bool(view is not None and view.booted is not None and view.booted.luma_metadata)


@dataclass(frozen=True)
class SystemApps:
    """A snapshot of the system half of every listing, read once per catalogue load."""
    luma: bool
    view: SystemView | None
    installed: frozenset = frozenset()

    def state(self, package: str) -> str:
        if not self.luma:
            return ABSENT
        return app_state(self.view, self.installed, package)

    @property
    def busy(self) -> bool:
        return bool(self.view and self.view.transaction)

    @classmethod
    def read(cls, packages) -> 'SystemApps':
        view = read_system()
        luma = is_luma(view)
        installed = installed_packages(packages) if luma else frozenset()
        return cls(luma, view, installed)
