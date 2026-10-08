"""Asynchronous graphical client for the narrow system Mod D-Bus API."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .errors import TransactionError

BUS_NAME = "org.projectluma.ModTransactions1"
OBJECT_PATH = "/org/projectluma/ModTransactions1"

Completion = Callable[[str | None, Exception | None], None]


class SystemTransactionClient:
    def __init__(self) -> None:
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM,
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                None,
                BUS_NAME,
                OBJECT_PATH,
                BUS_NAME,
                None,
            )
        except GLib.Error as error:
            raise TransactionError(error.message) from error

    @staticmethod
    def _complete(proxy: Gio.DBusProxy, result, callback: Completion) -> None:
        try:
            value = proxy.call_finish(result)
            unpacked = value.unpack() if value is not None else ()
            response = unpacked[0] if unpacked else ""
            callback(response, None)
        except GLib.Error as error:
            callback(None, TransactionError(error.message))

    def stage_catalog(
        self,
        identifier: str,
        snapshot_id: str,
        composition_sha256: str,
        callback: Completion,
    ) -> None:
        self.proxy.call(
            "StageCatalog",
            GLib.Variant("(sssb)", (
                identifier, snapshot_id, composition_sha256, True,
            )),
            Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
            30 * 60 * 1000,
            None,
            lambda proxy, result: self._complete(proxy, result, callback),
        )

    def activate(self, candidate_id: str, callback: Completion) -> None:
        self.proxy.call(
            "Activate",
            GLib.Variant("(sb)", (candidate_id, True)),
            Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
            120 * 1000,
            None,
            lambda proxy, result: self._complete(proxy, result, callback),
        )
