# SPDX-License-Identifier: MPL-2.0
"""Waking a sleeping computer for an alarm: org.projectluma.BackgroundWake1.

No process in a person's session can arm a wake-capable timer. A timerfd on
CLOCK_REALTIME_ALARM and a systemd timer's WakeSystem= both need
CAP_WAKE_ALARM, and the user service manager quietly falls back to an ordinary
clock that sleeps with the machine. So an alarm set for 07:00 on a laptop that
suspended at midnight rang when the lid opened, or was reported missed.

This system service does that one thing on behalf of the active local session:

- A caller names a wake-up (its application ID) and a time. The service asks
  the system manager for a *transient* timer with WakeSystem=true that elapses
  then and triggers only /usr/bin/true. The kernel's RTC alarm is held by
  systemd itself, which already has wake_alarm in Fedora's SELinux policy, so
  no custom policy, no written unit file and no resident helper is needed.
  Waking is all the timer does: the alarm itself still rings from the app's
  own wall-clock timer, which fires as soon as the machine is up.
- polkit decides who may, with `org.projectluma.background.wake-system`:
  allowed without asking for the active local session, refused otherwise.
- Wake-ups are named by the caller's UID, so nobody can see, replace or clear
  another person's. Each person has at most eight, each at most 31 days
  ahead. They stop with that person's user manager (PartOf=user@UID.service)
  and never outlive a reboot.
- Setting a wake-up arms the new timer before the old one of that name is
  stopped, so a change never leaves a gap.
- The service exits after 30 seconds without a call; D-Bus starts it again.
"""

from __future__ import annotations

import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import ids, journal  # noqa: E402

BUS_NAME = "org.projectluma.BackgroundWake1"
OBJECT_PATH = "/org/projectluma/BackgroundWake1"
INTERFACE = "org.projectluma.BackgroundWake1"
INTERFACE_VERSION = 1
ERROR_PREFIX = "org.projectluma.BackgroundWake1.Error."
ACTION = "org.projectluma.background.wake-system"

MAX_PER_USER = 8
MIN_AHEAD_SECONDS = 5
MAX_AHEAD_SECONDS = 31 * 24 * 3600
IDLE_EXIT_SECONDS = 30
UNIT_PREFIX = "luma-wake-"
TRUE = "/usr/bin/true"

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_TIMER = re.compile(r"^luma-wake-u(?P<uid>[0-9]{1,10})-(?P<name>[A-Za-z0-9_.\\]{1,512})-(?P<at>[0-9]{1,12})\.timer$")

INTERFACE_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="SetWakeup">
      <arg name="name" type="s" direction="in"/>
      <arg name="time" type="x" direction="in"/>
    </method>
    <method name="ClearWakeup">
      <arg name="name" type="s" direction="in"/>
      <arg name="cleared" type="b" direction="out"/>
    </method>
    <method name="ListWakeups">
      <arg name="wakeups" type="a(sx)" direction="out"/>
    </method>
    <property name="Version" type="u" access="read"/>
  </interface>
</node>
"""


class WakeError(Exception):
    error = "Failed"


class NotAuthorized(WakeError):
    error = "NotAuthorized"


class InvalidArgument(WakeError):
    error = "InvalidArgument"


class LimitReached(WakeError):
    error = "LimitReached"


def wakeup_name(value: object) -> str:
    if not isinstance(value, str) or not value.isascii() or _NAME.fullmatch(value) is None:
        raise InvalidArgument(f"not a valid wake-up name: {value!r}")
    return value


def timer_unit(uid: int, name: str, at: int) -> str:
    # '-' separates the fields, so it is escaped inside the name as systemd does.
    return f"{UNIT_PREFIX}u{int(uid)}-{wakeup_name(name).replace('-', chr(92) + 'x2d')}-{int(at)}.timer"


def service_unit(timer: str) -> str:
    return timer[: -len(".timer")] + ".service"


def parse_timer_unit(unit: str) -> tuple[int, str, int] | None:
    match = _TIMER.fullmatch(unit)
    if match is None:
        return None
    return int(match["uid"]), match["name"].replace(chr(92) + "x2d", "-"), int(match["at"])


def calendar(at: int) -> str:
    """An OnCalendar= value for one instant, in UTC so no time zone is guessed."""
    return datetime.fromtimestamp(int(at), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def timer_properties(uid: int, name: str, at: int) -> list[tuple[str, str, object]]:
    return [
        ("Description", "s", f"Wake the computer for {name}"),
        ("TimersCalendar", "a(ss)", [("OnCalendar", calendar(at))]),
        ("WakeSystem", "b", True),
        ("RemainAfterElapse", "b", False),
        ("AccuracyUSec", "t", 1_000_000),
        ("PartOf", "as", [f"user@{int(uid)}.service"]),
    ]


def service_properties(name: str) -> list[tuple[str, str, object]]:
    return [
        ("Description", "s", f"Wake the computer for {name}"),
        ("Type", "s", "oneshot"),
        ("ExecStart", "a(sasb)", [(TRUE, [TRUE], False)]),
        ("CollectMode", "s", "inactive-or-failed"),
    ]


class SystemdPort(Protocol):
    def list_timers(self, uid: int) -> list[str]: ...
    def start_timer(self, timer: str, properties: list, service: str, service_properties: list) -> None: ...
    def stop(self, unit: str) -> None: ...


class AuthorityPort(Protocol):
    def uid(self, sender: str) -> int: ...
    def authorized(self, sender: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Wakeup:
    name: str
    at: int
    unit: str


class WakeManager:
    """The rules, with systemd and polkit behind ports so they can be tested."""

    def __init__(self, systemd: SystemdPort, authority: AuthorityPort, clock=time.time) -> None:
        self.systemd = systemd
        self.authority = authority
        self.clock = clock

    def _caller(self, sender: str) -> int:
        uid = self.authority.uid(sender)
        if not self.authority.authorized(sender):
            journal.send(f"Refused a wake-up request from UID {uid}", priority="notice",
                         event="wake-refused", LUMA_WAKE_UID=str(uid))
            raise NotAuthorized("only the active local session may wake the computer")
        return uid

    def wakeups(self, uid: int) -> list[Wakeup]:
        found = []
        for unit in self.systemd.list_timers(uid):
            parsed = parse_timer_unit(unit)
            if parsed is not None and parsed[0] == uid:
                found.append(Wakeup(parsed[1], parsed[2], unit))
        return sorted(found, key=lambda item: (item.name, item.at))

    def set(self, sender: str, name: str, at: int) -> Wakeup:
        name = wakeup_name(name)
        uid = self._caller(sender)
        at = int(at)
        now = self.clock()
        if at < now + MIN_AHEAD_SECONDS:
            raise InvalidArgument("the wake-up time has passed")
        if at > now + MAX_AHEAD_SECONDS:
            raise InvalidArgument("a wake-up can be at most 31 days ahead")
        existing = self.wakeups(uid)
        same = [item for item in existing if item.name == name]
        kept = next((item for item in same if item.at == at), None)
        if kept is None:
            if len(existing) - len(same) >= MAX_PER_USER:
                raise LimitReached(f"at most {MAX_PER_USER} wake-ups per person")
            unit = timer_unit(uid, name, at)
            self.systemd.start_timer(unit, timer_properties(uid, name, at), service_unit(unit),
                                     service_properties(name))
            kept = Wakeup(name, at, unit)
            journal.send(f"Wake-up for {name} set for {calendar(at)}", event="wake-set",
                         app_id=name if ids.is_app_id(name) else "", LUMA_WAKE_UID=str(uid),
                         LUMA_WAKE_AT=str(at))
        for item in same:
            if item.unit != kept.unit:
                self.systemd.stop(item.unit)
        return kept

    def clear(self, sender: str, name: str) -> bool:
        name = wakeup_name(name)
        uid = self._caller(sender)
        cleared = False
        for item in self.wakeups(uid):
            if item.name == name:
                self.systemd.stop(item.unit)
                cleared = True
        if cleared:
            journal.send(f"Wake-up for {name} cleared", event="wake-cleared",
                         app_id=name if ids.is_app_id(name) else "", LUMA_WAKE_UID=str(uid))
        return cleared

    def list(self, sender: str) -> list[tuple[str, int]]:
        uid = self._caller(sender)
        return [(item.name, item.at) for item in self.wakeups(uid)]


# -- The system bus ------------------------------------------------------------

SYSTEMD = "org.freedesktop.systemd1"
SYSTEMD_PATH = "/org/freedesktop/systemd1"
SYSTEMD_MANAGER = "org.freedesktop.systemd1.Manager"
TIMEOUT_MS = 15_000


def _variant(signature: str, value: object) -> GLib.Variant:
    return GLib.Variant(signature, value)


def _properties(items: list[tuple[str, str, object]]) -> list[tuple[str, GLib.Variant]]:
    return [(key, _variant(signature, value)) for key, signature, value in items]


class SystemdClient:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection

    def _call(self, method: str, parameters: GLib.Variant, reply: str | None):
        return self.connection.call_sync(SYSTEMD, SYSTEMD_PATH, SYSTEMD_MANAGER, method, parameters,
                                         GLib.VariantType.new(reply) if reply else None,
                                         Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)

    def list_timers(self, uid: int) -> list[str]:
        # Only the timers still waiting. A transient timer that was stopped (a
        # wake-up moved or cleared) stays loaded until systemd collects it, and
        # counting those would wake nobody while using up the person's few
        # wake-ups.
        result = self._call("ListUnitsByPatterns",
                            GLib.Variant("(asas)", (["active", "activating"],
                                                    [f"{UNIT_PREFIX}u{int(uid)}-*.timer"])),
                            "(a(ssssssouso))")
        return [row[0] for row in result.unpack()[0]]

    def start_timer(self, timer: str, properties: list, service: str, service_properties: list) -> None:
        parameters = GLib.Variant("(ssa(sv)a(sa(sv)))", (
            timer, "fail", _properties(properties), [(service, _properties(service_properties))]))
        try:
            self._call("StartTransientUnit", parameters, "(o)")
        except GLib.Error as error:
            raise WakeError(f"the system manager refused the wake-up: {error.message}") from error

    def stop(self, unit: str) -> None:
        try:
            self._call("StopUnit", GLib.Variant("(ss)", (unit, "replace")), "(o)")
        except GLib.Error as error:
            if "NoSuchUnit" not in (Gio.DBusError.get_remote_error(error) or ""):
                journal.send(f"Could not stop {unit}: {error.message}", priority="warning", event="wake-stop-failed")


class PolkitAuthority:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection

    def uid(self, sender: str) -> int:
        result = self.connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                           "GetConnectionUnixUser", GLib.Variant("(s)", (sender,)),
                                           GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        return int(result.unpack()[0])

    def authorized(self, sender: str) -> bool:
        subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})
        try:
            result = self.connection.call_sync(
                "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
                "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization",
                GLib.Variant("((sa{sv})sa{ss}us)", (subject, ACTION, {}, 0, "")),
                GLib.VariantType.new("((bba{ss}))"), Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        except GLib.Error as error:
            # No polkit, no wake-ups: fail closed.
            journal.send(f"polkit could not be asked: {error.message}", priority="warning", event="wake-polkit-failed")
            return False
        return bool(result.unpack()[0][0])


class WakeService:
    def __init__(self, connection: Gio.DBusConnection, manager: WakeManager, loop: GLib.MainLoop) -> None:
        self.connection = connection
        self.manager = manager
        self.loop = loop
        self._idle = 0
        node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        connection.register_object(OBJECT_PATH, node.interfaces[0], self._call, self._get_property, None)
        self._touch()

    def _touch(self) -> None:
        if self._idle:
            GLib.source_remove(self._idle)
        self._idle = GLib.timeout_add_seconds(IDLE_EXIT_SECONDS, self._idle_exit)

    def _idle_exit(self) -> bool:
        self._idle = 0
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _get_property(self, _connection, _sender, _path, _interface, name):
        if name == "Version":
            return GLib.Variant("u", INTERFACE_VERSION)
        return None

    def _call(self, _connection, sender, _path, _interface, method, parameters, invocation) -> None:
        self._touch()
        try:
            if method == "SetWakeup":
                name, at = parameters.unpack()
                self.manager.set(sender, name, at)
                invocation.return_value(None)
            elif method == "ClearWakeup":
                (name,) = parameters.unpack()
                invocation.return_value(GLib.Variant("(b)", (self.manager.clear(sender, name),)))
            elif method == "ListWakeups":
                invocation.return_value(GLib.Variant("(a(sx))", (self.manager.list(sender),)))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except WakeError as error:
            invocation.return_dbus_error(ERROR_PREFIX + error.error, str(error))
        except GLib.Error as error:
            invocation.return_dbus_error(ERROR_PREFIX + "Failed", error.message)


def main() -> int:
    loop = GLib.MainLoop()
    connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    manager = WakeManager(SystemdClient(connection), PolkitAuthority(connection))
    WakeService(connection, manager, loop)

    def lost(_connection, _name) -> None:
        loop.quit()

    Gio.bus_own_name_on_connection(connection, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, None, lost)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    loop.run()
    return 0
