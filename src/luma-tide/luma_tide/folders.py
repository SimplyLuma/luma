# SPDX-License-Identifier: Apache-2.0
"""Folders Tide can play from without a file chooser: the device's Music folder
and every mounted removable drive (an SD card, a USB stick, a second disk).

udisks2 mounts removable media under /run/media/<user>/<label>; distributions
without it use /media/<user>/<label> or /media/<label>. Only mounted
directories are offered, and nothing already in the library is offered twice.
"""
from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class FolderChoice:
    path: Path
    name: str
    subtitle: str
    icon: str
    removable: bool


def media_roots(user: str | None = None) -> tuple[Path, ...]:
    """Where this system mounts removable media for `user`, most specific first."""
    user = user or _user()
    return (Path('/run/media') / user, Path('/media') / user, Path('/media'))


def _user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return str(os.getuid())


def removable_root(path: Path, roots: Sequence[Path] | None = None) -> Path | None:
    """The mounted drive `path` lives on, or None for the device's own storage."""
    path = Path(path)
    for root in roots if roots is not None else media_roots():
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            return root / relative.parts[0]
    return None


def removable_drives(roots: Sequence[Path] | None = None) -> list[Path]:
    """Mounted removable drives, in name order; a drive appears once."""
    roots = list(roots if roots is not None else media_roots())
    found: dict[str, Path] = {}
    for root in roots:
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for entry in entries:
            # /media/<user> is itself a directory under /media: a root, never a drive.
            if entry.name.startswith('.') or any(entry == other for other in roots):
                continue
            try:
                if not entry.is_dir() or not os.access(entry, os.R_OK | os.X_OK):
                    continue
                key = str(entry.resolve())
            except OSError:
                continue
            found.setdefault(key, entry)
    return list(found.values())


def _known(path: Path, existing: Iterable[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for other in existing:
        try:
            if Path(other).resolve() == resolved:
                return True
        except OSError:
            if Path(other) == path:
                return True
    return False


def folder_choices(existing: Iterable[Path], *, music: Path | None = None,
                   roots: Sequence[Path] | None = None) -> list[FolderChoice]:
    """The device's Music folder (if it is not a source yet) and each mounted drive."""
    existing = [Path(p) for p in existing]
    choices: list[FolderChoice] = []
    if music is not None and music.is_dir() and not _known(music, existing):
        choices.append(FolderChoice(music, 'Music on this device', _display(music), 'folder', False))
    for drive in removable_drives(roots):
        if _known(drive, existing):
            continue
        choices.append(FolderChoice(drive, drive.name, 'Removable drive', 'hard-drive', True))
    return choices


def _display(path: Path) -> str:
    home = Path.home()
    try:
        return '~/' + str(path.relative_to(home)) if path != home else '~'
    except ValueError:
        return str(path)
