# SPDX-License-Identifier: Apache-2.0
"""Fail-closed session-lock state used by semantic authorization."""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


class SessionLockMonitor:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self._connection = connection

    def locked(self) -> bool:
        """Unknown lock state is treated as locked, never as implicit consent."""

        try:
            result = self._connection.call_sync(
                "org.gnome.ScreenSaver",
                "/org/gnome/ScreenSaver",
                "org.gnome.ScreenSaver",
                "GetActive",
                None,
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE,
                2_000,
                None,
            )
            active = result.unpack()[0]
            return active if isinstance(active, bool) else True
        except GLib.Error:
            return True
