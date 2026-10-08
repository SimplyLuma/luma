# SPDX-License-Identifier: Apache-2.0
"""NetworkManager, UPower, polkit and logind, each read over the system bus.

Every probe degrades to a conservative answer: NetworkManager not answering,
or an unknown metered state, is treated as metered (download waits for a
person); UPower not answering as a battery of unknown charge (download waits);
and a failed polkit query as "not authorized".
"""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

__all__ = ("NetworkState", "PowerState", "network_state", "power_state", "check_authorization",
           "reboot", "caller_uid", "NotAuthorized", "Inhibited", "authorize_restart", "other_users_logged_in")

# NMMetered
NM_METERED_UNKNOWN, NM_METERED_YES, NM_METERED_NO, NM_METERED_GUESS_YES, NM_METERED_GUESS_NO = range(5)
# NMConnectivityState
NM_CONNECTIVITY_UNKNOWN, NM_CONNECTIVITY_NONE, NM_CONNECTIVITY_PORTAL, NM_CONNECTIVITY_LIMITED, NM_CONNECTIVITY_FULL = range(5)
# UPowerDeviceState
UP_STATE_CHARGING, UP_STATE_DISCHARGING, UP_STATE_FULLY_CHARGED = 1, 2, 4


class NotAuthorized(Exception):
    error_class = "not-authorized"


@dataclass(frozen=True)
class NetworkState:
    available: bool          # NetworkManager answered
    metered: bool | None     # None: unknown
    connectivity: int

    @property
    def online(self) -> bool:
        # Without NetworkManager, let the fetch itself decide.
        return not self.available or self.connectivity in (NM_CONNECTIVITY_FULL, NM_CONNECTIVITY_UNKNOWN,
                                                            NM_CONNECTIVITY_LIMITED)


@dataclass(frozen=True)
class PowerState:
    available: bool
    on_battery: bool
    percentage: float | None


def _get(connection, bus, path, interface, name):
    result = connection.call_sync(bus, path, "org.freedesktop.DBus.Properties", "Get",
                                  GLib.Variant("(ss)", (interface, name)), GLib.VariantType.new("(v)"),
                                  Gio.DBusCallFlags.NO_AUTO_START, 5000, None)
    return result.unpack()[0]


def network_state(connection=None) -> NetworkState:
    connection = connection or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    try:
        metered = _get(connection, "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager",
                       "org.freedesktop.NetworkManager", "Metered")
        connectivity = _get(connection, "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager",
                            "org.freedesktop.NetworkManager", "Connectivity")
    except GLib.Error:
        return NetworkState(False, None, NM_CONNECTIVITY_UNKNOWN)
    if metered in (NM_METERED_YES, NM_METERED_GUESS_YES):
        value = True
    elif metered in (NM_METERED_NO, NM_METERED_GUESS_NO):
        value = False
    else:
        value = None
    return NetworkState(True, value, int(connectivity))


def power_state(connection=None) -> PowerState:
    connection = connection or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    try:
        on_battery = bool(_get(connection, "org.freedesktop.UPower", "/org/freedesktop/UPower",
                               "org.freedesktop.UPower", "OnBattery"))
    except GLib.Error:
        return PowerState(False, False, None)
    percentage = None
    try:
        path = "/org/freedesktop/UPower/devices/DisplayDevice"
        if _get(connection, "org.freedesktop.UPower", path, "org.freedesktop.UPower.Device", "IsPresent"):
            percentage = float(_get(connection, "org.freedesktop.UPower", path,
                                    "org.freedesktop.UPower.Device", "Percentage"))
    except GLib.Error:
        percentage = None
    return PowerState(True, on_battery, percentage)


def caller_uid(connection, sender: str) -> int:
    result = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                  "GetConnectionUnixUser", GLib.Variant("(s)", (sender,)),
                                  GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
    return int(result.unpack()[0])


def check_authorization(connection, sender: str, action_id: str, interactive: bool = True) -> bool:
    """Ask polkit whether the D-Bus caller may perform ``action_id``."""
    subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})
    flags = 1 if interactive else 0  # AllowUserInteraction
    try:
        result = connection.call_sync(
            "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
            "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization",
            GLib.Variant("((sa{sv})sa{ss}us)", (subject, action_id, {}, flags, "")),
            GLib.VariantType.new("((bba{ss}))"), Gio.DBusCallFlags.NONE,
            5 * 60 * 1000 if interactive else 25000, None)
    except GLib.Error:
        return False
    (is_authorized, _is_challenge, _details), = result.unpack()
    return bool(is_authorized)


LOGIND = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
LOGIND_MANAGER = "org.freedesktop.login1.Manager"
#: org.freedesktop.login1(5), systemd 259: honour "block-weak" inhibitors for
#: root as well. "block" inhibitors are enforced for every caller since 257.
SD_LOGIND_ROOT_CHECK_INHIBITORS = 1 << 0
#: logind's SESSION_CLASS_IS_INHIBITOR_LIKE (src/login/logind-session.h, 259):
#: the sessions that count as "other users logged in".
USER_SESSION_CLASSES = ("user", "user-early", "user-light", "user-early-light")


class Inhibited(Exception):
    error_class = "inhibited"


def other_users_logged_in(connection, uid: int) -> bool:
    """Whether logind has a user session of someone other than ``uid``, as
    logind itself decides before a reboot. Unknown counts as yes."""
    try:
        result = connection.call_sync(LOGIND, LOGIND_PATH, LOGIND_MANAGER, "ListSessions", None,
                                      GLib.VariantType.new("(a(susso))"), Gio.DBusCallFlags.NONE, 10000, None)
    except GLib.Error:
        return True
    for _session_id, session_uid, _user, _seat, path in result.unpack()[0]:
        if int(session_uid) == uid:
            continue
        try:
            session_class = _get(connection, LOGIND, path, "org.freedesktop.login1.Session", "Class")
        except GLib.Error:
            return True
        if session_class in USER_SESSION_CLASSES:
            return True
    return False


def authorize_restart(connection, sender: str) -> str:
    """Check that the D-Bus caller may restart the computer, as logind would
    check them. Returns the logind action that was granted."""
    from .dbus_interface import LOGIN1_REBOOT, LOGIN1_REBOOT_MULTIPLE_SESSIONS
    try:
        uid = caller_uid(connection, sender)
    except GLib.Error as error:
        raise NotAuthorized(f"could not identify the caller: {error.message}") from None
    action = LOGIN1_REBOOT_MULTIPLE_SESSIONS if other_users_logged_in(connection, uid) else LOGIN1_REBOOT
    if not check_authorization(connection, sender, action, interactive=True):
        raise NotAuthorized("not authorized to restart this computer"
                            + (" while other people are logged in" if action == LOGIN1_REBOOT_MULTIPLE_SESSIONS else ""))
    return action


def reboot(connection=None) -> None:
    """Ask logind to restart, honouring every block inhibitor.

    luma-updated is root, for which logind skips its own polkit checks, so the
    caller is authorized first with ``authorize_restart``. There is no fallback
    to ``Reboot``: it would ignore "block-weak" inhibitors."""
    connection = connection or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    try:
        connection.call_sync(LOGIND, LOGIND_PATH, LOGIND_MANAGER, "RebootWithFlags",
                             GLib.Variant("(t)", (SD_LOGIND_ROOT_CHECK_INHIBITORS,)),
                             None, Gio.DBusCallFlags.NONE, 30000, None)
    except GLib.Error as error:
        name = Gio.DBusError.get_remote_error(error) or ""
        if name == "org.freedesktop.login1.BlockedByInhibitorLock" or "inhibitor" in (error.message or "").lower():
            raise Inhibited("an application is preventing the restart right now; "
                            "close it or restart from the system menu") from None
        raise
