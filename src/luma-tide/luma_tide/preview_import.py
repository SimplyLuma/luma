# SPDX-License-Identifier: Apache-2.0
"""Bring a dev-preview Tide's library into the installed Tide, once.

A preview build ran from a checkout with its own XDG data and cache
directories, launched through a user D-Bus override. Installed Tide retires
that override at login (see `retire_overrides.py`), which would otherwise
leave the person's library, listening history and server sign-in behind.

This module finds the preview's directories (from the retired override's
launcher, or the standard checkout location), copies the files Tide owns
there (server artwork and downloads) into Tide's own directories, and asks
`LibraryStore.import_preview_library` to copy the library rows. It has no GTK
dependency, so it is unit-tested directly.

Secrets are never read here. A source keeps its keyring reference; the
keyring entry is moved to the current schema the first time the source
connects (see `credentials.SecretServiceStore.lookup`).
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .model import LibraryStore, PreviewImport, _cache_home, _data_home

# Directories below `luma-tide/` in the preview's data and cache homes that
# hold files Tide itself created and owns.
_OWNED_DATA = ("offline",)
_OWNED_CACHE = ("remote-artwork", "artwork")
_EXPORT = re.compile(r"^\s*export\s+(XDG_DATA_HOME|XDG_CACHE_HOME)=(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class PreviewHome:
    data_home: Path
    cache_home: Path

    @property
    def library(self) -> Path:
        return self.data_home / "luma-tide/library.db"


def _state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))


def _launcher_homes(launcher: Path) -> PreviewHome | None:
    """Read the XDG directories a preview launcher script exported."""
    try:
        text = launcher.read_text(errors="replace")
    except OSError:
        return None
    values: dict[str, Path] = {}
    for line in text.splitlines():
        match = _EXPORT.match(line)
        if match:
            try:
                parts = shlex.split(match.group(2))
            except ValueError:
                continue
            if len(parts) == 1:
                values[match.group(1)] = Path(parts[0])
    if "XDG_DATA_HOME" not in values:
        return None
    return PreviewHome(
        values["XDG_DATA_HOME"],
        values.get("XDG_CACHE_HOME", values["XDG_DATA_HOME"].parent / "cache"),
    )


def find_preview_homes(state_home: Path | None = None, data_home: Path | None = None) -> list[PreviewHome]:
    """Every preview library that still exists: first those named by a retired
    override's launcher, newest retirement first, then the standard checkout."""
    found: list[PreviewHome] = []
    retired_root = (state_home or _state_home()) / "luma-tide"
    for retired in sorted(retired_root.glob("retired-overrides-*"), reverse=True):
        for service in sorted(retired.glob("dbus-1/services/*.service")):
            try:
                lines = service.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.startswith("Exec="):
                    continue
                try:
                    executable = Path(shlex.split(line.removeprefix("Exec="))[0])
                except (ValueError, IndexError):
                    continue
                home = _launcher_homes(executable)
                if home is not None:
                    found.append(home)
    base = (data_home or _data_home()) / "luma-dev/tide-navidrome"
    found.append(PreviewHome(base / "data", base / "cache"))
    unique: list[PreviewHome] = []
    for home in found:
        if home.library.is_file() and all(home.library.resolve() != item.library.resolve() for item in unique):
            unique.append(home)
    return unique


def _copy_tree(source: Path, target: Path) -> int:
    """Copy files that aren't already at the target. Returns files copied."""
    copied = 0
    if not source.is_dir() or source.is_symlink():
        return 0
    for directory, names, files in os.walk(source, followlinks=False):
        names[:] = [name for name in names if not (Path(directory) / name).is_symlink()]
        relative = Path(directory).relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in files:
            origin = Path(directory) / name
            destination = target / relative / name
            if origin.is_symlink() or destination.exists():
                continue
            shutil.copy2(origin, destination)
            copied += 1
    return copied


class _Rewriter:
    """Maps a URI or path under the preview's owned directories to the copy."""

    def __init__(self, pairs: list[tuple[Path, Path]]) -> None:
        self.pairs = pairs

    def _path(self, path: Path) -> Path | None:
        for old, new in self.pairs:
            try:
                return new / path.relative_to(old)
            except ValueError:
                continue
        return None

    def __call__(self, value: str | None) -> str | None:
        if not value:
            return value
        if value.startswith("file:"):
            parsed = urlparse(value)
            moved = self._path(Path(unquote(parsed.path)))
            return moved.as_uri() if moved is not None else value
        if value.startswith("/"):
            moved = self._path(Path(value))
            return str(moved) if moved is not None else value
        return value


def import_preview_library(
    store: LibraryStore,
    *,
    log: logging.Logger,
    state_home: Path | None = None,
    data_home: Path | None = None,
    cache_home: Path | None = None,
) -> PreviewImport | None:
    """Import the most recent preview library, if any, exactly once. Never
    raises: a failure is logged and the installed library is left as it was."""
    try:
        if store.preview_import_done():
            return None
        homes = find_preview_homes(state_home, data_home)
        if not homes:
            return None
        home = homes[0]
        data_target = (data_home or _data_home()) / "luma-tide"
        cache_target = (cache_home or _cache_home()) / "luma-tide"
        pairs: list[tuple[Path, Path]] = []
        copied = 0
        for owned, source_root, target_root in (
            *((name, home.data_home / "luma-tide", data_target) for name in _OWNED_DATA),
            *((name, home.cache_home / "luma-tide", cache_target) for name in _OWNED_CACHE),
        ):
            source = source_root / owned
            if source.is_dir() and source.resolve() != (target_root / owned).resolve():
                copied += _copy_tree(source, target_root / owned)
                pairs.append((source, target_root / owned))
                pairs.append((source.resolve(), target_root / owned))
        result = store.import_preview_library(home.library, rewrite=_Rewriter(pairs))
        if result is not None:
            log.info(
                "Imported the preview Tide library from %s: %d sources, %d songs, %d copies, "
                "%d playlists, queue %s, %d files copied",
                home.library, result.sources, result.tracks, result.copies,
                result.playlists, "restored" if result.queue_restored else "kept", copied,
            )
        return result
    except Exception:
        log.exception("Importing the preview Tide library failed; the installed library is unchanged")
        return None
