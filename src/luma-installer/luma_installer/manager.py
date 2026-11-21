from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import capsule_refresh
from .desktop import (applications_root, iter_records, records_root, remove_command, remove_record,
                      safe_id)
from .errors import InstallerError
from .removal import refuse_protected
from .safety import cleanup_abandoned_user_transactions
from .progress import Cancelled, current


def _run(arguments: list[str], *, timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    try:
        transaction = current.get()
        if transaction is None or not transaction.cancellable:
            result = subprocess.run(arguments, check=False, capture_output=True, text=True, timeout=timeout)
        else:
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            def stop() -> None:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            transaction.cancel_callback = stop
            try:
                deadline = time.monotonic() + timeout
                cancel_deadline = None
                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if transaction.cancelled.is_set() and process.poll() is None:
                            if cancel_deadline is None:
                                stop()
                                cancel_deadline = time.monotonic() + 3
                            elif time.monotonic() >= cancel_deadline:
                                try:
                                    os.killpg(process.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                        if time.monotonic() >= deadline:
                            stop()
                            process.communicate(timeout=5)
                            raise subprocess.TimeoutExpired(arguments, timeout)
                if transaction.cancelled.is_set() and process.returncode != 0:
                    raise Cancelled('Application management was interrupted.')
                result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
            finally:
                transaction.cancel_callback = None
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallerError(f"Application management could not complete: {error}") from error
    if result.returncode != 0:
        message = next((line.strip() for line in reversed((result.stderr + result.stdout).splitlines())
                        if line.strip()), "Application management failed")
        raise InstallerError(message)
    return result


def _record(application_id: str) -> dict[str, object]:
    reconcile()
    target = safe_id(application_id)
    for record in iter_records():
        if record.get("application_id") == target:
            return record
    raise InstallerError(f"No Luma-managed application is named {application_id}.")


def _safe_remove_tree(value: object, root: Path) -> None:
    if not isinstance(value, (str, Path)):
        return
    try:
        candidate = Path(value).resolve(strict=False)
        boundary = root.resolve(strict=False)
    except OSError as error:
        raise InstallerError(f"The managed path could not be resolved: {error}") from error
    if candidate == boundary or not candidate.is_relative_to(boundary):
        raise InstallerError("The application record points outside Luma-managed storage.")
    if not candidate.exists():
        return
    try:
        shutil.rmtree(candidate)
    except OSError as error:
        raise InstallerError(f"The application's managed files could not be removed: {error}") from error


def _remove_exports(application_id: str, exported_command: object = None) -> None:
    desktop = applications_root() / f"org.projectluma.Installed.{safe_id(application_id)}.desktop"
    desktop.unlink(missing_ok=True)
    icon_home = Path.home() / ".local/share/icons/hicolor"
    for path in icon_home.glob(f"*/apps/{safe_id(application_id)}.*"):
        path.unlink(missing_ok=True)
    remove_command(exported_command)


def _remove_from_store(record: dict[str, object]) -> None:
    """Take an application's trees out of the store, and what a move from a capsule still kept beside them."""
    from . import closure_launcher, store_install
    from .app_store import Store
    application_id = str(dict(record["store"])["id"])  # type: ignore[arg-type]
    store = Store()
    try:
        store.remove(application_id)
    except InstallerError:
        pass  # already gone from the store; the record and exports still have to go
    closure_launcher.remove_runtime(application_id)
    store_install.log_path(application_id).unlink(missing_ok=True)
    try:
        store_install.log_path(application_id).parent.rmdir()
    except OSError:
        pass
    migration = record.get("migration")
    capsule = dict(migration.get("capsule") or {}) if isinstance(migration, dict) else {}
    for image in capsule_refresh.images(capsule):
        subprocess.run(["podman", "image", "rm", "--force", image], check=False, capture_output=True)
    if not store.root.exists():
        # The last application from the store: its download cache has nothing left to serve.
        shutil.rmtree(Path(os.environ.get("LUMA_APP_SCRATCH") or Path.home() / ".cache/luma/app-closures"), ignore_errors=True)


def reconcile() -> None:
    """Forget RPM removals once the rebooted deployment proves them absent."""
    cleanup_abandoned_user_transactions()
    for record in iter_records():
        if record.get("format") != "rpm" or record.get("state") != "removal-pending-restart":
            continue
        application_id = str(record.get("application_id", ""))
        name = record.get("package_name", record.get("name"))
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,127}", name):
            continue
        result = subprocess.run(
            ["rpm", "-q", "--", name], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != 1:
            continue
        _remove_exports(application_id, record.get("exported_command"))
        sha256 = record.get("sha256")
        if isinstance(sha256, str) and len(sha256) == 64:
            _safe_remove_tree(
                Path.home() / ".local/share/luma/installer/staging" / sha256,
                Path.home() / ".local/share/luma/installer/staging",
            )
        remove_record(application_id)


def remove(application_id: str, delete_data: bool = False) -> str:
    record = _record(application_id)
    refuse_protected(record)
    kind = str(record.get("format", ""))
    if kind == "flatpak":
        location = record.get("installation", "user")
        if location not in {"user", "system"}:
            raise InstallerError("The Flatpak installation identity is invalid.")
        command = ["flatpak", "uninstall", "--"+location, "--noninteractive", "--assumeyes"]
        if delete_data:
            command.append("--delete-data")
        command.append(str(record["flatpak_id"]))
        _run(command)
    elif kind == "flatpakrepo":
        _run(["flatpak", "remote-delete", "--user", str(record["remote"])])
    elif kind == 'rpm' and record.get('external_layered'):
        _run(['pkexec', '/usr/libexec/luma-installer-system', 'remove-layered',
              str(record['desktop_id']), str(record['package_name'])])
        present = subprocess.run(['rpm', '-q', '--', str(record['package_name'])],
                                 check=False, capture_output=True, text=True, timeout=30)
        if present.returncode != 1:
            record['state'] = 'removal-pending-restart'
            (records_root() / f'{safe_id(application_id)}.json').write_text(
                json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            return 'Removal is staged. Restart to finish. Personal settings and files are retained.'
        remove_record(application_id)
        return 'Removed. Personal settings and files are retained.'
    elif kind in {"snap", "rpm"} and "system_receipt" in record:
        _run(["pkexec", "/usr/libexec/luma-installer-system", "remove", str(record["system_receipt"])])
        if kind == "rpm":
            record["state"] = "removal-pending-restart"
            record_path = records_root() / f"{safe_id(application_id)}.json"
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return "Removal is staged. Restart to enter the deployment without this system component."
    elif capsule_refresh.in_store(record):
        _remove_from_store(record)
    elif kind in {"deb", "rpm"}:
        _run(["podman", "image", "rm", "--force", str(record["image"])])
        capsule_refresh.forget(record)
    elif kind == "appimage":
        root = record.get("root")
        if isinstance(root, (str, Path)) and Path(root).name == "AppDir":
            root = Path(root).parent
        _safe_remove_tree(root, Path.home() / ".local/share/luma/appimages")
    elif kind == "portable":
        root = record.get("root")
        if isinstance(root, (str, Path)) and Path(root).name == "app":
            root = Path(root).parent
        _safe_remove_tree(root, Path.home() / ".local/share/luma/portable")
        if record.get("image"):
            _run(["podman", "image", "rm", "--force", str(record["image"])])
            capsule_refresh.forget(record)
    else:
        raise InstallerError(f"Removal is not implemented for {kind or 'this record'}.")

    _remove_exports(application_id, record.get("exported_command"))
    sha256 = record.get("sha256")
    if isinstance(sha256, str) and len(sha256) == 64:
        _safe_remove_tree(
            Path.home() / ".local/share/luma/installer/staging" / sha256,
            Path.home() / ".local/share/luma/installer/staging",
        )
        if delete_data:
            _safe_remove_tree(
                Path.home() / ".local/share/luma/installer/data" / sha256,
                Path.home() / ".local/share/luma/installer/data",
            )
            if record.get("data_home"):
                _safe_remove_tree(record["data_home"], Path.home() / ".local/share/luma/apps-data")
                try:
                    (Path.home() / ".local/share/luma/apps-data").rmdir()
                except OSError:
                    pass
            if kind in {"deb", "rpm", "appimage", "portable"}:
                # Its saved passwords are part of its data.
                try:
                    from . import capsule_identity, capsule_secrets
                    capsule_secrets.forget(capsule_identity.keyring_tag(record))
                except Exception:  # noqa: BLE001 - never a reason to keep the application
                    pass
    remove_record(application_id)
    if kind == "snap":
        return "Removed. snapd manages the retained snapshot according to system policy."
    return "Removed. Private application data was deleted." if delete_data else "Removed. Private application data was retained."


def update(application_id: str) -> str:
    record = _record(application_id)
    kind = str(record.get("format", ""))
    if kind == "flatpak":
        _run(["flatpak", "update", "--user", "--noninteractive", "--assumeyes", str(record["flatpak_id"])])
        return "Flatpak checked its signed origin and applied the newest compatible update."
    if capsule_refresh.managed(record):
        status = capsule_refresh.refresh(str(record["application_id"]), force=True)
        return {
            "updated": "The capsule's packages were updated from their signed repositories. "
                       "The previous capsule is kept in case this one fails.",
            "update waiting": "An updated capsule is ready and will be used the next time the application starts.",
            "update failed": "The updated capsule did not start, so the application stays as it was.",
            "held": "Updates are held for this application.",
        }.get(status, "The capsule's packages are already up to date.")
    if kind in {"appimage", "portable", "deb", "rpm", "snap"}:
        raise InstallerError("This local package has no trusted update origin. Open a newer package to review and replace it.")
    raise InstallerError(f"Updates are not implemented for {kind or 'this record'}.")


def update_states(application_id: str | None = None) -> dict[str, dict[str, object]]:
    """Per application: updated, held, rolled back, and what it would take to go back.

    What the list of applications in Settings and Valet reads; see
    capsule_refresh.summary for the fields.
    """
    wanted = safe_id(application_id) if application_id else None
    return {str(record.get("application_id")): capsule_refresh.summary(record) for record in iter_records()
            if capsule_refresh.managed(record) and wanted in (None, record.get("application_id"))}


def _energy(values) -> int:
    """Publish what Luma does to applications it did not write.

    Luma starts some Chromium and Electron applications with switches their
    publisher did not choose, because the override is plainly better for the
    person. That licence is only defensible if the person can see it, so this
    is where it is said in words rather than in flags.
    """
    from . import capsule_runtime as runtime_policy

    if values.undo or values.redo:
        if not values.application_id:
            print("error: name the application to change.", file=sys.stderr)
            return 1
        group = values.undo or values.redo
        if group != "all" and group not in runtime_policy.FLAG_GROUPS:
            known = ", ".join(sorted(runtime_policy.FLAG_GROUPS))
            print(f"error: unknown setting {group!r}; try {known} or all.", file=sys.stderr)
            return 1
        if values.undo:
            runtime_policy.record_flag_backoff(values.application_id, group)
            print(f"Luma will no longer change {group} for {values.application_id}. "
                  f"It takes effect the next time the application starts.")
        else:
            _forget_backoff(values.application_id, group)
            print(f"Luma will change {group} for {values.application_id} again.")
        return 0

    records = iter_records()
    if values.application_id:
        records = [r for r in records if r.get("application_id") == values.application_id]
        if not records:
            print(f"error: no application {values.application_id!r} is installed.", file=sys.stderr)
            return 1

    shown = 0
    for record in records:
        if not record.get("chromium"):
            continue
        shown += 1
        identity = str(record.get("application_id") or "")
        print(f"{record.get('name') or identity}  ({identity})")
        for row in runtime_policy.flag_overrides(identity):
            mark = "on " if row["active"] else "off"
            print(f"   [{mark}] {row['title']}")
            print(f"         {row['explanation']}")
            if not row["active"] and row["withdrawn_because"]:
                print(f"         {row['withdrawn_because']}")
        print()
    if not shown:
        print("Luma is not changing how any installed application starts.")
        return 0
    print("Change any of these with:  luma-appctl energy <application> --undo <setting>")
    print("Skip them once without recording anything:  LUMA_ENERGY_FLAGS=off <command>")
    return 0


def _forget_backoff(application_id: str, group: str) -> None:
    """Remove the person's own entry, so the setting comes back."""
    from . import capsule_runtime as runtime_policy
    path = runtime_policy._person_flag_exceptions()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    kept = []
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if fields and fields[0] == application_id:
            if group == "all" or group in fields[1:] or len(fields) == 1:
                continue
        kept.append(line)
    try:
        path.write_text("\n".join(kept) + ("\n" if kept else ""))
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-appctl", description="Manage applications installed through Luma")
    subparsers = parser.add_subparsers(dest="action", required=True)
    listing = subparsers.add_parser("list")
    listing.add_argument("--json", action="store_true")
    updating = subparsers.add_parser("update")
    updating.add_argument("application_id")
    removing = subparsers.add_parser("remove")
    removing.add_argument("application_id")
    removing.add_argument("--delete-data", action="store_true")
    energy = subparsers.add_parser(
        "energy", help="what Luma changes about how applications start, and how to stop it")
    energy.add_argument("application_id", nargs="?")
    energy.add_argument("--undo", metavar="GROUP", nargs="?", const="all",
                        help="stop doing this to the application: video-decode, wayland, or all")
    energy.add_argument("--redo", metavar="GROUP", nargs="?", const="all",
                        help="start doing it again")
    for name, text in (("hold", "do not update this application's capsule until it is released"),
                       ("release", "let this application's capsule be updated again"),
                       ("rollback", "go back to the capsule this application had before its last update")):
        subparsers.add_parser(name, help=text).add_argument("application_id")
    status = subparsers.add_parser("updates", help="what has been updated, held or rolled back")
    status.add_argument("application_id", nargs="?")
    values = parser.parse_args(argv)
    try:
        if values.action in {"hold", "release"}:
            capsule_refresh.hold(str(_record(values.application_id)["application_id"]), values.action == "hold")
            print("Updates are held." if values.action == "hold" else "Updates are released.")
            return 0
        if values.action == "rollback":
            capsule_refresh.rollback(str(_record(values.application_id)["application_id"]), "Rolled back on request.")
            print("Rolled back to the previous capsule.")
            return 0
        if values.action == "updates":
            print(json.dumps(update_states(values.application_id), indent=2, sort_keys=True))
            return 0
        if values.action == "list":
            reconcile()
            records = iter_records()
            if values.json:
                print(json.dumps(records, indent=2, sort_keys=True))
            else:
                for record in records:
                    line = f"{record.get('application_id')}\t{record.get('format')}\t{record.get('name')}"
                    if record.get("exported_command"):
                        line += f"\tcommand: {record['exported_command']}"
                    elif record.get("command_not_exported"):
                        line += f"\tno command: {record['command_not_exported']}"
                    print(line)
            return 0
        if values.action == "update":
            print(update(values.application_id))
            return 0
        if values.action == "remove":
            print(remove(values.application_id, values.delete_data))
            return 0
        if values.action == "energy":
            return _energy(values)
    except (InstallerError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
