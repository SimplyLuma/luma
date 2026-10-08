# SPDX-License-Identifier: Apache-2.0

"""Which install of an app Luma Connect syncs with: the system package or the Flatpak.

A Luma app installed from the operating system keeps its data under
`$XDG_DATA_HOME` (normally `~/.local/share`). The same app installed as a
Flatpak keeps it under `~/.var/app/<app id>/data`, because the sandbox sets
`XDG_DATA_HOME` there. Connect sync runs outside any sandbox and has to read and
write whichever of the two the person actually uses.

The rule, in order:

1. **The administrator-owned signed Flatpak role, when declared by the OS.**
   ADR-053 roles take priority over retained native desktop entries. Removal
   preserves that app's sandbox data ownership instead of restoring stale data.
2. **The system install, when there is no independent role.** Older ADR-031 had Depot never
   install the Flatpak of an app whose system package is present, so a system
   install is the one in use. It also keeps every existing native setup exactly
   as it was.
3. **Otherwise the Flatpak, when one is installed** — for the person, or for
   the whole system.
4. **Otherwise the system location**, which is where the data would be if the
   app were installed later, and what sync always used.

How installs are recognised, without running anything:

* A system install is a desktop entry for the app id in an applications
  directory on the data path that is *not* a Flatpak export: it has no
  `X-Flatpak` key and is not under `…/flatpak/exports/`. It is read with GIO's
  desktop app info.
* A Flatpak install is Flatpak's own deployment metadata:
  `<installation>/app/<app id>/current/active/metadata` naming the app id, in
  the per-user installation (`$FLATPAK_USER_DIR` or
  `~/.local/share/flatpak`) or the system one (`/var/lib/flatpak`).

Security: the app ids are this module's constants and the Flatpak data path is
built from them, never taken from a file. A Flatpak data directory is only used
when it is a real directory owned by this user — not a symlink somebody placed to
point sync's writes somewhere else — so a `~/.var/app/<id>` that is a link is
treated as absent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

NOTES = "org.projectluma.Notes"
WEATHER = "org.projectluma.Weather"
CLOCK = "org.projectluma.Clock"
LEAF = "org.projectluma.Leaf"
TIDE = "org.projectluma.Tide"
TASKS = "org.projectluma.Tasks"
CONTACTS = "org.projectluma.Contacts"
SYNCED_APPS = (NOTES, WEATHER, CLOCK, LEAF, TIDE, TASKS, CONTACTS)

SYSTEM = "system"
FLATPAK = "flatpak"
NONE = "none"


@dataclass(frozen=True)
class Install:
    app_id: str
    kind: str          # SYSTEM, FLATPAK or NONE
    data_home: Path    # the XDG_DATA_HOME that install's app sees


def _env(environment: dict[str, str] | None) -> dict[str, str]:
    return dict(os.environ if environment is None else environment)


def _home(env: dict[str, str]) -> Path:
    return Path(env.get("HOME") or str(Path.home()))


def native_data_home(environment: dict[str, str] | None = None) -> Path:
    env = _env(environment)
    return Path(env.get("XDG_DATA_HOME") or _home(env) / ".local" / "share")


def flatpak_data_home(app_id: str, environment: dict[str, str] | None = None) -> Path:
    if app_id not in SYNCED_APPS:
        raise ValueError(f"Not an app Connect syncs: {app_id}")
    return _home(_env(environment)) / ".var" / "app" / app_id / "data"


def _data_dirs(env: dict[str, str]) -> list[Path]:
    dirs = [native_data_home(env)]
    dirs += [Path(part) for part in (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if part]
    return dirs


def _is_flatpak_export(path: Path) -> bool:
    return "flatpak/exports" in path.as_posix()


def system_desktop_entry(app_id: str, environment: dict[str, str] | None = None) -> Path | None:
    """The desktop entry of a non-Flatpak install of `app_id`, if there is one."""
    env = _env(environment)
    for directory in _data_dirs(env):
        candidate = directory / "applications" / f"{app_id}.desktop"
        if _is_flatpak_export(candidate) or not candidate.is_file():
            continue
        if _flatpak_key(candidate):
            continue
        return candidate
    return None


def _flatpak_key(path: Path) -> bool:
    try:
        from gi.repository import Gio

        info = Gio.DesktopAppInfo.new_from_filename(str(path))
        if info is not None:
            return bool(info.get_string("X-Flatpak"))
    except (ImportError, TypeError):
        pass
    try:
        return any(line.startswith("X-Flatpak=") for line in path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError):
        return False


def flatpak_installations(environment: dict[str, str] | None = None) -> list[Path]:
    env = _env(environment)
    user = Path(env.get("FLATPAK_USER_DIR") or native_data_home(env) / "flatpak")
    system = Path(env.get("FLATPAK_SYSTEM_DIR") or "/var/lib/flatpak")
    return [user, system]


def flatpak_installed(app_id: str, environment: dict[str, str] | None = None) -> bool:
    for installation in flatpak_installations(environment):
        metadata = installation / "app" / app_id / "current" / "active" / "metadata"
        try:
            text = metadata.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(line.strip() == f"name={app_id}" for line in text.splitlines()):
            return True
    return False


def _owned_directory(path: Path, home: Path) -> bool:
    """A directory of this user's under ~/.var, with no symlink on the way to it."""
    base = home / ".var"
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return False
    probe = base
    for part in ("",) + parts:
        probe = probe / part if part else probe
        try:
            status = probe.lstat()
        except FileNotFoundError:
            return True  # not created yet: the app makes it on first run
        except OSError:
            return False
        if not os.path.isdir(probe) or os.path.islink(probe) or status.st_uid != os.getuid():
            return False
    return True


def resolve(app_id: str, environment: dict[str, str] | None = None) -> Install:
    if app_id not in SYNCED_APPS:
        raise ValueError(f"Not an app Connect syncs: {app_id}")
    # ADR-053 moves user application ownership to the signed system Flatpak.
    # Only an administrator-owned OS role contract can override native entries;
    # a user export/catalogue cannot impersonate that transition.
    try:
        from luma_installer.native_app_roles import required, installed
    except ImportError:
        required = None  # traditional Linux without Luma's host owner
    if required is not None and required(app_id):
        import gi
        gi.require_version('Flatpak', '1.0')
        from gi.repository import Flatpak
        installation = Flatpak.Installation.new_system(None)
        present = any(ref.get_name() == app_id and ref.format_ref().startswith('app/')
                      for ref in installation.list_installed_refs(None))
        data_home = flatpak_data_home(app_id, environment)
        if not _owned_directory(data_home, _home(_env(environment))):
            raise ValueError('The independently updated application data path is unsafe.')
        if present:
            installed(installation, app_id)  # exact origin/ref/commit/trust proof
            return Install(app_id, FLATPAK, data_home)
        # A person's removal choice does not restore the stale native app/store.
        return Install(app_id, NONE, data_home)
    if system_desktop_entry(app_id, environment) is not None:
        return Install(app_id, SYSTEM, native_data_home(environment))
    if flatpak_installed(app_id, environment):
        data_home = flatpak_data_home(app_id, environment)
        if _owned_directory(data_home, _home(_env(environment))):
            return Install(app_id, FLATPAK, data_home)
    return Install(app_id, NONE, native_data_home(environment))


def app_environment(app_id: str, environment: dict[str, str] | None = None) -> dict[str, str]:
    """`environment` as the chosen install of `app_id` sees its own data.

    Only XDG_DATA_HOME changes, so every existing path helper that reads it
    (notes_data_directory, the collections, Leaf's library) lands in the right
    place without knowing about Flatpak.
    """
    env = _env(environment)
    env["XDG_DATA_HOME"] = str(resolve(app_id, env).data_home)
    return env


__all__ = [
    "CLOCK", "CONTACTS", "TASKS", "FLATPAK", "Install", "LEAF", "NONE", "NOTES", "SYNCED_APPS", "SYSTEM", "TIDE", "WEATHER",
    "app_environment", "flatpak_data_home", "flatpak_installations", "flatpak_installed",
    "native_data_home", "resolve", "system_desktop_entry",
]
