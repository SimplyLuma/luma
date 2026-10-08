# SPDX-License-Identifier: MPL-2.0
"""Who is calling, established from the kernel and the bus, never from arguments.

The approach follows the Semantic Broker's identity resolver
(src/luma-platform/broker/luma_semantic_broker/identity.py): a Flatpak is
known by the sandbox's own .flatpak-info, a native process by the systemd
unit the session launched it into.
"""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import ids  # noqa: E402

UNIQUE_NAME = re.compile(r"^:[0-9]+\.[0-9]+$")
TRUSTED_HOSTS = {
    # The shells that draw the dock menu, and Settings.
    "/usr/bin/gnome-shell": "org.gnome.Shell",
    "/usr/bin/phosh": "sm.puri.Phosh",
    "/usr/libexec/phosh": "sm.puri.Phosh",
    "/usr/bin/gnome-control-center": "",
}
_SCOPE = re.compile(
    r"^app-(?:(?:gnome|flatpak|kde|luma|systemd)-)?(?P<app>[A-Za-z0-9_.\\]+?)-[0-9]+\.scope$"
)
_DBUS_SERVICE = re.compile(r"^dbus-:[0-9.]+-(?P<app>[A-Za-z0-9_.\\]+?)@[0-9]+\.service$")
_APP_SERVICE = re.compile(r"^app-(?:(?:gnome|flatpak|luma)-)?(?P<app>[A-Za-z0-9_.\\]+?)@[0-9a-z]+\.service$")


class IdentityError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class Caller:
    sender: str
    pid: int
    uid: int
    app_id: str = ""
    sandboxed: bool = False
    #: The last component of the caller's cgroup, e.g. app-org.x.Y-agent.service.
    unit: str = ""
    executable: str = ""

    @property
    def is_agent(self) -> bool:
        return bool(self.app_id) and self.unit == ids.agent_unit(self.app_id)


def unit_app(unit: str) -> str:
    """The application a session unit was launched for, or an empty string."""

    agent = ids.parse_agent_unit(unit)
    if agent:
        return agent
    for pattern in (_SCOPE, _DBUS_SERVICE, _APP_SERVICE):
        match = pattern.fullmatch(unit)
        if match:
            candidate = ids.systemd_unescape(match.group("app"))
            if ids.is_app_id(candidate):
                return candidate
    return ""


class IdentityResolver:
    def __init__(self, connection: Gio.DBusConnection, *, proc: Path = Path("/proc"),
                 extra_trusted: tuple[str, ...] = ()) -> None:
        self.connection = connection
        self.proc = proc
        self.trusted = dict(TRUSTED_HOSTS)
        for executable in extra_trusted:
            if executable.startswith("/"):
                self.trusted.setdefault(executable, "")

    def _credentials(self, sender: str) -> tuple[int, int]:
        if not UNIQUE_NAME.fullmatch(sender or ""):
            raise IdentityError("caller has no unique bus name")
        result = self.connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "GetConnectionCredentials", GLib.Variant("(s)", (sender,)),
            GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 5_000, None)
        values = result.unpack()[0]
        uid, pid = values.get("UnixUserID"), values.get("ProcessID")
        if not isinstance(uid, int) or not isinstance(pid, int) or pid <= 0:
            raise IdentityError("the bus did not report the caller's process")
        return uid, pid

    def _flatpak_app(self, pid: int) -> str:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            with (self.proc / str(pid) / "root/.flatpak-info").open("r", encoding="utf-8") as stream:
                parser.read_file(stream)
        except (OSError, configparser.Error, UnicodeError):
            return ""
        name = parser.get("Application", "name", fallback="").strip()
        return name if ids.is_app_id(name) else ""

    def _unit(self, pid: int) -> str:
        try:
            lines = (self.proc / str(pid) / "cgroup").read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        for line in lines:
            if line.startswith("0::"):
                return line[3:].rstrip("/").rsplit("/", 1)[-1]
        return ""

    def _executable(self, pid: int) -> str:
        process = self.proc / str(pid)
        try:
            return os.readlink(process / "exe")
        except PermissionError:
            pass
        except OSError:
            return ""
        # GNOME Shell carries a file capability, which makes it non-dumpable
        # and its exe link unreadable to the user. Then require everything
        # else the kernel reports to agree: the command, the process name,
        # and the Shell's own systemd service; the bus-name check in
        # is_trusted_host still applies on top.
        try:
            command = (process / "cmdline").read_bytes().split(b"\0")[0].decode()
            name = (process / "comm").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return ""
        unit = self._unit(pid)
        if command in {"/usr/bin/gnome-shell"} and name == "gnome-shell" \
                and re.fullmatch(r"org\.gnome\.Shell@[a-z0-9-]+\.service", unit):
            return command
        return ""

    def resolve(self, sender: str) -> Caller:
        uid, pid = self._credentials(sender)
        if uid != os.getuid():
            raise IdentityError("caller belongs to another user")
        flatpak = self._flatpak_app(pid)
        unit = self._unit(pid)
        if flatpak:
            return Caller(sender, pid, uid, flatpak, True, unit, "")
        return Caller(sender, pid, uid, unit_app(unit), False, unit, self._executable(pid))

    def is_trusted_host(self, caller: Caller) -> bool:
        """The Shell, Phosh, or Settings: the places a person flips the switch."""

        if caller.sandboxed or caller.executable not in self.trusted:
            return False
        required_name = self.trusted[caller.executable]
        if not required_name:
            return True
        try:
            owner = self.connection.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "GetNameOwner", GLib.Variant("(s)", (required_name,)),
                GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 2_000, None).unpack()[0]
        except GLib.Error:
            return False
        return owner == caller.sender
