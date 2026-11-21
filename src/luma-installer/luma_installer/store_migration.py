# SPDX-License-Identifier: Apache-2.0
"""Move applications installed as capsules into the application store.

In the background, one application at a time (`luma-app-migrate.timer`):

1. **Build** the application's tree from the package file Valet kept when it
   was installed (or, for an AppImage or an archive, from its unpacked
   folder), check its libraries and start it once out of sight
   (`store_install.build_tree`). If any of that fails the capsule is left
   exactly as it is and the record says why; it is tried again when the
   installer itself has changed.
2. **Switch**, only while the application is closed: its data folder moves to
   the store's place for it (one rename), and its record names the tree. The
   record keeps everything it needs to go back. The application's identity
   does not change, so its launcher, its place on the dock, its category pair
   and the mark on its keyring entries (`capsule_identity.keyring_tag`) stay
   what they were: its saved passwords are found without being touched.
3. **First real start**, watched. If it opens, the capsule image (or the
   unpacked folder) and Valet's kept copy of the package are removed. If it
   fails while opening, the data folder moves back, the record is the
   capsule's again, the tree leaves the store and the application starts the
   way it did before.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import capsule_refresh, closure_launcher, store_install
from .app_store import Store
from .desktop import iter_records, read_record, safe_id, write_record
from .errors import InstallerError
from .safety import require_free_space, staging_root

# What a record carries only while it is a capsule.
CAPSULE_KEYS = ("image", "root", "command", "runtime_version", "missing_libraries", "refresh", "data_home",
                "desktop_path", "startup_wm_class", "chromium", "bus_names", "terminal", "mime_types", "toolkit")


def installer_release() -> int:
    from .app_host_compatibility import SUPPORTED_INSTALLER_RELEASE
    return int(SUPPORTED_INSTALLER_RELEASE)


def state(record: dict[str, object]) -> dict[str, object]:
    value = record.get("migration")
    return dict(value) if isinstance(value, dict) else {}


def eligible(record: dict[str, object]) -> bool:
    """A capsule, or an unpacked application, that has not been moved and has not failed with this installer."""
    kind = record.get("format")
    if kind not in store_install.KINDS or record.get("store") or record.get("system_receipt") or record.get("external_layered"):
        return False
    if record.get("kept") is False:
        return False  # an AppImage that only ran from its file
    if kind in {"deb", "rpm"} and not record.get("image"):
        return False
    current = state(record)
    return not (current.get("state") == "failed" and current.get("release") == installer_release())


def old_data(record: dict[str, object]) -> Path:
    return Path.home() / ".local/share/luma/installer/data" / str(record["sha256"])


def capsule_running(record: dict[str, object]) -> bool:
    """Whether a launcher of the capsule (podman or bubblewrap) is alive: each keeps a bus socket named for it."""
    if record.get("image") and capsule_refresh.is_running(record):
        return True
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    prefix = str(record.get("sha256") or "")[:24]
    try:
        entries = list((runtime / "luma-installer-bus").iterdir())
    except OSError:
        return False
    for entry in entries:
        head, _dash, tail = entry.name.rpartition("-")
        process = tail.split(".", 1)[0]
        if prefix and head == prefix and process.isdigit() and Path(f"/proc/{process}").exists():
            return True
    return False


def _kept_package(record: dict[str, object]) -> Path | None:
    try:
        folder = staging_root(str(record["sha256"]))
    except InstallerError:
        return None
    suffix = "." + str(record.get("format"))
    for path in sorted(folder.glob("*")) if folder.is_dir() else []:
        if path.is_file() and path.suffix.lower() == suffix and not path.name.startswith("."):
            return path
    return None


def _fail(application_id: str, stage: str, reason: str) -> str:
    record = read_record(application_id)
    write_record(application_id, {**record, "migration": {"state": "failed", "stage": stage, "reason": reason[:600],
                                                           "release": installer_release(), "unix": int(time.time())}})
    return f"stays a capsule ({stage}): {reason}"


def migrate(application_id: str, *, start: Callable[..., tuple[bool, str]] | None = None) -> str:
    """Build and try the application's tree; switch to it if the application is closed."""
    application_id = safe_id(application_id)
    with capsule_refresh._locked(application_id):
        record = read_record(application_id)
        if state(record).get("state") == "ready":
            return _switch_if_closed(record)
        if not eligible(record):
            return "not a capsule to move"
        kind = str(record["format"])
        store = Store()
        require_free_space(store.root if store.root.exists() else store.root.parent, 3 * 1024 ** 3, "move this application")
        options: dict[str, object] = {}
        if kind in {"deb", "rpm"}:
            package = _kept_package(record)
            if package is None:
                return _fail(application_id, "build", "The package file it was installed from is no longer kept.")
            options["package_file"] = package
        else:
            root = Path(str(record.get("root") or ""))
            if not root.is_dir():
                return _fail(application_id, "build", "Its unpacked folder is missing.")
            command = str(record.get("command") or "")
            options.update(payload=root, sha256=str(record.get("sha256") or ""), display_name=str(record.get("name") or ""),
                           package=str(record.get("name") or application_id),
                           command=f"/opt/{application_id}/{command.removeprefix('/app/')}" if kind == "portable" else "")
        try:
            built = store_install.build_tree(store, application_id, kind, record=record, start=start, **options)  # type: ignore[arg-type]
        except store_install.Fallback as failure:
            return _fail(application_id, failure.stage, failure.reason)
        record = read_record(application_id)
        write_record(application_id, {**record, "migration": {"state": "ready", "commit": built["commit"],
                                                               "graphics": built.get("graphics"), "unix": int(time.time())}})
        return _switch_if_closed(read_record(application_id))


def _switch_if_closed(record: dict[str, object]) -> str:
    if capsule_running(record):
        return "ready; waits until the application is closed"
    switch(record)
    return "moved; kept as a capsule until its first start"


def switch(record: dict[str, object]) -> dict[str, object]:
    """Point the record at the tree and move the data folder. The application must be closed."""
    application_id = str(record["application_id"])
    current = state(record)
    store = Store()
    ref = store.app_ref(application_id)
    commit = store.resolve(ref)
    if current.get("state") != "ready" or not commit:
        raise InstallerError("There is no tried tree to switch this application to.")
    tree = store.read_record(ref)
    checkout, _tree = closure_launcher.deployment(store, application_id)
    source, destination = old_data(record), closure_launcher.data_home(application_id)
    moved = False
    if source.is_dir() and not destination.exists():
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.rename(source, destination)
        moved = True
    try:
        built = {"commit": commit, "tree": tree, "graphics": current.get("graphics")}
        kept = {key: record[key] for key in ("mcs_level", "keyring", "kept", "installed_unix", "version", "publisher", "source",
                                              "source_bytes", "reviewed_details", "destination", "comment") if key in record}
        updated = store_install.described(application_id, str(record["format"]), built, str(record.get("sha256") or ""),
                                          package=str(record.get("package") or ""), fallback_name=str(record.get("name") or ""),
                                          kept=kept)
        updated["legacy_home"] = True
        updated["migration"] = {"state": "unconfirmed", "unix": int(time.time()), "data_moved": moved,
                                "capsule": {key: record[key] for key in (*CAPSULE_KEYS, "name", "icon", "exported_command",
                                                                         "command_not_exported", "launcher_metadata_version")
                                            if key in record}}
        exports = dict(tree.get("exports") or {})  # type: ignore[arg-type]
        return store_install.publish(application_id, updated, checkout, str(exports.get("icon") or ""))
    except Exception:
        if moved:
            os.rename(destination, source)
        raise


def unconfirmed(record: dict[str, object]) -> bool:
    return bool(record.get("store")) and state(record).get("state") == "unconfirmed"


def before_start(record: dict[str, object]) -> dict[str, object]:
    """A tree that was ready while the application was open is switched to before it starts again."""
    if record.get("store"):
        # A removal that was interrupted (the session ended while a capsule image was being deleted) is finished here.
        return finish_removal(record) if state(record).get("leaving") else record
    if state(record).get("state") != "ready" or capsule_running(record):
        return record
    return switch(record)


def confirm(record: dict[str, object]) -> dict[str, object]:
    """The application opened from the store: what it was before is removed.

    The record says so first. Removing a capsule image takes a while, and a
    launcher that is stopped meanwhile (the session ending) must not leave a
    record that could still go back to an image that is gone.
    """
    application_id = str(record["application_id"])
    stored = read_record(application_id)
    capsule = dict(state(stored).get("capsule") or {})  # type: ignore[arg-type]
    root = str(capsule.get("root") or "")
    leaving = {"images": capsule_refresh.images(capsule), "root": root}
    updated = {**stored, "migration": {"state": "done", "unix": int(time.time()), "from": str(capsule.get("image") or root),
                                       "leaving": leaving}}
    write_record(application_id, updated)
    return finish_removal(updated)


def finish_removal(record: dict[str, object]) -> dict[str, object]:
    """Remove what a confirmed move still has to remove; safe to run again after an interruption."""
    current = state(record)
    leaving = current.get("leaving")
    if current.get("state") != "done" or not isinstance(leaving, dict):
        return record
    for image in leaving.get("images") or []:
        subprocess.run(["podman", "image", "rm", "--force", str(image)], capture_output=True, check=False)
    root = Path(str(leaving.get("root") or ""))
    home = Path.home() / ".local/share/luma"
    for boundary, name in ((home / "appimages", "AppDir"), (home / "portable", "app")):
        if root.name == name and root.parent.parent == boundary and root.parent.is_dir():
            shutil.rmtree(root.parent, ignore_errors=True)
    sha256 = str(record.get("sha256") or "")
    if len(sha256) == 64:
        shutil.rmtree(Path.home() / ".local/share/luma/installer/staging" / sha256, ignore_errors=True)
    application_id = str(record["application_id"])
    stored = read_record(application_id)
    updated = {**stored, "migration": {key: value for key, value in state(stored).items() if key != "leaving"}}
    write_record(application_id, updated)
    return updated


def revert(application_id: str, reason: str) -> dict[str, object]:
    """Back to the capsule: data folder, record and store as they were before the switch."""
    application_id = safe_id(application_id)
    record = read_record(application_id)
    current = state(record)
    capsule = dict(current.get("capsule") or {})  # type: ignore[arg-type]
    if current.get("state") != "unconfirmed" or not capsule:
        raise InstallerError("This application has no capsule to go back to.")
    source, destination = closure_launcher.data_home(application_id), old_data(record)
    if current.get("data_moved") and source.is_dir() and not destination.exists():
        os.rename(source, destination)
    restored = {key: value for key, value in record.items()
                if key not in {"store", "legacy_home", "vendor_repositories", "migration", *CAPSULE_KEYS}}
    restored.update(capsule)
    restored["migration"] = {"state": "failed", "stage": "first start", "reason": reason[:600],
                             "release": installer_release(), "unix": int(time.time())}
    write_record(application_id, restored)
    store = Store()
    try:
        store.remove(application_id)
    except InstallerError:
        pass
    if not current.get("data_moved"):
        # The capsule had no data folder; what the one start from the store wrote goes with the tree.
        shutil.rmtree(source, ignore_errors=True)
    closure_launcher.remove_runtime(application_id)
    return restored


def migrate_all(log: Callable[[str], None] = print) -> int:
    """Every capsule that can be moved, one after the other. Returns how many stayed capsules."""
    if not store_install.enabled():
        log("the application store is switched off on this device")
        return 0
    if capsule_refresh.metered():
        log("metered connection; applications are not moved now")
        return 0
    stayed = 0
    for record in iter_records():
        if not eligible(record) and state(record).get("state") != "ready":
            continue
        application_id = str(record.get("application_id"))
        try:
            result = migrate(application_id)
        except (InstallerError, OSError) as error:
            result = f"not moved: {error}"
        stayed += not result.startswith(("moved", "ready"))
        log(f"{application_id}: {result}")
    return stayed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-app-migrate", description="Move installed capsules to the application store")
    parser.add_argument("application_id", nargs="?")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--status", action="store_true")
    values = parser.parse_args(argv)
    try:
        if values.status:
            rows = {str(r.get("application_id")): {"in_store": bool(r.get("store")), **state(r),
                                                    **({"store_fallback": r["store_fallback"]} if r.get("store_fallback") else {})}
                    for r in iter_records() if r.get("format") in store_install.KINDS
                    and values.application_id in (None, r.get("application_id"))}
            for row in rows.values():
                row.pop("capsule", None)
            print(json.dumps(rows, indent=2, sort_keys=True))
            return 0
        if values.all:
            migrate_all(log=lambda line: print(line, flush=True))
            return 0
        if not values.application_id:
            parser.error("name an application or pass --all")
        print(migrate(values.application_id))
        return 0
    except InstallerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
