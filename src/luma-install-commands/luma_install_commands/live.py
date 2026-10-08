# SPDX-License-Identifier: MPL-2.0
"""Make a change applied to the running system visible to running sessions.

rpm-ostree applies a change live by placing an overlay over ``/usr``. Programs
that were already watching ``/usr`` (GNOME Shell's app list, the session bus,
systemd) keep watching the directories underneath and never hear about it, so
a new app would not appear until the person logged out. This module closes
that gap without a login script or a resident process:

* launchers (``.desktop`` files) and icons that appeared are exported to
  ``/run/luma/live-exports/share``, which every session has at the front of
  ``XDG_DATA_DIRS`` (``61-luma-live-exports`` user environment generator), so
  the Shell's own monitors pick them up at once; launchers that disappeared
  are exported as ``Hidden=true`` so they leave the app list at once too;
* the system bus and each session bus reload their service files;
* systemd reloads its units when units changed.

``/run`` is emptied at every boot, when the change is part of ``/usr`` anyway.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(os.environ.get("LUMA_LIVE_ROOT", "/"))
EXPORTS = Path(os.environ.get("LUMA_LIVE_EXPORTS", "/run/luma/live-exports/share"))

WATCHED = {
    "applications": "usr/share/applications",
    "icons": "usr/share/icons",
    "session-services": "usr/share/dbus-1/services",
    "system-services": "usr/share/dbus-1/system-services",
    "system-units": "usr/lib/systemd/system",
    "user-units": "usr/lib/systemd/user",
}


def _listing(path: Path) -> set[str]:
    out: set[str] = set()
    if not path.is_dir():
        return out
    for directory, _dirs, files in os.walk(path):
        for name in files:
            out.add(os.path.relpath(os.path.join(directory, name), path))
    return out


def snapshot(root: Path | None = None) -> dict[str, set[str]]:
    root = root or ROOT
    return {key: _listing(root / relative) for key, relative in WATCHED.items()}


def diff(before: dict[str, set[str]], after: dict[str, set[str]]) -> dict[str, tuple[set[str], set[str]]]:
    return {key: (after.get(key, set()) - before.get(key, set()), before.get(key, set()) - after.get(key, set()))
            for key in WATCHED}


HIDDEN = "[Desktop Entry]\nType=Application\nName=Removed\nHidden=true\nNoDisplay=true\n"


def export(changes: dict[str, tuple[set[str], set[str]]], root: Path | None = None,
           exports: Path | None = None) -> list[str]:
    """Write the exports for ``changes``; returns what was written, for the log."""
    root = root or ROOT
    exports = exports or EXPORTS
    written: list[str] = []
    added, removed = changes.get("applications", (set(), set()))
    applications = exports / "applications"
    for name in sorted(added):
        if not name.endswith(".desktop"):
            continue
        target = applications / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / WATCHED["applications"] / name, target)
        written.append(str(target))
    for name in sorted(removed):
        if not name.endswith(".desktop"):
            continue
        target = applications / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(HIDDEN)
        written.append(str(target))
    icons_added, _ = changes.get("icons", (set(), set()))
    for name in sorted(icons_added):
        source = root / WATCHED["icons"] / name
        if not source.is_file() or name.endswith(".cache"):
            continue
        target = exports / "icons" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        written.append(str(target))
    if icons_added:
        # Icon theme lookups check the theme directory's time stamp.
        for theme in {Path(name).parts[0] for name in icons_added if Path(name).parts}:
            directory = exports / "icons" / theme
            if directory.is_dir():
                os.utime(directory)
    return written


def sessions(root: Path | None = None) -> list[tuple[int, Path]]:
    run_user = (root or ROOT) / "run/user"
    out = []
    if run_user.is_dir():
        for entry in sorted(run_user.iterdir()):
            if entry.name.isdigit() and (entry / "bus").exists():
                out.append((int(entry.name), entry))
    return out


def reload_services(changes, runner=subprocess.run, root: Path | None = None) -> None:
    def quiet(argv, env=None):
        try:
            runner(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, timeout=30)
        except Exception:
            pass

    if any(changes.get(key, (set(), set()))[0] or changes.get(key, (set(), set()))[1]
           for key in ("system-services",)):
        quiet(["/usr/bin/busctl", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
               "org.freedesktop.DBus", "ReloadConfig"])
    if any(changes.get("system-units", (set(), set()))):
        quiet(["/usr/bin/systemctl", "daemon-reload"])
    session_services = any(changes.get("session-services", (set(), set())))
    user_units = any(changes.get("user-units", (set(), set())))
    if session_services or user_units:
        for uid, runtime in sessions(root):
            env = {"XDG_RUNTIME_DIR": str(runtime), "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
                   "PATH": "/usr/bin"}
            user = str(uid)
            if session_services:
                quiet(["/usr/sbin/runuser", "-u", _user_name(uid), "--", "/usr/bin/busctl", "--user", "call",
                       "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReloadConfig"],
                      env=env)
            if user_units:
                quiet(["/usr/bin/systemctl", "--user", "-M", f"{_user_name(uid)}@", "daemon-reload"])
            del user


def _user_name(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (KeyError, ImportError):
        return str(uid)


def refresh(before: dict[str, set[str]], runner=subprocess.run) -> dict[str, tuple[set[str], set[str]]]:
    """After a live change: export launchers and icons, reload buses and units."""
    changes = diff(before, snapshot())
    try:
        export(changes)
    except OSError:
        pass
    reload_services(changes, runner)
    return changes
