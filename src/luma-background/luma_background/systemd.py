# SPDX-License-Identifier: MPL-2.0
"""The person's systemd user manager, over its D-Bus API.

Unit files are written to $XDG_RUNTIME_DIR/systemd/user and tracked in a
manifest, so the service only ever rewrites or removes what it wrote itself:
`systemctl --user --runtime` uses the same directory for its own links.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Iterable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import cgroup, journal, units  # noqa: E402
from .manager import UnitStatus  # noqa: E402

SYSTEMD = "org.freedesktop.systemd1"
MANAGER_PATH = "/org/freedesktop/systemd1"
MANAGER = "org.freedesktop.systemd1.Manager"
UNIT = "org.freedesktop.systemd1.Unit"
PROPERTIES = "org.freedesktop.DBus.Properties"
_OWNED_NAME = re.compile(r"^app-[A-Za-z0-9_.\\]+(-agent(-[a-z0-9-]+)?\.(service|timer)|@autostart\.service)$")
JOB_TIMEOUT_MS = 120_000


def runtime_unit_directory() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime.startswith("/"):
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    return Path(runtime) / "systemd/user"


def unit_from_object_path(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return re.sub(r"_([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), name)


class SystemdClient:
    def __init__(self, connection: Gio.DBusConnection, *, directory: Path | None = None,
                 on_unit_changed: Callable[[str], None] = lambda unit: None) -> None:
        self.connection = connection
        self.directory = directory or runtime_unit_directory()
        self.manifest = self.directory.parent.parent / "luma-background" / "owned-units.json"
        # A start queued while one is pending joins the same job, so several
        # callers can be waiting on one job path.
        self._jobs: dict[str, list[Callable[[bool, str], None]]] = {}
        self._on_unit_changed = on_unit_changed
        self.connection.signal_subscribe(
            SYSTEMD, MANAGER, "JobRemoved", MANAGER_PATH, None,
            Gio.DBusSignalFlags.NONE, self._job_removed)
        self.connection.signal_subscribe(
            SYSTEMD, PROPERTIES, "PropertiesChanged", None, UNIT,
            Gio.DBusSignalFlags.NONE, self._properties_changed)
        self._call("Subscribe", None, None, lambda ok, result, message: None)

    # -- Plumbing ------------------------------------------------------------

    def _call(self, method: str, parameters, reply_type: str | None,
              done: Callable[[bool, object, str], None], *, path: str = MANAGER_PATH,
              interface: str = MANAGER, timeout: int = JOB_TIMEOUT_MS) -> None:
        def finished(connection, result) -> None:
            try:
                value = connection.call_finish(result)
            except GLib.Error as error:
                done(False, None, f"{Gio.DBusError.get_remote_error(error) or ''} {error.message}".strip())
                return
            done(True, value, "")

        self.connection.call(SYSTEMD, path, interface, method, parameters,
                             GLib.VariantType.new(reply_type) if reply_type else None,
                             Gio.DBusCallFlags.NONE, timeout, None, finished)

    def _call_sync(self, method: str, parameters, reply_type: str, *, path: str = MANAGER_PATH,
                   interface: str = MANAGER):
        return self.connection.call_sync(SYSTEMD, path, interface, method, parameters,
                                         GLib.VariantType.new(reply_type), Gio.DBusCallFlags.NONE,
                                         10_000, None)

    def _job_removed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        _id, job, unit, result = parameters.unpack()
        for done in self._jobs.pop(job, []):
            done(result == "done", "" if result == "done" else f"job for {unit} ended: {result}")

    def _properties_changed(self, _connection, _sender, path, _interface, _signal, parameters) -> None:
        interface = parameters.get_child_value(0).get_string()
        if interface != UNIT:
            return
        unit = unit_from_object_path(path)
        if unit.startswith("app-") and unit.endswith(".service"):
            self._on_unit_changed(unit)

    def _job(self, method: str, unit: str, done: Callable[[bool, str], None]) -> None:
        def queued(ok: bool, value, message: str) -> None:
            if not ok:
                done(False, message)
                return
            self._jobs.setdefault(value.unpack()[0], []).append(done)

        self._call(method, GLib.Variant("(ss)", (unit, "replace")), "(o)", queued)

    # -- Files ---------------------------------------------------------------

    def _owned(self) -> set[str]:
        try:
            names = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {name for name in names if isinstance(name, str) and _OWNED_NAME.fullmatch(name)}

    def _write_manifest(self, names: set[str]) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(sorted(names)), encoding="utf-8")
        os.replace(temporary, self.manifest)

    def sync_units(self, files: dict[str, str], links: dict[str, str],
                   owned: Iterable[str] = ()) -> bool:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        previous = self._owned()
        wanted = set(files) | set(links)
        changed = False
        for name in sorted(previous - wanted):
            path = self.directory / name
            try:
                path.unlink()
                changed = True
            except FileNotFoundError:
                pass
            except OSError as error:
                journal.send(f"Could not remove {name}: {error.strerror}", priority="warning",
                             event="unit-write-failed")
        now_owned: set[str] = set()
        for name, content in sorted(files.items()):
            if not _OWNED_NAME.fullmatch(name):
                raise ValueError(f"refusing to write unit {name!r}")
            path = self.directory / name
            if path.is_symlink() or (path.exists() and name not in previous):
                journal.send(f"Not overwriting {name}, which luma-background did not write",
                             priority="warning", event="unit-conflict")
                continue
            now_owned.add(name)
            try:
                if path.read_text(encoding="utf-8") == content:
                    continue
            except OSError:
                pass
            temporary = path.with_name(f".{name}.tmp")
            temporary.write_text(content, encoding="utf-8")
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
            changed = True
        for name, target in sorted(links.items()):
            if not _OWNED_NAME.fullmatch(name) or target != "/dev/null":
                raise ValueError(f"refusing to link unit {name!r}")
            path = self.directory / name
            if path.is_symlink() and os.readlink(path) == target:
                now_owned.add(name)
                continue
            if path.exists() or path.is_symlink():
                if name not in previous:
                    journal.send(f"Not replacing {name}, which luma-background did not write",
                                 priority="warning", event="unit-conflict")
                    continue
                path.unlink()
            os.symlink(target, path)
            now_owned.add(name)
            changed = True
        if now_owned != previous:
            self._write_manifest(now_owned)
        return changed

    # -- Manager -------------------------------------------------------------

    def reload(self, done: Callable[[bool], None]) -> None:
        def finished(ok: bool, _value, message: str) -> None:
            if not ok:
                journal.send(f"systemd reload failed: {message}", priority="error", event="reload-failed")
            done(ok)

        self._call("Reload", None, None, finished)

    def start(self, unit: str, done: Callable[[bool, str], None]) -> None:
        self._job("StartUnit", unit, done)

    def stop(self, unit: str, done: Callable[[bool, str], None]) -> None:
        self._job("StopUnit", unit, done)

    def reset_failed(self, unit: str) -> None:
        self._call("ResetFailedUnit", GLib.Variant("(s)", (unit,)), None, lambda ok, value, message: None)

    def freeze(self, unit: str, done: Callable[[bool, str], None]) -> None:
        self._call("FreezeUnit", GLib.Variant("(s)", (unit,)), None,
                   lambda ok, value, message: done(ok, message))

    def thaw(self, unit: str, done: Callable[[bool, str], None]) -> None:
        self._call("ThawUnit", GLib.Variant("(s)", (unit,)), None,
                   lambda ok, value, message: done(ok, message))

    def status(self, unit: str) -> UnitStatus:
        try:
            path = self._call_sync("GetUnit", GLib.Variant("(s)", (unit,)), "(o)").unpack()[0]
            props = self._call_sync("GetAll", GLib.Variant("(s)", ("",)), "(a{sv})",
                                    path=path, interface=PROPERTIES).unpack()[0]
        except GLib.Error:
            return UnitStatus()
        return UnitStatus(
            active_state=str(props.get("ActiveState", "inactive")),
            sub_state=str(props.get("SubState", "")),
            result=str(props.get("Result", "")),
            control_group=str(props.get("ControlGroup", "")),
            restarts=int(props.get("NRestarts", 0) or 0),
            active_enter_usec=int(props.get("ActiveEnterTimestamp", 0) or 0),
            freezer_state=str(props.get("FreezerState", "running")),
            main_pid=int(props.get("MainPID", 0) or 0),
        )

    def usage(self, status: UnitStatus) -> cgroup.Usage:
        return cgroup.read_usage(status.control_group)

    def process_unit(self, pid: int) -> str:
        try:
            path = self._call_sync("GetUnitByPID", GLib.Variant("(u)", (pid,)), "(o)").unpack()[0]
            value = self._call_sync("Get", GLib.Variant("(ss)", (UNIT, "Id")), "(v)",
                                    path=path, interface=PROPERTIES).unpack()[0]
        except GLib.Error:
            return ""
        return str(value)

    def set_limits(self, unit: str, memory_high: int, memory_max: int) -> None:
        properties = GLib.Variant("(sba(sv))", (unit, True, [
            ("MemoryHigh", GLib.Variant("t", memory_high)),
            ("MemoryMax", GLib.Variant("t", memory_max)),
            ("CPUWeight", GLib.Variant("t", 20)),
            ("IOWeight", GLib.Variant("t", 20)),
        ]))
        self._call("SetUnitProperties", properties, None, lambda ok, value, message: ok or journal.send(
            f"Could not limit {unit}: {message}", priority="warning", event="limits-failed"))
