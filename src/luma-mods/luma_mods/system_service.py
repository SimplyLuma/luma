"""Polkit-gated system-bus boundary for future system Mod deployments.

The service is packaged but remains closed unless a root-owned enable marker and
validated recovery-readiness record both exist. Luma does not ship either gate.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .errors import LumaModsError, TransactionError
from .system_backend import RpmOstreeBackend
from .system_catalog import SystemCatalogAdmission
from .system_coordinator import SystemTransactionCoordinator
from .system_host import SYSTEM_HOST_CONFIG
from .catalog_runtime import SYSTEM_CLIENT_CONFIG
from .system_state import SystemStateStore
from .system_gate import (
    POLKIT_CHECK_AUTHORIZATION_SIGNATURE,
    polkit_authorized,
    validate_recovery_record,
)

BUS_NAME = "org.projectluma.ModTransactions1"
OBJECT_PATH = "/org/projectluma/ModTransactions1"
INTERFACE = BUS_NAME
ENABLE_MARKER = Path("/etc/luma/mod-transactions-enabled")
RECOVERY_RECORD = Path("/var/lib/luma/mods/recovery-ready.json")
INTROSPECTION_PATH = Path("/usr/share/dbus-1/interfaces/org.projectluma.ModTransactions1.xml")


def _recovery_ready() -> bool:
    try:
        def read_protected(path, limit):
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                info = os.fstat(descriptor)
                if (info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode)
                        or info.st_size > limit):
                    raise ValueError('Unsafe recovery readiness record.')
                payload = os.read(descriptor, limit + 1)
                if len(payload) > limit: raise ValueError('Oversized recovery readiness record.')
                return payload
            finally:
                os.close(descriptor)
        read_protected(ENABLE_MARKER, 4096)
        value = json.loads(read_protected(RECOVERY_RECORD, 65536).decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return False
    return validate_recovery_record(value)


class Authority:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self._proxy = Gio.DBusProxy.new_sync(
            connection,
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
            None,
            "org.freedesktop.PolicyKit1",
            "/org/freedesktop/PolicyKit1/Authority",
            "org.freedesktop.PolicyKit1.Authority",
            None,
        )

    def check(self, sender: str, action: str, allow_interaction: bool) -> None:
        parameters = GLib.Variant(
            POLKIT_CHECK_AUTHORIZATION_SIGNATURE,
            (
                (
                    "system-bus-name",
                    {"name": GLib.Variant("s", sender)},
                ),
                action,
                {},
                1 if allow_interaction else 0,
                "",
            ),
        )
        result = self._proxy.call_sync(
            "CheckAuthorization",
            parameters,
            Gio.DBusCallFlags.NONE,
            120_000 if allow_interaction else 10_000,
            None,
        )
        authorized = polkit_authorized(result.unpack())
        if not authorized:
            raise TransactionError("system Mod action was not authorized")


class ModTransactionService:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self._connection = connection
        self._authority = Authority(connection)
        self._backend = RpmOstreeBackend()
        self._state = SystemStateStore()
        self._coordinator = SystemTransactionCoordinator(self._backend, self._state)
        self._admission = None
        if _recovery_ready():
            self._coordinator.recover_incomplete_staging()
        xml = INTROSPECTION_PATH.read_text(encoding="utf-8")
        self._node = Gio.DBusNodeInfo.new_for_xml(xml)
        self._registration = connection.register_object(
            OBJECT_PATH,
            self._node.interfaces[0],
            self._method_call,
            None,
            None,
        )

    def _catalog_admission(self) -> SystemCatalogAdmission:
        if self._admission is None:
            self._admission = SystemCatalogAdmission.from_system(
                artifact_store=self._backend.artifact_store
            )
        return self._admission

    def _method_call(
        self,
        _connection,
        sender,
        _object_path,
        _interface_name,
        method_name,
        parameters,
        invocation,
    ) -> None:
        try:
            if method_name == "GetStatus":
                status = {
                    "schema": "org.luma.mod-transactions-status/v0.1",
                    "enabled": (
                        _recovery_ready()
                        and SYSTEM_CLIENT_CONFIG.is_file()
                        and SYSTEM_HOST_CONFIG.is_file()
                    ),
                    "backend": "rpm-ostree",
                    "catalog_configured": SYSTEM_CLIENT_CONFIG.is_file(),
                    "host_profile_configured": SYSTEM_HOST_CONFIG.is_file(),
                    "state": self._state.read(),
                }
                invocation.return_value(
                    GLib.Variant("(s)", (json.dumps(status, sort_keys=True),))
                )
                return
            if method_name == "StageCatalog":
                (
                    mod_id,
                    reviewed_snapshot_id,
                    reviewed_composition_sha256,
                    allow_interaction,
                ) = parameters.unpack()
                self._authority.check(
                    sender, "org.projectluma.mods.stage-system", allow_interaction
                )
                if not _recovery_ready():
                    raise TransactionError(
                        "system Mod deployment is closed until recovery readiness is proven"
                    )
                transaction = self._catalog_admission().prepare(
                    mod_id,
                    reviewed_snapshot_id=reviewed_snapshot_id,
                    reviewed_composition_sha256=reviewed_composition_sha256,
                    recovery_ready=True,
                )
                candidate = self._coordinator.stage(transaction.request)
                invocation.return_value(GLib.Variant("(s)", (candidate,)))
                return
            candidate, allow_interaction = parameters.unpack()
            if method_name == "Activate":
                self._authority.check(
                    sender, "org.projectluma.mods.activate-system", allow_interaction
                )
                if not _recovery_ready():
                    raise TransactionError("system Mod activation is closed")
                self._coordinator.activate(candidate)
            elif method_name == "Rollback":
                self._authority.check(
                    sender, "org.projectluma.mods.rollback-system", allow_interaction
                )
                self._coordinator.rollback(candidate)
            else:
                raise TransactionError(f"unknown system Mod method: {method_name}")
            invocation.return_value(None)
        except (
            LumaModsError,
            OSError,
            json.JSONDecodeError,
            GLib.Error,
            TypeError,
            ValueError,
        ) as error:
            invocation.return_dbus_error(
                "org.projectluma.ModTransactions1.Error.Refused", str(error)
            )


def main() -> int:
    loop = GLib.MainLoop()
    holder = {}

    def acquired(connection, _name):
        holder["service"] = ModTransactionService(connection)

    owner = Gio.bus_own_name(
        Gio.BusType.SYSTEM,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        acquired,
        None,
        lambda _connection, _name: loop.quit(),
    )
    try:
        loop.run()
    finally:
        Gio.bus_unown_name(owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
