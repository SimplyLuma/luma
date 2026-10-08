# SPDX-License-Identifier: Apache-2.0

"""Shell-facing system state for native calls.

The Semantic Broker's Live Extension publication ABI is intentionally still a
preview.  Until that authenticated broker ships, a resident freedesktop
notification is the supported upstream shell boundary: Phone publishes only
structured text and actions, while the shell owns rendering and placement.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


class TransientShellSurface:
    """Lease one bounded shell-edge treatment for this process lifetime."""

    BUS_NAME = "org.project_luma.ShellState1"
    OBJECT_PATH = "/org/project_luma/ShellState1"
    INTERFACE = BUS_NAME

    def __init__(self, application_id: str) -> None:
        self._application_id = application_id
        self._connection = None

    def set(self, surface: str) -> None:
        try:
            if self._connection is None:
                self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._connection.call(
                self.BUS_NAME,
                self.OBJECT_PATH,
                self.INTERFACE,
                "SetTransientSurface",
                GLib.Variant("(ss)", (self._application_id, surface)),
                None,
                Gio.DBusCallFlags.NONE,
                1_000,
                None,
                None,
                None,
            )
        except GLib.Error:
            # Non-Phosh sessions and early startup legitimately lack the broker.
            self._connection = None

    def clear(self) -> None:
        self.set("")


class IncomingCallIndicator:
    """Persistent shell-owned incoming-call actions for an unlocked session."""

    BUS_NAME = "org.freedesktop.Notifications"
    OBJECT_PATH = "/org/freedesktop/Notifications"
    INTERFACE = "org.freedesktop.Notifications"

    def __init__(self, *, on_expand, on_answer, on_decline) -> None:
        self._on_expand = on_expand
        self._on_answer = on_answer
        self._on_decline = on_decline
        self._proxy = None
        self._notification_id = 0
        self._caller = ""
        self._republish_source = 0
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                self.BUS_NAME,
                self.OBJECT_PATH,
                self.INTERFACE,
                None,
            )
            self._proxy.connect("g-signal", self._signal)
        except GLib.Error:
            self._proxy = None

    def publish(self, caller: str) -> None:
        self._caller = caller or "Incoming call"
        self._refresh()

    def withdraw(self) -> None:
        if self._republish_source:
            GLib.source_remove(self._republish_source)
            self._republish_source = 0
        if self._proxy is not None and self._notification_id:
            try:
                self._proxy.call_sync(
                    "CloseNotification",
                    GLib.Variant("(u)", (self._notification_id,)),
                    Gio.DBusCallFlags.NONE,
                    2_000,
                    None,
                )
            except GLib.Error:
                pass
        self._notification_id = 0
        self._caller = ""

    def _refresh(self) -> None:
        if self._proxy is None or not self._caller:
            return
        try:
            result = self._proxy.call_sync(
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        "Luma Phone",
                        self._notification_id,
                        "org.projectluma.Phone",
                        self._caller,
                        "Incoming call",
                        [
                            "default", "Open",
                            "answer", "Answer",
                            "decline", "Decline",
                        ],
                        {
                            "urgency": GLib.Variant("y", 2),
                            "resident": GLib.Variant("b", True),
                            "transient": GLib.Variant("b", False),
                            "category": GLib.Variant("s", "call.incoming"),
                            "desktop-entry": GLib.Variant(
                                "s", "org.projectluma.Phone"
                            ),
                        },
                        0,
                    ),
                ),
                Gio.DBusCallFlags.NONE,
                2_000,
                None,
            )
            if result is not None:
                self._notification_id = int(result.unpack()[0])
        except GLib.Error:
            pass

    def _republish(self) -> bool:
        self._republish_source = 0
        if self._caller:
            self._refresh()
        return GLib.SOURCE_REMOVE

    def _signal(self, _proxy, _sender, signal_name: str, parameters) -> None:
        if signal_name == "ActionInvoked":
            notification_id, action = parameters.unpack()
            if int(notification_id) != self._notification_id:
                return
            if action == "answer":
                self._on_answer()
            elif action == "decline":
                self._on_decline()
            else:
                self._on_expand()
        elif signal_name == "NotificationClosed":
            notification_id, _reason = parameters.unpack()
            if int(notification_id) != self._notification_id:
                return
            self._notification_id = 0
            if self._caller and not self._republish_source:
                self._republish_source = GLib.timeout_add(250, self._republish)


class ActiveCallIndicator:
    """Resident, actionable call state rendered by the current shell."""

    BUS_NAME = "org.freedesktop.Notifications"
    OBJECT_PATH = "/org/freedesktop/Notifications"
    INTERFACE = "org.freedesktop.Notifications"

    def __init__(self, *, on_return, on_mute, on_end,
                 application_id: str = "org.projectluma.Phone") -> None:
        self._application_id = application_id
        self._on_return = on_return
        self._on_mute = on_mute
        self._on_end = on_end
        self._proxy = None
        self._notification_id = 0
        self._caller = ""
        self._connected_at = 0
        self._muted = False
        self._timer_source = 0
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                self.BUS_NAME,
                self.OBJECT_PATH,
                self.INTERFACE,
                None,
            )
            self._proxy.connect("g-signal", self._signal)
        except GLib.Error:
            self._proxy = None

    def publish(self, caller: str, connected_at: int, *, muted: bool = False) -> None:
        self._caller = caller or "Call in progress"
        self._connected_at = max(0, int(connected_at))
        self._muted = bool(muted)
        self._refresh()
        if not self._timer_source:
            self._timer_source = GLib.timeout_add_seconds(15, self._tick)

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._notification_id:
            self._refresh()

    def withdraw(self) -> None:
        if self._timer_source:
            GLib.source_remove(self._timer_source)
            self._timer_source = 0
        if self._proxy is not None and self._notification_id:
            try:
                self._proxy.call_sync(
                    "CloseNotification",
                    GLib.Variant("(u)", (self._notification_id,)),
                    Gio.DBusCallFlags.NONE,
                    2_000,
                    None,
                )
            except GLib.Error:
                pass
        self._notification_id = 0
        self._caller = ""
        self._connected_at = 0

    def _tick(self) -> bool:
        if not self._caller:
            self._timer_source = 0
            return GLib.SOURCE_REMOVE
        self._refresh()
        return GLib.SOURCE_CONTINUE

    def _refresh(self) -> None:
        if self._proxy is None or not self._caller:
            return
        elapsed = max(0, int(time.time()) - self._connected_at) if self._connected_at else 0
        minutes, seconds = divmod(elapsed, 60)
        body = f"{minutes}:{seconds:02d}" if self._connected_at else "Connecting"
        actions = [
            "default",
            "Return to call",
            "mute",
            "Unmute" if self._muted else "Mute",
            "end",
            "End",
        ]
        hints = {
            "urgency": GLib.Variant("y", 2),
            "resident": GLib.Variant("b", True),
            "transient": GLib.Variant("b", False),
            "category": GLib.Variant("s", "call"),
            "desktop-entry": GLib.Variant("s", self._application_id),
        }
        try:
            result = self._proxy.call_sync(
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        "Luma Phone",
                        self._notification_id,
                        "org.projectluma.Phone",
                        self._caller,
                        body,
                        actions,
                        hints,
                        0,
                    ),
                ),
                Gio.DBusCallFlags.NONE,
                2_000,
                None,
            )
            if result is not None:
                self._notification_id = int(result.unpack()[0])
        except GLib.Error:
            pass

    def _signal(self, _proxy, _sender, signal_name: str, parameters) -> None:
        if signal_name == "ActionInvoked":
            notification_id, action = parameters.unpack()
            if int(notification_id) != self._notification_id:
                return
            if action == "default":
                self._on_return()
            elif action == "mute":
                self._on_mute(not self._muted)
            elif action == "end":
                self._on_end()
        elif signal_name == "NotificationClosed":
            notification_id, _reason = parameters.unpack()
            if int(notification_id) == self._notification_id:
                # Calls remain authoritative even if a shell permits manual
                # dismissal. The next state/timer update republishes it.
                self._notification_id = 0
