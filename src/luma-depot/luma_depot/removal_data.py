# SPDX-License-Identifier: Apache-2.0
"""Backup and remove the private files of an opt-in Flatpak uninstall.

The transaction that uninstalls the application is elsewhere. Its caller
must finish a backup before running that transaction and only delete data
after it succeeds. Default uninstall never calls these functions.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import time
import uuid


_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def _require_unlinked_parents(path: Path, home: Path) -> None:
    node = path
    while node != home:
        if node.is_symlink():
            raise ValueError(f"Private data path contains a link: {node.name}")
        if node.parent == node:
            raise ValueError("Private data path leaves the home directory")
        node = node.parent


def private_data_path(app_id: str, home: Path) -> Path:
    if not _APP_ID.fullmatch(app_id) or app_id in {".", ".."}:
        raise ValueError("Invalid Flatpak application ID")
    root = home / ".var/app"
    _require_unlinked_parents(root, home)
    path = root / app_id
    if path.is_symlink() or path.exists() and path.resolve().parent != root.resolve():
        raise ValueError("Flatpak data path leaves its private root")
    return path


def backup_private_data(app_id: str, home: Path) -> Path | None:
    source = private_data_path(app_id, home)
    if not source.exists():
        return None
    root = home / ".local/share/luma/depot/backups"
    _require_unlinked_parents(root, home)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_mode & 0o077:
        raise ValueError("Private data backup directory is not private")
    destination = root / f"{app_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    shutil.copytree(source, destination, symlinks=True)
    return destination


def remove_backed_up_data(app_id: str, home: Path, backup: Path | None) -> None:
    if backup is None:
        return
    root = (home / ".local/share/luma/depot/backups").resolve()
    _require_unlinked_parents(home / ".local/share/luma/depot/backups", home)
    if not backup.is_dir() or backup.is_symlink() or backup.resolve().parent != root:
        raise ValueError("Private data backup is missing")
    source = private_data_path(app_id, home)
    if source.exists():
        shutil.rmtree(source)
