# SPDX-License-Identifier: Apache-2.0
"""Updates for applications in the store (ADR-052, Applications 7).

The same life as a capsule's update (capsule_refresh.py), with trees for
images, and the same state in the record, so one schedule
(`luma-capsule-refresh.timer`), one list (`manager.update_states`) and one set
of commands (`luma-appctl hold | release | rollback | updates`) serve both.

1. **Ask the distribution.** In a private copy of the tree, the release's own
   package manager says what would change, against its repositories and the
   ones the package added for itself at install (recorded with their keys in
   the tree's record: a vendor's own updates arrive the same way).
2. **Build a new tree** from the record when something changed, or when the
   recorded baseline moved. It is committed beside the tree in use, on a
   waiting ref, with the tree in use as its parent.
3. **Hidden start** of the new tree. One that fails is deleted and not
   offered again.
4. **Swap between runs.** The application's ref moves to the new tree only
   while no launcher of the application holds its lock; otherwise the tree
   waits and the launcher swaps before the next start.
5. **Rollback.** The previous tree stays behind the ref. The first real start
   on a new tree is watched; if it fails while opening, the ref goes back,
   the application starts again from the tree that worked, and that package
   set is refused from then on. `rollback` does the same on request.
6. **Hold.** A held application is not refreshed until released.
"""
from __future__ import annotations

import hashlib
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import capsule_refresh, closure, closure_launcher, launch_guard, store_install
from .app_store import Store
from .desktop import read_record, safe_id, write_record
from .errors import InstallerError
from .safety import require_free_space


def managed(record: dict[str, object]) -> bool:
    return isinstance(record.get("store"), dict) and bool(record["store"].get("id"))  # type: ignore[union-attr]


def app_id(record: dict[str, object]) -> str:
    return str(dict(record["store"])["id"])  # type: ignore[arg-type]


def is_running(record: dict[str, object]) -> bool:
    return closure_launcher.is_running(app_id(record))


def changes_fingerprint(changes: list[str], baseline_version: int) -> str:
    return hashlib.sha256("\n".join([*sorted(changes), f"baseline {baseline_version}"]).encode()).hexdigest()[:24]


def _payload(store: Store, tree: dict[str, object], commit: str, holder: Path) -> Path | None:
    """An unpacked application's own folder, out of the tree it is in: it is rebuilt around the same payload."""
    if tree.get("format") not in {"appimage", "portable"}:
        return None
    destination = holder / "payload"
    store._ostree("checkout", "--user-mode", f"--subpath=/opt/{tree['id']}", commit, str(destination), userns=True)
    return destination


def refresh(application_id: str, *, force: bool = False,
            start: Callable[..., tuple[bool, str]] | None = None) -> str:
    """Bring one application's tree up to date. Returns its status afterwards."""
    application_id = safe_id(application_id)
    with capsule_refresh._locked(application_id):
        record = read_record(application_id)
        if not managed(record):
            raise InstallerError("This application is not in the application store.")
        current = capsule_refresh.state(record)
        if current.get("held") and not force:
            return "held"
        store, app = Store(), app_id(record)
        ref = store.app_ref(app)
        in_use = store.resolve(ref)
        base = str(current.get("pending_commit") or "")
        base = base if store.has_commit(base) else in_use
        if not base:
            raise InstallerError("The application's tree is missing from the store.")
        tree = store.read_record(base)
        work, cache = store_install.scratch()
        log = closure.BuildLog(store_install.log_path(application_id))
        holder = Path(tempfile.mkdtemp(prefix=f"refresh-{app}-", dir=work))
        try:
            try:
                store.export(base, holder / "root")
                resolved = closure.resolve_changes(tree, holder / "root", cache, log)
            except InstallerError as error:
                capsule_refresh._save(application_id, checked_unix=int(time.time()), detail=str(error)[:300])
                raise
            changes = [f"{name} {old or 'absent'} -> {new}" for name, old, new in resolved["changes"]]  # type: ignore[misc]
            wanted_baseline = int(closure.baseline_for(str(tree["userland"]))["version"])  # type: ignore[call-overload]
            stale = bool(tree.get("windowed", True)) and int(tree.get("baseline_version") or 0) < wanted_baseline
            checked = {"checked_unix": int(time.time()), "vendor_repositories": store_install.vendor_repositories(tree)}
            if not changes and not stale:
                capsule_refresh._save(application_id, **checked, status=current.get("status") or "current", detail="")
                return str(current.get("status") or "current")
            fingerprint = changes_fingerprint(changes, wanted_baseline)
            if current.get("refused") == fingerprint:
                # The same packages that already failed here; nothing new to try.
                capsule_refresh._save(application_id, **checked)
                return str(current.get("status") or "current")
            require_free_space(store.root, 2 * 1024 ** 3, "update this application")
            spec = closure.spec_from_record(tree, store.kept_package(tree))
            if spec.package_file and any(name.partition(":")[0] == spec.package.partition(":")[0]
                                         for name, _old, _new in resolved["changes"]):  # type: ignore[misc]
                # A repository of the tree now carries a newer build of the package itself. Its package
                # manager would take that one; rebuilding from the kept file would bring the old one
                # back and the same update would be found again at every check.
                spec.package_file, spec.from_repository = "", True
            pending = store.pending_ref(app)
            try:
                built = store_install.build_tree(store, app, str(tree["format"]), spec=spec,
                                                 payload=_payload(store, tree, base, holder), record=record,
                                                 ref=pending, parent=in_use, start=start)
            except store_install.Fallback as failure:
                capsule_refresh._save(application_id, **checked, status="update failed", refused=fingerprint,
                                      detail=f"The updated application was not used ({failure.stage}): {failure.reason}"[:400])
                return "update failed"
        finally:
            closure.remove_tree(holder)
        capsule_refresh._save(application_id, **checked, pending_commit=built["commit"], pending_packages=changes[:200],
                              pending_fingerprint=fingerprint, detail=str(built["why"]))
        return str(capsule_refresh.summary(apply_pending(read_record(application_id))).get("status"))


def apply_pending(record: dict[str, object], running: bool | None = None) -> dict[str, object]:
    """Move the application to the waiting tree, if there is one and the application is closed."""
    current = capsule_refresh.state(record)
    pending = str(current.get("pending_commit") or "")
    if not pending or not managed(record):
        return record
    if running if running is not None else is_running(record):
        return record
    application_id = str(record["application_id"])
    store, app = Store(), app_id(record)
    ref = store.app_ref(app)
    if not store.has_commit(pending):
        return capsule_refresh._save(application_id, pending_commit=None, pending_packages=None, pending_fingerprint=None)
    previous = store.resolve(ref)
    store.promote(ref, pending)
    store.delete_ref(store.pending_ref(app))
    # The checkout is composed now, not at the first start on the new tree.
    closure_launcher.deployment(store, app)
    stored = read_record(application_id)
    refresh_state = {**capsule_refresh.state(stored), "previous_commit": previous, "status": "updated", "confirmed": False,
                     "updated_unix": int(time.time()),
                     "packages_updated": len(current.get("pending_packages") or []),  # type: ignore[arg-type]
                     "packages": list(current.get("pending_packages") or [])[:200],  # type: ignore[call-overload]
                     "fingerprint": current.get("pending_fingerprint")}
    for key in ("pending_commit", "pending_packages", "pending_fingerprint"):
        refresh_state.pop(key, None)
    updated = {**stored, "store": {**dict(stored["store"]), "commit": pending}, "refresh": refresh_state}  # type: ignore[arg-type]
    write_record(application_id, updated)
    store.collect()
    return {**record, "store": updated["store"], "refresh": refresh_state}


def rollback(application_id: str, reason: str = "") -> dict[str, object]:
    """Go back to the previous tree and do not take that update again."""
    record = read_record(safe_id(application_id))
    current = capsule_refresh.state(record)
    store, app = Store(), app_id(record)
    previous = store.rollback(store.app_ref(app))
    closure_launcher.deployment(store, app)
    refresh_state = {**current, "status": "rolled back", "confirmed": True, "refused": current.get("fingerprint"),
                     "detail": (reason or "Rolled back to the previous version.")[:400], "rolled_back_unix": int(time.time())}
    for key in ("previous_commit", "packages", "packages_updated", "fingerprint"):
        refresh_state.pop(key, None)
    updated = {**record, "store": {**dict(record["store"]), "commit": previous}, "refresh": refresh_state}  # type: ignore[arg-type]
    write_record(str(record["application_id"]), updated)
    if not is_running(record):
        store.collect()
    return updated


# -- Around a start --------------------------------------------------------------------

def before_start(record: dict[str, object]) -> dict[str, object]:
    """What waits for the application to be closed is done here, before it starts."""
    from . import store_migration
    try:
        record = store_migration.before_start(record)
        if managed(record):
            record = apply_pending(record)
    except (InstallerError, OSError, KeyError):
        pass  # opening what is installed never waits on housekeeping
    return record


def unconfirmed(record: dict[str, object]) -> bool:
    """Whether this start is the first real one on a new tree (an update, or a move to the store)."""
    from . import store_migration
    return capsule_refresh.unconfirmed(record) or store_migration.unconfirmed(record)


def after_watched_start(record: dict[str, object], status: int, failure: launch_guard.Failure | None,
                        extra: list[str] | None) -> int:
    """The first start on a new tree has ended: keep the tree, or go back and start again."""
    from . import launcher, store_migration
    application_id = str(record["application_id"])
    name = str(record.get("name") or application_id)
    moved = store_migration.unconfirmed(record)
    if failure is None:
        try:
            store_migration.confirm(record) if moved else capsule_refresh.confirm(record)
        except (InstallerError, OSError):
            pass
        return status
    why = launch_guard.summary(name, failure)
    try:
        if moved:
            store_migration.revert(application_id, f"It failed on its first start from the store: {why}")
            return launcher.main([application_id, *(extra or [])])
        previous = rollback(application_id, f"The updated application failed on its first start: {why}")
    except (InstallerError, OSError, KeyError):
        launch_guard.report(name, application_id, str(record.get("icon") or ""), failure)
        return status
    return closure_launcher.launch(Store(), app_id(previous), extra, record=previous)
