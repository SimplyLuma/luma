# SPDX-License-Identifier: MPL-2.0
"""The one-time question, asked as a notification.

A notification rather than a dialog: it appears where the person already
looks, in the Shell's own style, does not take focus from what they are doing,
and waits there until answered. It is asked once; dismissing it is an answer
of "not now", after which the app stays off until the person turns it on.
"""

from __future__ import annotations

import gettext
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import journal  # noqa: E402

_ = gettext.translation("luma-background", fallback=True).gettext

NOTIFICATIONS = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
DESKTOP_ENTRY = "org.projectluma.Background"


class NotificationPrompt:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection
        self._waiting: dict[int, tuple[str, Callable[[str | None], None]]] = {}
        connection.signal_subscribe(NOTIFICATIONS, NOTIFICATIONS, "ActionInvoked",
                                    NOTIFICATIONS_PATH, None, Gio.DBusSignalFlags.NONE,
                                    self._action_invoked)
        connection.signal_subscribe(NOTIFICATIONS, NOTIFICATIONS, "NotificationClosed",
                                    NOTIFICATIONS_PATH, None, Gio.DBusSignalFlags.NONE,
                                    self._closed)

    @staticmethod
    def text(record, reason: str) -> tuple[str, str]:
        title = _("Let {app} work in the background?").format(app=record.name)
        purpose = record.category.explanation(record.name)
        body = f"{purpose}. " + _("You can change this in Settings.")
        if reason:
            # The app's reason is its own words, so it is shown as a quote of
            # the app and never as the system's explanation.
            cleaned = " ".join(reason.split())[:256]
            body = f"{purpose}. “{cleaned}” " + _("You can change this in Settings.")
        return title, body

    def ask(self, record, reason: str, answer: Callable[[str | None], None]) -> None:
        title, body = self.text(record, reason)
        hints = {
            "desktop-entry": GLib.Variant("s", DESKTOP_ENTRY),
            "urgency": GLib.Variant("y", 1),
            "resident": GLib.Variant("b", True),
            "category": GLib.Variant("s", "x-luma.background-request"),
            "x-luma-app-id": GLib.Variant("s", record.app_id),
        }
        parameters = GLib.Variant("(susssasa{sv}i)", (
            _("Background Activity"), 0, record.icon or "application-x-executable",
            title, body, ["deny", _("Don’t Allow"), "allow", _("Allow")], hints, 0,
        ))

        def sent(connection, result) -> None:
            try:
                notification_id = connection.call_finish(result).unpack()[0]
            except GLib.Error as error:
                journal.send(f"Could not show the background request: {error.message}",
                             priority="warning", app_id=record.app_id, event="prompt-failed")
                answer(None)
                return
            self._waiting[notification_id] = (record.app_id, answer)

        self.connection.call(NOTIFICATIONS, NOTIFICATIONS_PATH, NOTIFICATIONS, "Notify",
                             parameters, GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE,
                             10_000, None, sent)

    def dismiss(self, app_id: str) -> None:
        for notification_id, (waiting_app, _answer) in list(self._waiting.items()):
            if waiting_app == app_id:
                self.connection.call(NOTIFICATIONS, NOTIFICATIONS_PATH, NOTIFICATIONS,
                                     "CloseNotification", GLib.Variant("(u)", (notification_id,)),
                                     None, Gio.DBusCallFlags.NONE, 5_000, None, None)

    def _action_invoked(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        notification_id, action = parameters.unpack()
        waiting = self._waiting.pop(notification_id, None)
        if waiting is None:
            return
        self.connection.call(NOTIFICATIONS, NOTIFICATIONS_PATH, NOTIFICATIONS, "CloseNotification",
                             GLib.Variant("(u)", (notification_id,)), None,
                             Gio.DBusCallFlags.NONE, 5_000, None, None)
        waiting[1](action if action in {"allow", "deny"} else None)

    def _closed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        notification_id, _reason = parameters.unpack()
        waiting = self._waiting.pop(notification_id, None)
        if waiting is not None:
            waiting[1](None)
