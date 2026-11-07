# SPDX-License-Identifier: Apache-2.0
"""The account's first session: what first boot chose, applied without GTK.

luma-setup --first-session runs before the Shell starts (the desk and
accessibility) and once the desktop is up (apps, Connect); it imports only
Gio and GLib so it costs the session's start as little as possible.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import gi

from gi.repository import Gio, GLib  # noqa: E402

from . import system  # noqa: E402

log = logging.getLogger("luma-setup")

SYSTEM_WELCOME = Path(os.environ.get("LUMA_SETUP_WELCOME_DIR", "/var/lib/luma-setup/welcome"))
LEAVE = "I don’t want to do this."
TILING_UUID = "tilingshell@ferrarodomenico.com"
SHELL_STATE = "org.project_luma.shell-state"

#: The dash styles, as Settings' Dash panel sets them (shell-state shelf keys).
DASH_PRESETS = {
    "luma": {"shelf-group-layout": "luma", "shelf-surface-mode": "separate", "shelf-edge-mode": "floating",
             "shelf-span-full": False, "shelf-float-ends": True, "shelf-dock-anchor": "center",
             "shelf-actions-anchor": "center"},
    "cupertino": {"shelf-group-layout": "luma", "shelf-surface-mode": "connected", "shelf-edge-mode": "floating",
                  "shelf-span-full": False, "shelf-float-ends": True, "shelf-dock-anchor": "center",
                  "shelf-actions-anchor": "center"},
    "redmond": {"shelf-group-layout": "split-ends", "shelf-surface-mode": "connected",
                "shelf-edge-mode": "protruding", "shelf-span-full": True, "shelf-float-ends": False,
                "shelf-dock-anchor": "start", "shelf-actions-anchor": "end"},
}


def _state_dir() -> Path:
    return Path(GLib.get_user_state_dir()) / "luma-setup"


def pending(user: str | None = None) -> dict | None:
    """The welcome first boot left for this account, or None once it is done."""
    user = user or GLib.get_user_name()
    if (_state_dir() / "welcome-done").exists():
        return None
    try:
        return json.loads((SYSTEM_WELCOME / user).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def mark_done(how: str) -> None:
    folder = _state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "welcome-done").write_text(how + "\n", encoding="utf-8")


def carry_accessibility(welcome: dict) -> None:
    """What was switched on in the setup session stays on in theirs."""
    for name, on in (welcome.get("accessibility") or {}).items():
        if name in system.ACCESSIBILITY and on:
            system.set_accessibility(name, True)


def _shell_settings() -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SHELL_STATE, True) is None:
        return None
    return Gio.Settings.new(SHELL_STATE)


def current_dash() -> str:
    settings = _shell_settings()
    if settings is None:
        return "luma"
    schema = settings.props.settings_schema
    for name, keys in DASH_PRESETS.items():
        if all(not schema.has_key(k) or settings.get_value(k).unpack() == v for k, v in keys.items()):
            return name
    return "luma"


def apply_dash(name: str) -> None:
    settings = _shell_settings()
    if settings is None or name not in DASH_PRESETS or current_dash() == name:
        return   # already so: nothing written
    schema = settings.props.settings_schema
    settings.delay()
    for key, value in DASH_PRESETS[name].items():
        if schema.has_key(key):
            if isinstance(value, bool):
                settings.set_boolean(key, value)
            else:
                settings.set_string(key, value)
    settings.apply()


def tiling_on() -> bool:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.shell", True) is None:
        return False
    shell = Gio.Settings.new("org.gnome.shell")
    return TILING_UUID in shell.get_strv("enabled-extensions") and TILING_UUID not in shell.get_strv(
        "disabled-extensions")


def set_tiling(on: bool) -> None:
    """The Shell's own extension service, as the Tiling quick toggle does."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions", "org.gnome.Shell.Extensions",
                 "EnableExtension" if on else "DisableExtension", GLib.Variant("(s)", (TILING_UUID,)),
                 None, Gio.DBusCallFlags.NONE, 5000, None, None, None)
    except GLib.Error as error:
        log.info("tiling: %s", error.message)


def collections() -> list[dict]:
    """Depot's collections and their apps, from the catalogue Depot installs from."""
    data = system.fixture()
    if data is not None and "collections" in data:
        return data["collections"]
    try:
        from luma_installer.depot_catalog import load_catalog
        catalog = load_catalog()
    except Exception as error:  # noqa: BLE001 - no catalogue: no Add apps page
        log.info("no Depot catalogue: %s", error)
        return []
    found = []
    for collection in getattr(catalog, "collections", ()):
        apps = [{"id": e.id, "name": e.name, "icon": e.app_id or e.id} for e in catalog.members(collection)]
        if apps:
            found.append({"id": collection.id, "name": collection.name, "summary": collection.summary, "apps": apps})
    return found


def start_installs(app_ids: list[str]) -> None:
    """Depot installs them in the background, through its own provisioning, from a plan of this account's."""
    if not app_ids:
        return
    plan = _state_dir() / "apps.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({"collections": [], "applications": app_ids}) + "\n", encoding="utf-8")
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
    launcher.setenv("LUMA_FIRST_BOOT_APPS", str(plan), True)
    try:
        launcher.spawnv(["luma-depot", "--provision"])
    except GLib.Error as error:
        log.warning("Depot did not start: %s", error.message)


def open_connect() -> None:
    """Luma Connect's own sign-in (a browser sign-in; the password never passes through here)."""
    info = Gio.DesktopAppInfo.new("org.projectluma.Connect.desktop")
    if info is not None:
        info.launch([], None)


def connect_account() -> str:
    """The signed-in Connect @username, or "" (the Connect agent's public profile)."""
    data = system.fixture()
    if data is not None:
        return data.get("connect", "")
    path = Path(GLib.get_user_config_dir()) / "luma-connect" / "profile.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(profile.get("username") or "")


def page_names(phone: bool, groups: list | None = None) -> list[str]:
    """The welcome's pages; first boot counts them so the dots run on across both."""
    groups = collections() if groups is None else groups
    return (["connect"] if phone else ["desk", "connect"]) + (["apps"] if groups else [])


def first_session(stage: str) -> int:
    """luma-setup --first-session: what was chosen at first boot, applied in the account's first session.

    stage "settings" runs before the Shell starts (the desk and accessibility
    are in place for its first frame); stage "desktop" runs once the desktop
    is up: Depot installs the chosen apps in the background, and a quiet
    notification offers Luma Connect when it was left for later.
    """
    welcome = pending()
    if welcome is None:
        return 0
    chosen = welcome.get("first_session") or {}
    if stage == "settings":
        carry_accessibility(welcome)
        desk = chosen.get("desk")
        if desk:
            if desk.get("dash"):
                apply_dash(desk["dash"])
            _set_tiling_setting(bool(desk.get("tiling")))
        Gio.Settings.sync()
        return 0
    start_installs(list(chosen.get("apps") or []))
    if chosen.get("connect_later"):
        _notify("Luma Connect", "Sign in to keep your files, photos and messages on every device.")
    mark_done("first-session")
    return 0


def _set_tiling_setting(on: bool) -> None:
    """Before the Shell runs: the enabled-extensions list the Shell starts from."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.shell", True) is None:
        return
    shell = Gio.Settings.new("org.gnome.shell")
    if tiling_on() == on and (on or TILING_UUID not in shell.get_strv("enabled-extensions")):
        return   # already so: no write, no settings service to start before the Shell
    enabled = [e for e in shell.get_strv("enabled-extensions") if e != TILING_UUID]
    disabled = [e for e in shell.get_strv("disabled-extensions") if e != TILING_UUID]
    (enabled if on else disabled).append(TILING_UUID)
    shell.set_strv("enabled-extensions", enabled)
    shell.set_strv("disabled-extensions", disabled)


def _notify(title: str, body: str) -> None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Luma Connect", 0, "org.projectluma.Connect", title, body,
                                                      [], {"desktop-entry": GLib.Variant("s", "org.projectluma.Connect")},
                                                      -1)),
                      None, Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as error:
        log.info("no notification: %s", error.message)
