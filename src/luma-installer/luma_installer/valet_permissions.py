"""Apply a reviewed Flatpak network choice without changing other overrides.

The package's declared permissions remain the starting point. Only an
explicit choice from Valet reaches this module. Flatpak owns the policy file;
its override command makes the field change, after Valet backs up the prior
user override. No fixture code imports or calls this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess

from .errors import InstallerError


_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_SUPPORTED = frozenset({"network"})


def network_declared(report) -> bool | None:
    """Read a bundle's declared network access; None means no metadata yet."""
    if report.kind != "flatpak":
        return None
    shared = report.details.get("Context · shared")
    if shared is None:
        return None
    return "network" in {item.strip() for item in shared.split(";")}


def _backup_once(app_id: str, data_home: Path) -> None:
    original = data_home / "flatpak" / "overrides" / app_id
    root = data_home / "luma" / "installer" / "permission-backups"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = root / (app_id + ".json")
    try:
        saved = backup.lstat()
    except FileNotFoundError:
        saved = None
    if saved is not None:
        if not stat.S_ISREG(saved.st_mode) or saved.st_mode & 0o077:
            raise InstallerError("The Flatpak permission backup is not private and regular.")
        try:
            document = json.loads(backup.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise InstallerError("The Flatpak permission backup is unreadable.") from error
        if not isinstance(document, dict) or document.get("flatpak_id") != app_id or "original_override" not in document:
            raise InstallerError("The Flatpak permission backup does not match this application.")
        return
    try:
        info = original.lstat()
    except FileNotFoundError:
        contents = None
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise InstallerError("The existing Flatpak override is not a regular file Valet can back up.")
        contents = original.read_text(encoding="utf-8")
    document = {"flatpak_id": app_id, "original_override": contents}
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(document, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def apply_flatpak_choices(record: dict, changes: dict[str, bool], *,
                          data_home: Path | None = None, runner=subprocess.run) -> None:
    """Apply only changed, supported choices to a user Flatpak installation.

    `changes` comes from a diff against what the ticket initially showed.
    An empty mapping makes no filesystem or Flatpak call.
    """
    if not changes:
        return
    if set(changes) - _SUPPORTED or any(type(value) is not bool for value in changes.values()):
        raise InstallerError("Valet received an unsupported permission choice.")
    if record.get("format") != "flatpak" or record.get("installation", "user") != "user":
        raise InstallerError("This app's permissions cannot be changed through Flatpak user overrides.")
    app_id = str(record.get("flatpak_id", ""))
    if not _APP_ID.fullmatch(app_id):
        raise InstallerError("The Flatpak application ID is invalid.")
    root = Path(data_home or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    _backup_once(app_id, root)
    for key, allowed in changes.items():
        flag = "--share=network" if allowed else "--unshare=network"
        try:
            result = runner(["flatpak", "override", "--user", flag, app_id],
                            capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InstallerError("The app was installed, but its network choice could not be saved. "
                                 "Review its permissions in Flatpak before opening it.") from error
        if result.returncode:
            raise InstallerError("The app was installed, but its network choice could not be saved. "
                                 "Review its permissions in Flatpak before opening it.")
