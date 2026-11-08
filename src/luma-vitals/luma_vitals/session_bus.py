# SPDX-License-Identifier: Apache-2.0
"""The person's session bus: which app has focus, and notifications that can be answered.

Focus comes from GNOME Shell's Introspect interface (GetRunningApplications),
asked under the bus name org.projectluma.Vitals1. The Shell answers only
callers on its allowlist; until Vitals is on it, focus is unknown and the
memory policy acts as if any busy app may be the one in use. Answers
to a notification ("Close", "Keep Open") arrive as signals, received on a
thread of their own and handed to the sampling loop through a queue.

Without GObject introspection or a session bus nothing here raises: focus is
unknown and notifications go out through gdbus, without buttons.
"""
from __future__ import annotations

import queue
import subprocess
import threading

BUS_NAME = "org.projectluma.Vitals1"
NOTIFICATIONS = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
SHELL = "org.gnome.Shell"
#: gnome-settings-daemon's housekeeping plugin, which names systemd-oomd kills.
HOUSEKEEPING = "org.gnome.SettingsDaemon.Housekeeping"
INTROSPECT_PATH = "/org/gnome/Shell/Introspect"
INTROSPECT = "org.gnome.Shell.Introspect"
#: Notifications about memory come from Monitor, where the person can see it.
SOURCE_APP = "Monitor"
SOURCE_DESKTOP_ENTRY = "io.luma.Monitor"


class SessionBus:
    def __init__(self) -> None:
        self.actions: queue.Queue = queue.Queue()
        self.connection = None
        self._started = False
        self._glib = None

    def _connect(self):
        if self._started:
            return self.connection
        self._started = True
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError):
            return None
        self._glib = (Gio, GLib)
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            # DBUS_NAME_FLAG_DO_NOT_QUEUE: a second Vitals simply goes without.
            connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "RequestName", GLib.Variant("(su)", (BUS_NAME, 4)),
                                 GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:
            return None
        self.connection = connection
        ready = threading.Event()
        threading.Thread(target=self._listen, args=(ready,), name="vitals-session-bus", daemon=True).start()
        ready.wait(2)
        return connection

    def _listen(self, ready: threading.Event) -> None:
        Gio, GLib = self._glib
        context = GLib.MainContext.new()
        context.push_thread_default()
        loop = GLib.MainLoop.new(context, False)

        def invoked(_connection, _sender, _path, _interface, signal, parameters, *_):
            if signal == "ActionInvoked":
                identity, key = parameters.unpack()
                self.actions.put((int(identity), str(key)))
            elif signal == "NotificationClosed":
                self.actions.put((int(parameters.unpack()[0]), "closed"))

        self.connection.signal_subscribe(NOTIFICATIONS, NOTIFICATIONS, None, NOTIFICATIONS_PATH, None,
                                         Gio.DBusSignalFlags.NONE, invoked)
        ready.set()
        loop.run()

    def focused_apps(self) -> set[str] | None:
        """Desktop ids (without .desktop) of the focused app, or None when the Shell will not say."""
        connection = self._connect()
        if connection is None:
            return None
        Gio, GLib = self._glib
        try:
            reply = connection.call_sync(SHELL, INTROSPECT_PATH, INTROSPECT, "GetRunningApplications", None,
                                         GLib.VariantType.new("(a{sa{sv}})"), Gio.DBusCallFlags.NONE, 1000, None)
        except Exception:
            return None
        focused = set()
        for app_id, properties in reply.unpack()[0].items():
            if properties.get("active-on-seats"):
                focused.add(app_id.removesuffix(".desktop"))
                sandboxed = properties.get("sandboxed-app-id")
                if sandboxed:
                    focused.add(sandboxed)
        return focused

    def has_owner(self, name: str) -> bool:
        connection = self._connect()
        if connection is None:
            return False
        Gio, GLib = self._glib
        try:
            reply = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                         "NameHasOwner", GLib.Variant("(s)", (name,)),
                                         GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
        except Exception:
            return False
        return bool(reply.unpack()[0])

    def notify(self, summary: str, body: str, *, icon: str = "", actions: tuple[str, ...] = (),
               replaces: int = 0) -> int:
        """The notification's id (0 when it could not be shown)."""
        icon = icon or "utilities-system-monitor-symbolic"
        connection = self._connect()
        if connection is None:
            return self._notify_gdbus(summary, body, icon)
        Gio, GLib = self._glib
        hints = {"desktop-entry": GLib.Variant("s", SOURCE_DESKTOP_ENTRY), "urgency": GLib.Variant("y", 1)}
        try:
            reply = connection.call_sync(NOTIFICATIONS, NOTIFICATIONS_PATH, NOTIFICATIONS, "Notify",
                                         GLib.Variant("(susssasa{sv}i)", (SOURCE_APP, replaces, icon, summary, body,
                                                                         list(actions), hints, -1)),
                                         GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        except Exception:
            return 0
        return int(reply.unpack()[0])

    def withdraw(self, identity: int) -> None:
        connection = self._connect()
        if connection is None or not identity:
            return
        Gio, GLib = self._glib
        try:
            connection.call_sync(NOTIFICATIONS, NOTIFICATIONS_PATH, NOTIFICATIONS, "CloseNotification",
                                 GLib.Variant("(u)", (identity,)), None, Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:
            pass

    @staticmethod
    def _notify_gdbus(summary: str, body: str, icon: str) -> int:
        try:
            result = subprocess.run(["gdbus", "call", "--session", "--dest", NOTIFICATIONS,
                                     "--object-path", NOTIFICATIONS_PATH, "--method", f"{NOTIFICATIONS}.Notify",
                                     SOURCE_APP, "0", icon, summary, body, "[]",
                                     f"{{'urgency': <byte 1>, 'desktop-entry': <'{SOURCE_DESKTOP_ENTRY}'>}}", "-1"],
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return 0
        if result.returncode != 0:
            return 0
        digits = "".join(char for char in result.stdout if char.isdigit())
        return int(digits) if digits else 1

    def wait(self, timeout: float):
        """The next answer to a notification, or None after timeout seconds."""
        try:
            return self.actions.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
