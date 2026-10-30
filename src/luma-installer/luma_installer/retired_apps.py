# SPDX-License-Identifier: Apache-2.0
"""Remove retired app deployments; retain user data and unrelated refs.

This signed host policy cannot be extended by a downloaded catalogue or by
home-directory files. Normal Flatpak uninstall keeps ~/.var/app intact. No
home scan, change of user, runtime cleanup or force-stop belongs here.
"""
import argparse
from contextlib import contextmanager
import fcntl
import os
import re
from pathlib import Path
import stat

RETIRED = frozenset({'org.projectluma.Displays'})


def retired_refs(refs):
    found = []
    for ref in refs:
        parts = ref.format_ref().split('/')
        if len(parts) != 4 or parts[0] != 'app' or parts[1] not in RETIRED:
            continue
        if (ref.get_name() != parts[1] or
                any(not re.fullmatch(r'[A-Za-z0-9._-]+', part) for part in parts[2:])):
            raise ValueError('A retired application has an ambiguous Flatpak identity.')
        found.append(ref.format_ref())
    if len(found) != len(set(found)):
        raise ValueError('A retired application has duplicate installed refs.')
    return tuple(sorted(found))


def removal_transaction(installation, refs, cancellable=None):
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    tx = Flatpak.Transaction.new_for_installation(installation, cancellable)
    tx.set_no_interaction(True)
    tx.set_no_pull(True)
    tx.set_disable_dependencies(True)
    tx.set_disable_related(True)
    tx.set_disable_prune(True)
    tx.set_include_unused_uninstall_ops(False)
    tx.set_force_uninstall(False)
    tx.connect('add-new-remote', lambda *_: False)
    def ready(transaction):
        operations = transaction.get_operations()
        return len(operations) == len(refs) and {op.get_ref() for op in operations} == set(refs) and all(
            op.get_operation_type() == Flatpak.TransactionOperationType.UNINSTALL
            and op.get_ref() in refs for op in operations)
    tx.connect('ready', ready)
    for ref in refs:
        tx.add_uninstall(ref)
    return tx


def reconcile(installations, cancellable=None, *, transaction=removal_transaction):
    removed = []
    for installation in installations:
        refs = retired_refs(installation.list_installed_refs(cancellable))
        if not refs:
            continue
        tx = transaction(installation, refs, cancellable)
        if not tx.run(cancellable):
            raise RuntimeError('A retired application could not be removed; it will be retried.')
        installation.drop_caches(cancellable)
        if retired_refs(installation.list_installed_refs(cancellable)):
            raise RuntimeError('A retired application remains installed; it will be retried.')
        removed.extend(refs)
    return tuple(removed)


@contextmanager
def _lock(path, owner):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != owner or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('Unsafe application retirement lock.')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def reconcile_current(*, system=False):
    if system != (os.geteuid() == 0):
        raise PermissionError('System retirement requires root; user retirement runs as the signed-in user.')
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    installations = (Flatpak.get_system_installations(None) if system else
                     (Flatpak.Installation.new_user(None),))
    if system:
        # Boot reconciliation and the baseline handoff may overlap. Serialize
        # their inventories and transactions so neither races an uninstall.
        with _lock(Path('/run/luma-retired-apps.lock'), 0):
            return reconcile(installations)
    return reconcile(installations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--system', action='store_true')
    scope.add_argument('--user', action='store_true')
    args = parser.parse_args()
    for ref in reconcile_current(system=args.system):
        print('Retired application removed; user data kept: ' + ref)


if __name__ == '__main__':
    main()
