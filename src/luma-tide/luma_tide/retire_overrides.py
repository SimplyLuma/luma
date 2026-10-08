# SPDX-License-Identifier: Apache-2.0
"""Retire a stale dev-preview override of Tide's D-Bus service file or
desktop entry, never delete it.

Background: a dev-preview checkout of Tide can be made the thing that
actually launches when someone opens "Tide" by dropping a
`org.projectluma.Tide.service` and/or `org.projectluma.Tide.desktop` into
`$XDG_DATA_HOME/dbus-1/services/` / `$XDG_DATA_HOME/applications/` --
user-level overrides take priority over the installed package's copies
under `/usr/share/`. `LibraryStore.adopt_preview_library` (see model.py)
handles folding that preview's own library into the real one the first time
shipped Tide runs, but a stale override left in place means the SHIPPED
build may never get a chance to run at all: the session bus and GNOME
Shell resolve "org.projectluma.Tide" straight to the preview's own
`Exec=`, silently, every time. Retiring the override is what closes that
gap -- see the `2.luma.19` changelog for the real incident this addresses.

This module is deliberately free of any GTK/GLib/D-Bus dependency (see
`startup.py` for the same rationale) so it stays importable and testable
in a plain `unittest` run. The actual D-Bus "please reload" call is made
by `retire_overrides_main.main()`, not here, and only when there was
something to retire.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import time
from pathlib import Path
from typing import Callable

#: The real, installed binary every legitimate override should eventually
#: resolve to -- an absolute path (from a D-Bus service file's `Exec=`) or a
#: bare command name resolvable via $PATH (from a desktop file's `Exec=`,
#: which the *installed* copy under /usr/share uses) both count as fine.
INSTALLED_EXEC = "/usr/bin/org.projectluma.Tide"

#: (relative path under $XDG_DATA_HOME, install-relative subdir used for the backup)
_CANDIDATES = (
    Path("dbus-1/services/org.projectluma.Tide.service"),
    Path("applications/org.projectluma.Tide.desktop"),
)


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def _state_home() -> Path:
    # systemd's StateDirectory=luma-tide (see the .service file) creates
    # %h/.local/state/luma-tide itself, writable, before this ever runs --
    # no existence assumptions needed for it, unlike a bare ReadWritePaths
    # entry -- and sets $STATE_DIRECTORY to that exact path. Preferring it
    # over re-deriving the same path from $XDG_STATE_HOME sidesteps any
    # chance of the two disagreeing; retire_stale_overrides() appends
    # "luma-tide" itself, so this returns that directory's parent.
    state_directory = os.environ.get("STATE_DIRECTORY")
    if state_directory:
        return Path(state_directory).parent
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))


def _exec_target(path: Path) -> str | None:
    """The first token of the file's `Exec=` line, or None if there isn't
    one (not our file format, or unreadable)."""
    try:
        text = path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("Exec="):
            value = line[len("Exec=") :].strip()
            try:
                tokens = shlex.split(value, posix=True)
            except ValueError:
                tokens = value.split()
            return tokens[0] if tokens else None
    return None


def _is_stale(exec_target: str | None) -> bool:
    if exec_target is None:
        # Not our file shape at all -- don't touch something we can't
        # positively identify as a Tide launcher.
        return False
    if exec_target == INSTALLED_EXEC:
        return False
    if not exec_target.startswith("/") and shutil.which(exec_target) == INSTALLED_EXEC:
        # A bare command name (the installed desktop file's own `Exec=org.
        # projectluma.Tide %U` form) that genuinely resolves to the real
        # binary via $PATH.
        return False
    return True


def retire_stale_overrides(
    *,
    log: logging.Logger,
    data_home: Path | None = None,
    state_home: Path | None = None,
    on_retired: Callable[[Path], None] | None = None,
) -> list[Path]:
    """Move every override that exists and does not point at the real
    installed binary into a timestamped backup directory, preserving its
    original relative layout -- never delete. Returns the list of original
    paths that were retired (empty on a normal run once this has already
    happened, or if neither override is present, or both point at the
    real binary already).

    `on_retired`, if given, is called once per retired path, after the
    move -- a caller uses this to nudge file-info caches (bump a watched
    directory's mtime, ask the session bus to reload) without this module
    depending on GLib/D-Bus itself.
    """
    data_home = data_home if data_home is not None else _data_home()
    state_home = state_home if state_home is not None else _state_home()

    to_retire = [
        data_home / relative
        for relative in _CANDIDATES
        if (data_home / relative).is_file() and _is_stale(_exec_target(data_home / relative))
    ]
    if not to_retire:
        return []

    backup_root = state_home / "luma-tide" / f"retired-overrides-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    retired: list[Path] = []
    for source in to_retire:
        relative = source.relative_to(data_home)
        destination = backup_root / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError:
            log.exception("Could not retire stale Tide override %s", source)
            continue
        log.info("Retired a stale Tide launch override: %s -> %s", source, destination)
        retired.append(source)
        if on_retired is not None:
            try:
                on_retired(source)
            except Exception:
                log.exception("A retirement callback failed for %s", source)
    return retired
