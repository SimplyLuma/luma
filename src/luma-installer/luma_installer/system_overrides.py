# SPDX-License-Identifier: Apache-2.0
"""ADR-031: remove or restore an app that Luma's image ships.

Runs inside ``/usr/libexec/luma-installer-system`` as root, reached only
through pkexec and the ``org.projectluma.application-installer.system`` polkit
action. Two commands:

``override-remove PACKAGE``
    ``rpm-ostree override remove PACKAGE``. Allowed only for a package that a
    verified catalogue names as a Luma-tier ``luma_system`` source, where every
    listing naming that package says ``removable: true``, that is in the
    booted base (not layered, not locally replaced), that nothing installed
    requires, and that is not on this helper's own floor of packages a working
    Luma needs whatever a catalogue says.

``override-reset PACKAGE``
    ``rpm-ostree override reset PACKAGE``. Allowed for any package currently in
    the removal set of the deployment the next boot uses, catalogued or not, so
    a person can always undo a removal.

Both refuse while another rpm-ostree transaction runs, report progress as
``progress: <fraction> <text>`` lines on standard output, and read the result
back: rpm-ostree has been seen to report success for an override it silently
dropped, so success is only claimed when the staged deployment shows it.

The catalogue is read from two places only: the seed this package installs in
``/usr/share`` (root-owned, part of the signed OS image) and the invoking
person's cached catalogue, which is trusted only after its minisign signature
verifies against the key in ``/usr/share``. The newer of the two decides.
Nothing from the caller's environment is consulted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

from . import depot_catalog
from .depot_signature import SIGNATURE_MAX_BYTES
from .depot_system_apps import SystemView, in_base_database, parse_status, valid_package

RPM_OSTREE = '/usr/bin/rpm-ostree'
RPM = '/usr/bin/rpm'
SEED = Path('/usr/share/luma/installer/depot-catalog-4.json')
KEY = Path('/usr/share/luma/installer/depot-catalog.pub')
CACHE = Path('.cache/luma/depot/catalog-4.json')

#: Packages this helper never removes, whatever a catalogue says: the pieces
#: that let a person restore anything again, sign in, and start a session.
FLOOR = frozenset({
    'luma-application-installer', 'luma-update', 'luma-developer-platform', 'rpm-ostree', 'ostree',
    'polkit', 'systemd', 'glibc', 'bash', 'sudo', 'kernel', 'kernel-core', 'dbus-broker',
    'gnome-shell', 'mutter', 'gnome-session', 'gdm', 'gnome-control-center', 'NetworkManager',
    'flatpak', 'python3', 'python3-gobject', 'gtk4', 'libadwaita',
})


class Refused(ValueError):
    """The request is not allowed; the message is shown to the person."""


def _say(fraction: float, text: str) -> None:
    print(f'progress: {fraction:.2f} {text}', flush=True)


def status(runner=subprocess.run) -> SystemView:
    result = runner([RPM_OSTREE, 'status', '--json'], check=False, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError('could not read the system deployments')
    return parse_status(json.loads(result.stdout))


def installed(package: str, runner=subprocess.run) -> bool:
    result = runner([RPM, '-q', '--', package], check=False, capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def required_by(package: str, runner=subprocess.run) -> tuple[str, ...]:
    result = runner([RPM, '-q', '--whatrequires', '--qf', '%{NAME}\\n', '--', package], check=False,
                    capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return ()
    return tuple(sorted({line.strip() for line in result.stdout.splitlines()
                         if valid_package(line.strip()) and line.strip() != package}))


def _read_owned(path: Path, owner: int, limit: int) -> bytes:
    """A regular file owned by ``owner``, never a link, FIFO or device, bounded."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
            raise OSError('not a regular file owned by its user')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise OSError('too large')
        return content
    finally:
        os.close(descriptor)


def trusted_catalogs(invoking_uid: int | None, *, seed: Path = SEED, key: Path = KEY,
                     home_for=lambda uid: Path(pwd.getpwuid(uid).pw_dir)) -> list:
    """The shipped seed and the caller's verified cache, whichever verify."""
    catalogs = []
    try:
        content = _read_owned(seed, 0, depot_catalog.MAX_SIGNED_BYTES)
        catalogs.append(depot_catalog.validate_catalog(depot_catalog._decode(content)))
    except (OSError, depot_catalog.CatalogError):
        pass
    if invoking_uid is not None and invoking_uid > 0:
        try:
            path = home_for(invoking_uid) / CACHE
            content = _read_owned(path, invoking_uid, depot_catalog.MAX_SIGNED_BYTES)
            signature = _read_owned(depot_catalog.signature_path(path), invoking_uid, SIGNATURE_MAX_BYTES)
            key_data = _read_owned(key, 0, 1024)
            catalogs.append(depot_catalog._verified(content, signature, key_data))
        except (OSError, KeyError, depot_catalog.CatalogError):
            pass
    return catalogs


def _moment(catalog):
    try:
        return depot_catalog._moment(catalog.generated_at)
    except (ValueError, TypeError):
        return None


def removable(package: str, catalogs) -> bool:
    """Whether the newest trusted catalogue lets Depot remove ``package``."""
    dated = [catalog for catalog in catalogs if catalog.schema_version == 4 and _moment(catalog) is not None]
    if not dated:
        return False
    newest = max(dated, key=_moment)
    listings = [entry.luma_system for entry in newest.applications
                if entry.tier == 'luma' and entry.luma_system is not None
                and entry.luma_system.package == package]
    return bool(listings) and all(listing.removable for listing in listings)


def next_boot(view: SystemView):
    return view.pending or view.booted


def check_remove(package: str, view: SystemView, catalogs, *, runner=subprocess.run) -> bool:
    """Raise :class:`Refused` unless removal may start. True when there is nothing to do."""
    if not valid_package(package):
        raise Refused('That is not a package name.')
    if view.booted is None:
        raise Refused('This computer does not run from a Luma system image.')
    if view.transaction:
        raise Refused('Another change to the system is in progress. Try again when it finishes.')
    if package in FLOOR:
        raise Refused('Luma needs this package to work, so it cannot be removed.')
    if not removable(package, catalogs):
        raise Refused('Depot’s catalogue does not list this app as one you can remove.')
    target = next_boot(view)
    if package in target.removals:
        return True
    if package in target.requested_removals:
        # rpm-ostree recorded this removal earlier and could not apply it.
        raise Refused('This version of Luma cannot remove this app from its system image.')
    if package in view.booted.layered or package in target.layered:
        raise Refused('This app was added to this computer, not shipped with Luma.')
    if package in view.booted.replaced or package in target.replaced:
        raise Refused('This computer uses a replaced copy of this app; restore the original first.')
    if not installed(package, runner):
        raise Refused('This app is not part of the system on this computer.')
    if in_base_database(package, runner=runner, fixtures=False) is False:
        raise Refused('This version of Luma does not list this app in its system image’s package '
                      'database, so it cannot be removed.')
    needed = required_by(package, runner)
    if needed:
        raise Refused('Other parts of Luma need this app: ' + ', '.join(needed[:6]) + '.')
    return False


def check_reset(package: str, view: SystemView) -> bool:
    if not valid_package(package):
        raise Refused('That is not a package name.')
    if view.booted is None:
        raise Refused('This computer does not run from a Luma system image.')
    if view.transaction:
        raise Refused('Another change to the system is in progress. Try again when it finishes.')
    target = next_boot(view)
    if package not in target.requested_removals and package not in target.removals:
        raise Refused('This app has not been removed.')
    return False


#: rpm-ostree's own progress lines, in the order a layering transaction prints them.
_PHASES = (
    ('Checking out tree', 0.15, 'Reading the system'),
    ('Resolving dependencies', 0.3, 'Checking what else needs it'),
    ('Inactive base packages', 0.3, 'Checking what else needs it'),
    ('Checking out packages', 0.4, 'Preparing the change'),
    ('Running pre scripts', 0.5, 'Preparing the change'),
    ('Running post scripts', 0.6, 'Preparing the change'),
    ('Writing rpmdb', 0.7, 'Writing the new system'),
    ('Generating initramfs', 0.75, 'Writing the new system'),
    ('Writing OSTree commit', 0.85, 'Writing the new system'),
    ('Staging deployment', 0.92, 'Getting it ready for the next start'),
)


def run_rpm_ostree(arguments, *, popen=subprocess.Popen) -> None:
    process = popen([RPM_OSTREE, *arguments], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
    last, reached = '', 0.0
    for line in process.stdout:
        text = line.strip()
        if text:
            last = text
        for marker, fraction, words in _PHASES:
            if text.startswith(marker) and fraction > reached:
                reached = fraction
                _say(fraction, words)
                break
    code = process.wait(timeout=3600)
    if code != 0:
        raise RuntimeError(last or 'rpm-ostree could not stage the change')


def override(action: str, package: str, *, invoking_uid: int | None, runner=subprocess.run,
             popen=subprocess.Popen, catalogs=None, read_status=None) -> str:
    """Do one override change. Returns ``staged`` or ``unchanged``."""
    read_status = read_status or (lambda: status(runner))
    _say(0.05, 'Checking this computer')
    view = read_status()
    if action == 'override-remove':
        if catalogs is None:
            catalogs = trusted_catalogs(invoking_uid)
        if check_remove(package, view, catalogs, runner=runner):
            return 'unchanged'
        arguments = ['override', 'remove', package]
    elif action == 'override-reset':
        check_reset(package, view)
        arguments = ['override', 'reset', package]
    else:
        raise Refused('Unknown change.')
    run_rpm_ostree(arguments, popen=popen)
    after = next_boot(read_status())
    if action == 'override-remove':
        if after is None or package not in after.removals:
            # rpm-ostree reports success for a removal it records as inactive
            # and does not apply. Take the request back so nothing is left
            # half-done, and remove the no-op deployment when it is ours alone.
            withdraw(package, had_pending=view.pending is not None, popen=popen)
            raise RuntimeError('rpm-ostree could not remove this app from the system image, '
                               'so nothing changed')
    elif after is None or package in after.requested_removals or package in after.removals:
        raise RuntimeError('rpm-ostree reported success but did not stage the change')
    _say(1.0, 'Restart to finish')
    return 'staged'


def withdraw(package: str, *, had_pending: bool, popen=subprocess.Popen) -> None:
    try:
        run_rpm_ostree(['override', 'reset', package], popen=popen)
        if not had_pending:
            # Only a deployment this helper staged is cleaned up; another
            # pending one (a staged system update) is never touched.
            run_rpm_ostree(['cleanup', '--pending'], popen=popen)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass


def main(values, *, lock) -> int:
    action, package = values
    uid = os.environ.get('PKEXEC_UID')
    invoking = int(uid) if uid and uid.isdigit() else None
    try:
        with lock():
            result = override(action, package, invoking_uid=invoking)
        print(f'result: {result}', flush=True)
        return 0
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr, flush=True)
        return 3
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f'error: {error}', file=sys.stderr, flush=True)
        return 1
