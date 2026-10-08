# SPDX-License-Identifier: MPL-2.0
"""Flatpak apps reach the same policy through the standard Background portal.

Two halves, shaped by how xdg-desktop-portal 1.22 actually works:

- The portal frontend decides RequestBackground by itself from the `background`
  permission table (host apps are always allowed; an unset entry becomes
  `yes` silently) and writes the autostart entry itself. So Luma keeps that
  table in step with its own decisions, in both directions, and treats the
  frontend's first `yes` as the app's first request.
- The frontend asks the Background *backend* about sandboxed apps it finds
  running with no windows (NotifyBackground) and for window state
  (GetAppState). That backend is this module, preferred for the Background
  interface only.

Behaviour of the frontend was read from xdg-desktop-portal 1.22.1
src/background.c and of the GNOME backend from xdg-desktop-portal-gnome 50.0
src/background.c, the versions Fedora 44 ships.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import ids, journal  # noqa: E402

PERMISSION_STORE = "org.freedesktop.impl.portal.PermissionStore"
PERMISSION_STORE_PATH = "/org/freedesktop/impl/portal/PermissionStore"
TABLE = "background"
ENTRY = "background"

BACKEND_NAME = "org.freedesktop.impl.portal.desktop.luma.background"
BACKEND_PATH = "/org/freedesktop/portal/desktop"
GNOME_BACKEND = "org.freedesktop.impl.portal.desktop.gnome"

RESULT_FORBID = 0
RESULT_ALLOW = 1
RESULT_ALLOW_INSTANCE = 2

BACKEND_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.Background">
    <method name="GetAppState">
      <arg type="a{sv}" name="apps" direction="out"/>
    </method>
    <method name="NotifyBackground">
      <arg type="o" name="handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="EnableAutostart">
      <arg type="s" name="app_id" direction="in"/>
      <arg type="b" name="enable" direction="in"/>
      <arg type="as" name="commandline" direction="in"/>
      <arg type="u" name="flags" direction="in"/>
      <arg type="b" name="result" direction="out"/>
    </method>
    <signal name="RunningApplicationsChanged"/>
  </interface>
</node>
"""

REQUEST_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.Request">
    <method name="Close"/>
  </interface>
</node>
"""


def permission_values(permissions: dict[str, list[str]]) -> dict[str, str]:
    """The frontend reads exactly one of yes, no or ask; anything else is unset."""

    values = {}
    for app, value in permissions.items():
        if ids.is_app_id(app) and isinstance(value, list) and len(value) == 1 \
                and value[0] in {"yes", "no", "ask"}:
            values[app] = value[0]
    return values


class PermissionStoreMirror:
    def __init__(self, connection: Gio.DBusConnection,
                 changed: Callable[[str, str, str], None]) -> None:
        self.connection = connection
        self._changed = changed
        self.snapshot: dict[str, str] = {}
        self._writing: dict[str, str] = {}
        connection.signal_subscribe(PERMISSION_STORE, PERMISSION_STORE, "Changed",
                                    PERMISSION_STORE_PATH, TABLE, Gio.DBusSignalFlags.NONE,
                                    self._on_changed)

    def load(self) -> dict[str, str]:
        try:
            result = self.connection.call_sync(
                PERMISSION_STORE, PERMISSION_STORE_PATH, PERMISSION_STORE, "Lookup",
                GLib.Variant("(ss)", (TABLE, ENTRY)), GLib.VariantType.new("(a{sas}v)"),
                Gio.DBusCallFlags.NONE, 10_000, None)
            self.snapshot = permission_values(result.unpack()[0])
        except GLib.Error as error:
            if "NotFound" not in (Gio.DBusError.get_remote_error(error) or ""):
                journal.send(f"Could not read the portal permission table: {error.message}",
                             priority="notice", event="permission-store-unavailable")
            self.snapshot = {}
        return dict(self.snapshot)

    def set(self, app: str, allowed: bool) -> None:
        value = "yes" if allowed else "no"
        if self.snapshot.get(app) == value:
            return
        self._writing[app] = value

        def done(connection, result) -> None:
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                self._writing.pop(app, None)
                journal.send(f"Could not update the portal permission: {error.message}",
                             priority="warning", app_id=app, event="permission-store-failed")

        self.connection.call(PERMISSION_STORE, PERMISSION_STORE_PATH, PERMISSION_STORE,
                             "SetPermission", GLib.Variant("(sbssas)", (TABLE, True, ENTRY, app, [value])),
                             None, Gio.DBusCallFlags.NONE, 10_000, None, done)

    def _on_changed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        table, entry, deleted, _data, permissions = parameters.unpack()
        if table != TABLE or entry != ENTRY:
            return
        current = {} if deleted else permission_values(permissions)
        previous, self.snapshot = self.snapshot, current
        for app in sorted(set(previous) | set(current)):
            before, after = previous.get(app, ""), current.get(app, "")
            if before == after:
                continue
            if self._writing.get(app) == after:
                del self._writing[app]
                continue
            self._changed(app, after, before)


class BackgroundPortal:
    """org.freedesktop.impl.portal.Background, answering from Luma's policy."""

    def __init__(self, connection: Gio.DBusConnection,
                 notify: Callable[[str, Callable[[int], None]], None],
                 cancel: Callable[[str], None]) -> None:
        self.connection = connection
        self._notify = notify
        self._cancel = cancel
        self._requests: dict[str, tuple[int, str]] = {}
        node = Gio.DBusNodeInfo.new_for_xml(BACKEND_XML)
        self._request_info = Gio.DBusNodeInfo.new_for_xml(REQUEST_XML).interfaces[0]
        connection.register_object(BACKEND_PATH, node.interfaces[0], self._call, None, None)
        connection.signal_subscribe(GNOME_BACKEND, "org.freedesktop.impl.portal.Background",
                                    "RunningApplicationsChanged", BACKEND_PATH, None,
                                    Gio.DBusSignalFlags.NONE, self._relay_running_changed)

    def _relay_running_changed(self, *_args) -> None:
        self.connection.emit_signal(None, BACKEND_PATH, "org.freedesktop.impl.portal.Background",
                                    "RunningApplicationsChanged", None)

    def _call(self, _connection, sender, _path, _interface, method, parameters, invocation) -> None:
        if method == "GetAppState":
            self._get_app_state(invocation)
        elif method == "NotifyBackground":
            handle, app, name = parameters.unpack()
            self._notify_background(sender, handle, app, name, invocation)
        elif method == "EnableAutostart":
            # xdg-desktop-portal 1.18 and later never call this; autostart is
            # written by the frontend and picked up from the autostart folder.
            invocation.return_value(GLib.Variant("(b)", (False,)))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _get_app_state(self, invocation) -> None:
        """Window state comes from the Shell, which only answers the GNOME backend.

        GNOME Shell's Introspect interface is allow-listed to the GTK and GNOME
        portal backends, so the question is passed to the GNOME backend rather
        than asked of the Shell directly. If nobody can answer, the frontend
        is told so and leaves every app alone -- far better than an empty
        answer, which it would read as "everything is in the background".
        """

        def answered(connection, result) -> None:
            try:
                value = connection.call_finish(result)
            except GLib.Error as error:
                invocation.return_dbus_error("org.freedesktop.portal.Error.Failed",
                                             f"window state is unavailable: {error.message}")
                return
            invocation.return_value(value)

        self.connection.call(GNOME_BACKEND, BACKEND_PATH, "org.freedesktop.impl.portal.Background",
                             "GetAppState", None, GLib.VariantType.new("(a{sv})"),
                             Gio.DBusCallFlags.NONE, 10_000, None, answered)

    def _notify_background(self, sender: str, handle: str, app: str, name: str, invocation) -> None:
        if not ids.is_app_id(app):
            invocation.return_value(GLib.Variant("(ua{sv})", (2, {"result": GLib.Variant("u", RESULT_ALLOW_INSTANCE)})))
            return
        registration = 0
        try:
            registration = self.connection.register_object(
                handle, self._request_info,
                lambda *args: self._close_request(handle, args[-1]), None, None)
        except GLib.Error:
            registration = 0
        self._requests[handle] = (registration, app)

        def reply(result: int) -> None:
            entry = self._requests.pop(handle, None)
            if entry is None:
                return
            if entry[0]:
                self.connection.unregister_object(entry[0])
            invocation.return_value(GLib.Variant("(ua{sv})", (0, {"result": GLib.Variant("u", result)})))

        journal.send(f"{name or app} is running with no windows", app_id=app,
                     event="portal-notify-background")
        self._notify(app, reply)

    def _close_request(self, handle: str, invocation) -> None:
        entry = self._requests.get(handle)
        if entry is not None:
            self._cancel(entry[1])
        invocation.return_value(None)
