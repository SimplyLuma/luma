# SPDX-License-Identifier: Apache-2.0
"""Where a Luma app is running, and what it can lean on there.

The one place the kit absorbs platform differences, so an app never branches on
``sys.platform`` itself (DECISION.md, "luma port"). On Luma OS / Linux every answer
here is the one the app already assumed; elsewhere the kit gives the nearest
equivalent:

    system()        "linux" | "macos" | "windows" | "android"
    has_webkit()    WebKitGTK 6 can be imported (Linux only today)
    has_dbus()      a session bus is expected (Linux only)
    secret_store()  libsecret / macOS Keychain / Windows Credential Manager / memory
    form_factor()   "phone" | "desktop" from LUMA_FORM_FACTOR, else None (the kit adapts by width)
    user_dir(kind, app_id)  where an app keeps its own data/config/cache/state on this platform
    adopt_user_dirs(app_id) off Linux: point unset XDG_*_HOME at those folders (platform_boot does it)

Nothing here imports GTK, so it is safe before Gtk.init and in background agents.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache

from .platform_secrets import MemorySecretStore, SecretBackend, open_secret_store

__all__ = [
    "system", "is_linux", "is_macos", "is_windows", "is_android",
    "has_webkit", "has_libsecret", "has_dbus", "form_factor",
    "secret_store", "SecretBackend", "MemorySecretStore", "user_dir", "adopt_user_dirs",
    "primary_held", "keycap", "PRIMARY_ACCEL",
]

#: The accelerator prefix for an app's "Ctrl" shortcuts: <Control> on Linux, Windows and Android
#: (as before), <Meta> (Command) on macOS. Not <Primary>: GTK 4 parses that as Control everywhere,
#: macOS included (GTK's own Cmd shortcuts are written <Meta>).
PRIMARY_ACCEL = "<Meta>" if sys.platform == "darwin" and os.environ.get("LUMA_PLATFORM", "macos") == "macos" else "<Control>"

# How macOS writes keys on keycaps and in menus.
_MAC_KEYCAPS = {"Ctrl": "⌘", "Shift": "⇧", "Alt": "⌥", "Super": "⌃", "Return": "↩", "Backspace": "⌫",
                "Delete": "⌦", "Escape": "⎋", "Tab": "⇥", "Space": "␣"}


def keycap(name: str) -> str:
    """A normalised key name ("Ctrl", "Shift", "Return", "N") as this platform shows it:
    unchanged off macOS; ⌘ ⇧ ⌥ ↩ … on macOS, where an app's Ctrl shortcuts are Command."""
    return _MAC_KEYCAPS.get(name, name) if is_macos() else name


def primary_held(state) -> bool:
    """Whether a key/scroll event's modifier state holds the platform's primary modifier
    (Control; on macOS Command, with Control still accepted). For hand-written key handlers."""
    from gi.repository import Gdk

    mask = Gdk.ModifierType.CONTROL_MASK
    if is_macos():
        mask |= Gdk.ModifierType.META_MASK
    return bool(state & mask)


@lru_cache(maxsize=1)
def system() -> str:
    """The platform family: linux, macos, windows or android."""
    forced = os.environ.get("LUMA_PLATFORM", "").strip().lower()
    if forced in ("linux", "macos", "windows", "android"):
        return forced
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin"):
        return "windows"
    if sys.platform == "android" or hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in os.environ:
        return "android"
    return "linux"


def is_linux() -> bool:
    return system() == "linux"


def is_macos() -> bool:
    return system() == "macos"


def is_windows() -> bool:
    return system() == "windows"


def is_android() -> bool:
    return system() == "android"


def _has_namespace(namespace: str, version: str) -> bool:
    try:
        import gi

        import importlib

        gi.require_version(namespace, version)
        importlib.import_module(f"gi.repository.{namespace}")
    except (ImportError, ValueError, AttributeError):
        return False
    return True


@lru_cache(maxsize=1)
def has_webkit() -> bool:
    """WebKitGTK 6 is importable. False on macOS, Windows and Android."""
    return _has_namespace("WebKit", "6.0")


@lru_cache(maxsize=1)
def has_libsecret() -> bool:
    return _has_namespace("Secret", "1")


def has_dbus() -> bool:
    """A freedesktop session bus is part of the platform (Luma OS, Linux desktops)."""
    return is_linux()


def form_factor() -> str | None:
    value = os.environ.get("LUMA_FORM_FACTOR", "").strip().lower()
    return value if value in ("phone", "desktop") else None


def secret_store(app_id: str, *, memory: bool = False) -> SecretBackend:
    """A credential store keyed by (account, kind) for ``app_id``.

    Linux: Secret Service through libsecret, schema ``app_id`` with string attributes
    ``account`` and ``kind`` (the schema Luma apps already use, so items carry over).
    macOS: the login Keychain, generic passwords with service ``app_id`` and account
    ``"<account>/<kind>"``. Windows: Credential Manager, generic credentials named
    ``"<app_id>/<account>/<kind>"``. Android (until Keystore lands) and ``memory=True``
    (fixtures, tests, LUMA_SECRETS=memory): an in-process dict that never touches disk.
    """
    if memory or os.environ.get("LUMA_SECRETS", "").lower() == "memory":
        return MemorySecretStore()
    return open_secret_store(app_id, system())


_XDG = {"data": ("XDG_DATA_HOME", ".local/share"), "config": ("XDG_CONFIG_HOME", ".config"),
        "cache": ("XDG_CACHE_HOME", ".cache"), "state": ("XDG_STATE_HOME", ".local/state")}


def user_dir(kind: str, app_id: str | None = None) -> str:
    """The folder this platform gives an app for its own files of ``kind``
    (``data``, ``config``, ``cache`` or ``state``). Not created here.

    Linux and Android: the XDG base directory (``$XDG_DATA_HOME`` or ``~/.local/share``,
    ...), exactly what Luma apps already use; the Android boot points XDG_*_HOME into the
    app's private storage (beside files/, which GTK's glue empties on every update).
    macOS: ``~/Library/Application Support/<app_id>`` for data, with ``config`` and
    ``state`` inside it, and ``~/Library/Caches/<app_id>`` for the cache.
    Windows: ``%APPDATA%\\<app_id>`` (roaming: data, config, state) and
    ``%LOCALAPPDATA%\\<app_id>\\cache``. ``app_id`` defaults to ``LUMA_APP_ID``.
    """
    if kind not in _XDG:
        raise ValueError(f"unknown user folder kind {kind!r}")
    var, fallback = _XDG[kind]
    home = os.path.expanduser("~")
    which = system()
    app = app_id or os.environ.get("LUMA_APP_ID") or "luma-app"
    if which == "macos":
        if kind == "cache":
            return os.path.join(home, "Library", "Caches", app)
        base = os.path.join(home, "Library", "Application Support", app)
        return base if kind == "data" else os.path.join(base, kind)
    if which == "windows":
        if kind == "cache":
            local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
            return os.path.join(local, app, "cache")
        roaming = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        base = os.path.join(roaming, app)
        return base if kind == "data" else os.path.join(base, kind)
    return os.environ.get(var) or os.path.join(home, fallback)


def adopt_user_dirs(app_id: str | None = None) -> dict[str, str]:
    """Off Linux, export XDG_DATA_HOME/CONFIG/CACHE/STATE as :func:`user_dir` for any that
    are unset, so app code written against XDG (``$XDG_DATA_HOME/luma/notes``) lands in the
    platform's own place. Call before anything asks GLib for a user directory (GLib keeps
    the first answer). Linux: nothing changes. Returns what was set."""
    if is_linux():
        return {}
    done: dict[str, str] = {}
    for kind, (var, _fallback) in _XDG.items():
        if not os.environ.get(var):
            selected = user_dir(kind, app_id)
            if is_windows():
                from .private_paths import ensure_private_directory
                ensure_private_directory(selected)
            os.environ[var] = done[var] = selected
    return done
