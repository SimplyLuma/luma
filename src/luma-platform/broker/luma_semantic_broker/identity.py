# SPDX-License-Identifier: Apache-2.0
"""Resolve app identity from bus credentials instead of caller-supplied IDs."""

from __future__ import annotations

import configparser
import os
from pathlib import Path
import re
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .desktop import desktop_application
from .model import Identity, IdentityStrength
from .validation import APP_ID, ValidationError, application_id


UNIQUE_NAME = re.compile(r"^:[0-9]+\.[0-9]+$")
SCOPE_APP = re.compile(
    r"(?:^|/)app-(?:gnome-)?(?P<app>[A-Za-z0-9_.\\x]+?)-[0-9A-Fa-f]+\.scope(?:$|/)"
)
# A background agent (ADR-033) runs as the app, in a unit luma-background
# generates from the app's validated declaration: app-<app-id>-agent.service
# under a luma-background slice. It publishes the app's Live Extensions with
# every window closed, so it carries the app's identity like a launch scope.
AGENT_APP = re.compile(
    r"/luma-background(?:-[a-z]+)?\.slice/app-(?P<app>[A-Za-z0-9_.\\x]+?)-agent\.service(?:$|/)"
)


class IdentityError(PermissionError):
    pass


def _variant_integer(value: Any, field: str) -> int:
    if isinstance(value, GLib.Variant):
        value = value.unpack()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IdentityError(f"D-Bus returned an invalid {field}")
    return value


def _unescape_systemd(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    return re.sub(r"\\x([0-9A-Fa-f]{2})", replace, value)


class DBusCredentialResolver:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self._connection = connection

    def credentials(self, sender: str) -> tuple[int, int]:
        if not isinstance(sender, str) or not UNIQUE_NAME.fullmatch(sender):
            raise IdentityError("semantic caller does not have a unique bus name")
        result, descriptor_list = self._connection.call_with_unix_fd_list_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionCredentials",
            GLib.Variant("(s)", (sender,)),
            GLib.VariantType.new("(a{sv})"),
            Gio.DBusCallFlags.NONE,
            5_000,
            None,
            None,
        )
        values = result.unpack()[0]
        uid = _variant_integer(values.get("UnixUserID"), "UnixUserID")
        pid = _variant_integer(values.get("ProcessID"), "ProcessID")
        process_handle = values.get("ProcessFD")
        if process_handle is not None:
            if descriptor_list is None:
                raise IdentityError("D-Bus omitted the process descriptor list")
            handle = _variant_integer(process_handle, "ProcessFD")
            try:
                descriptor = descriptor_list.get(handle)
            except GLib.Error as error:
                raise IdentityError("D-Bus returned an invalid process descriptor") from error
            try:
                fields = {}
                for line in Path(f"/proc/self/fdinfo/{descriptor}").read_text(
                    encoding="utf-8"
                ).splitlines():
                    key, separator, value = line.partition(":")
                    if separator:
                        fields[key] = value.strip()
                pinned_pid = int(fields.get("Pid", "0"))
            except (OSError, ValueError) as error:
                raise IdentityError("D-Bus process descriptor could not be verified") from error
            finally:
                os.close(descriptor)
            if pinned_pid != pid:
                raise IdentityError("D-Bus process identity changed during resolution")
        return uid, pid

    def name_owner(self, name: str) -> str:
        result = self._connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE,
            5_000,
            None,
        )
        return result.unpack()[0]

class IdentityResolver:
    def __init__(
        self,
        credentials: DBusCredentialResolver,
        *,
        proc_root: Path = Path("/proc"),
        desktop_lookup=None,
    ) -> None:
        self._credentials = credentials
        self._proc_root = proc_root
        self._desktop_lookup = desktop_lookup or self._desktop_application

    @staticmethod
    def _desktop_application(app_id: str) -> tuple[bool, str]:
        return desktop_application(app_id)

    def _flatpak_id(self, pid: int) -> str | None:
        path = self._proc_root / str(pid) / "root/.flatpak-info"
        try:
            parser = configparser.ConfigParser(interpolation=None)
            with path.open("r", encoding="utf-8") as stream:
                parser.read_file(stream)
            value = parser.get("Application", "name", fallback="").strip()
            return application_id(value) if value else None
        except (OSError, configparser.Error, ValidationError):
            return None

    def _cgroup_id(self, pid: int) -> str | None:
        try:
            lines = (self._proc_root / str(pid) / "cgroup").read_text(
                encoding="utf-8"
            ).splitlines()
        except OSError:
            return None
        for line in lines:
            match = SCOPE_APP.search(line) or AGENT_APP.search(line)
            if match is None:
                continue
            candidate = _unescape_systemd(match.group("app"))
            if APP_ID.fullmatch(candidate) and self._desktop_lookup(candidate)[0]:
                return candidate
        return None

    def resolve(self, sender: str) -> Identity:
        uid, pid = self._credentials.credentials(sender)
        if uid != os.getuid():
            raise IdentityError("semantic caller belongs to another user")
        flatpak_id = self._flatpak_id(pid)
        if flatpak_id:
            found, label = self._desktop_lookup(flatpak_id)
            return Identity(
                key=f"flatpak:{flatpak_id}",
                app_id=flatpak_id,
                label=label if found else flatpak_id,
                sender=sender,
                uid=uid,
                pid=pid,
                strength=IdentityStrength.SANDBOXED,
            )
        cgroup_id = self._cgroup_id(pid)
        if cgroup_id:
            _, label = self._desktop_lookup(cgroup_id)
            return Identity(
                key=f"native:{cgroup_id}",
                app_id=cgroup_id,
                label=label,
                sender=sender,
                uid=uid,
                pid=pid,
                strength=IdentityStrength.MANAGED_NATIVE,
            )
        return Identity(
            key=f"transient:{uid}:{sender}",
            app_id="",
            label="Unverified native application",
            sender=sender,
            uid=uid,
            pid=pid,
            strength=IdentityStrength.TRANSIENT_NATIVE,
        )

    def provider(self, sender: str, claimed_app_id: str) -> Identity:
        claimed = application_id(claimed_app_id)
        identity = self.resolve(sender)
        if not identity.app_id:
            raise IdentityError(
                "native provider is not running in a verified application scope"
            )
        if identity.app_id != claimed:
            raise IdentityError("provider application identity does not match caller")
        return identity

    def require_shell_host(self, sender: str) -> Identity:
        """Authenticate the running system Shell, not merely a claimed app id."""

        identity = self.resolve(sender)
        try:
            owner = self._credentials.name_owner("org.gnome.Shell")
        except GLib.Error as error:
            raise IdentityError("system Live Extension host could not be verified") from error
        if owner != sender:
            raise IdentityError("Live Extensions may only be read by the system Shell")
        process = self._proc_root / str(identity.pid)
        try:
            executable = (process / "exe").readlink()
            executable_verified = executable == Path("/usr/bin/gnome-shell")
        except PermissionError:
            # Mount-namespace hardening deliberately makes the immutable exe
            # link unreadable. In that constrained case require all remaining
            # kernel-owned metadata together: exact command, process name, and
            # GNOME Shell's dedicated systemd user-service cgroup.
            try:
                command = (process / "cmdline").read_bytes().split(b"\0")
                process_name = (process / "comm").read_text(encoding="utf-8").strip()
                cgroup = (process / "cgroup").read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise IdentityError("system Live Extension host could not be verified") from error
            executable_verified = (
                command[:2] == [b"/usr/bin/gnome-shell", b"--mode=user"]
                and process_name == "gnome-shell"
                and re.search(
                    r"(?:^|/)org\.gnome\.Shell@user\.service(?:$|/)",
                    cgroup,
                    re.MULTILINE,
                )
                and "app-" not in cgroup
            )
        except OSError as error:
            raise IdentityError("system Live Extension host could not be verified") from error
        if not executable_verified:
            raise IdentityError("Live Extensions may only be read by the system Shell")
        return identity
