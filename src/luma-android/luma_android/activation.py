# SPDX-License-Identifier: Apache-2.0
"""Fixed Android-only restoration through the native session broker.

Native processes already hold their Unix user's desktop authority. Sandboxed
applications must use their own portals; this method does not grant a generic
application ID, command, window handle or focus API.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .errors import LumaAndroidError
from .activation_budget import CREDENTIAL_TIMEOUT_MS, EXISTING_CLIENT_TIMEOUT_MS, SHELL_TIMEOUT_MS

ANDROID_NAME = "org.projectluma.Android1"
ANDROID_PATH = "/org/projectluma/Android1"
SHELL_NAME = "org.gnome.Shell.Introspect"
SHELL_PATH = "/org/gnome/Shell/Introspect"
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


def validate_activation_package(package: str) -> None:
    if not isinstance(package, str) or len(package) > 255 or not PACKAGE.fullmatch(package):
        raise LumaAndroidError("Invalid Android package name.")


def _start(pid: int) -> str:
    # comm can contain spaces or closing parentheses.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def native_caller(connection, sender: str) -> tuple[int, str]:
    from gi.repository import Gio, GLib

    def credential(method):
        return connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            method, GLib.Variant("(s)", (sender,)), GLib.VariantType.new("(u)"),
            Gio.DBusCallFlags.NONE, CREDENTIAL_TIMEOUT_MS, None).unpack()[0]

    uid, pid = credential("GetConnectionUnixUser"), credential("GetConnectionUnixProcessID")
    if uid == 0 or uid != os.getuid() or pid <= 0:
        raise LumaAndroidError("Only this user's native session may restore an Android app.")
    before = _start(pid)
    # systemd applies user-service filesystem isolation in a child user
    # namespace. Parent process-root/sandbox inspection is therefore owned by
    # the native Shell, which derives this original sender's bus credentials.
    if _start(pid) != before:
        raise LumaAndroidError("The requesting session changed.")
    return pid, before


def activate_for_caller(connection, sender: str, package: str, engine, *, launch=False, close=False) -> bool:
    validate_activation_package(package)
    caller = native_caller(connection, sender)
    if package not in engine.live_launchable_packages():
        raise LumaAndroidError("This Android application is not installed.")
    if launch:
        authorize_from_broker(connection, sender)
        engine.launch(package)
    # Recheck bus identity and process lifetime after the registry/launch IO.
    if native_caller(connection, sender) != caller:
        raise LumaAndroidError("The requesting session changed.")
    return restore_from_broker(connection, package, caller=sender, close=close)


def authorize_from_broker(connection, caller: str) -> None:
    """Validate native process authority before even the Android-side launch."""
    from gi.repository import Gio, GLib

    result = connection.call_sync(
        SHELL_NAME, SHELL_PATH, SHELL_NAME, "CheckAndroidNativeCaller",
        GLib.Variant("(s)", (caller,)), GLib.VariantType.new("(b)"),
        Gio.DBusCallFlags.NONE, SHELL_TIMEOUT_MS, None)
    if not result.unpack()[0]:
        raise LumaAndroidError("The requesting native session was not admitted.")


def restore_from_broker(connection, package: str, *, caller: str, close=False) -> bool:
    from gi.repository import Gio, GLib

    validate_activation_package(package)
    result = connection.call_sync(
        SHELL_NAME, SHELL_PATH, SHELL_NAME, "CloseAndroidApplicationForCaller" if close else "ActivateAndroidApplicationForCaller",
        GLib.Variant("(ss)", (package, caller)), GLib.VariantType.new("(b)"),
        Gio.DBusCallFlags.NONE, SHELL_TIMEOUT_MS, None)
    return bool(result.unpack()[0])


def request_existing_restore(package: str, *, close=False) -> bool:
    """CLI launch has succeeded already; restore its existing host surface."""
    from gi.repository import Gio, GLib

    validate_activation_package(package)
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = connection.call_sync(
            ANDROID_NAME, ANDROID_PATH, ANDROID_NAME, "CloseExisting" if close else "ActivateExisting",
            GLib.Variant("(s)", (package,)), GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE, EXISTING_CLIENT_TIMEOUT_MS, None)
        return bool(result.unpack()[0])
    except GLib.Error as error:
        logging.getLogger(__name__).warning("Android host restoration failed: %s", error)
        return False
