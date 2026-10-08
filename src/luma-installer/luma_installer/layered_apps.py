"""Native applications added outside Valet, owned by rpm-ostree, not capsules.

Launcher metadata is never removal authority. The authenticated helper repeats
the launcher/RPM/deployment checks under its transaction lock before changing
anything. User records cannot turn a base package into a removable one.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat
import subprocess

from .depot_system_apps import BASE_DB, valid_package
from .removal import ESSENTIAL_PACKAGES, is_essential
from .system_overrides import FLOOR, Refused, next_boot, status

APPLICATIONS = Path('/usr/share/applications')
DESKTOP_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,239}\.desktop\Z')


def identity(desktop_id, expected=None, *, runner=None, applications=APPLICATIONS, base_db=BASE_DB):
    """Return one actual externally layered RPM; fail closed on unknown state."""
    runner = runner or subprocess.run
    if not isinstance(desktop_id, str) or not DESKTOP_ID.fullmatch(desktop_id):
        raise Refused('That is not a system application launcher.')
    path = applications / desktop_id
    for candidate in (applications, path):
        metadata = candidate.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or stat.S_ISLNK(metadata.st_mode):
            raise Refused('The application launcher is not owned by the system.')
    if not stat.S_ISREG(path.lstat().st_mode):
        raise Refused('The application launcher is not a regular file.')
    result = runner(['/usr/bin/rpm', '-qf', '--qf', '%{NAME}\\n', '--', str(path)],
                    check=False, capture_output=True, text=True, timeout=30)
    names = result.stdout.strip().splitlines()
    if result.returncode != 0 or len(names) != 1 or not valid_package(names[0]):
        raise Refused('The system could not identify the package that owns this application.')
    package = names[0]
    if expected is not None and package != expected:
        raise Refused('The application’s package owner changed after review. Review it again.')
    if package in FLOOR | ESSENTIAL_PACKAGES or is_essential(identity=desktop_id):
        raise Refused('Luma needs this package to work, so it cannot be removed.')
    view = status(runner)
    if view.booted is None or view.transaction:
        raise Refused('The system deployment is unavailable or another change is in progress.')
    target = next_boot(view)
    # apply-live installs into the running RPM database but intentionally
    # leaves the booted deployment's immutable origin unchanged. The staged
    # same-base origin is then the owner of the live package, not the old
    # booted requested-packages list.
    live_same_base = (view.pending is not None and bool(view.booted.base_commit)
                      and target.base_commit == view.booted.base_commit)
    if (package not in target.layered or
            (package not in view.booted.layered and not live_same_base) or
            any(package in deployment.replaced for deployment in (view.booted, target))):
        raise Refused('This package is not an externally added application in the current deployment.')
    if not base_db.is_dir():
        raise Refused('The system’s base package database is unavailable.')
    base = runner(['/usr/bin/rpm', '-q', '--dbpath', str(base_db), '--', package],
                  check=False, capture_output=True, text=True, timeout=30)
    if base.returncode != 1:
        raise Refused('This package belongs to the system image, or its ownership could not be verified.')
    return package


def receipt_id(desktop_id, package):
    return hashlib.sha256(('layered-rpm\0' + desktop_id + '\0' + package).encode()).hexdigest()


def remove(desktop_id, expected, *, runner=None):
    """Called only by the polkit helper under its existing transaction lock."""
    from . import system_helper
    runner = runner or subprocess.run
    package = identity(desktop_id, expected, runner=runner)
    fingerprint = receipt_id(desktop_id, package)
    root = system_helper.SYSTEM_STATE_ROOT / 'receipts'
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = root / (fingerprint + '.json')
    receipt = {'format': 'rpm', 'package_name': package, 'desktop_id': desktop_id,
               'sha256': fingerprint, 'external_layered': True, 'state': 'removing'}
    system_helper.write_receipt(path, receipt)
    # rpm-ostree's uninstall parser does not accept a '--' separator. The
    # actual RPM owner was already restricted to a non-option package name.
    result = runner(['/usr/bin/rpm-ostree', 'uninstall', package], check=False,
                    capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or 'Package removal failed.')
    view = status(runner)
    if view.booted is None or package in next_boot(view).layered:
        raise RuntimeError('The system did not stage this package’s removal; its receipt was retained.')
    restart = not system_helper.apply_live(replacement=True)
    if not restart:
        present = runner(['/usr/bin/rpm', '-q', '--', package], check=False,
                         capture_output=True, text=True, timeout=30)
        restart = present.returncode != 1
    if restart:
        receipt.update(state='removal-pending-restart', restart_required=True)
        system_helper.write_receipt(path, receipt)
    else:
        path.unlink()
    return restart
