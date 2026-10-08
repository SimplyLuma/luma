#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A desktop portal backend for headless tests: Access, Background, Notification.

xdg-desktop-portal is the real frontend in these tests: it identifies the app
(sandboxed or not), keeps the background permission, writes and removes the
autostart entry and validates every notification. What it needs from a desktop
is a backend, and a headless session has none, so this is one. It records every
call as a JSON line and lets the test press a notification's button the way a
desktop would: an exported (app.) action is activated on the application's bus
name, anything else is sent back as ActionInvoked.

    PORTAL_TEST_LOG=calls.jsonl python3 portal_test_backend.py

Owns org.freedesktop.impl.portal.desktop.lumatest; the test points
xdg-desktop-portal at it with a .portal file and portals.conf (see
clock_alarm_session.py). Test control: org.projectluma.PortalTest.InvokeAction.

With PORTAL_TEST_GTK_NOTIFICATIONS=1 it also owns org.gtk.Notifications, the
service GNOME Shell provides and GNotification uses outside a sandbox, and
answers a button press there the way the shell does: by activating the
application's action over org.freedesktop.Application.
"""

from __future__ import annotations

import json
import os
import sys
import time

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.impl.portal.desktop.lumatest"
PATH = "/org/freedesktop/portal/desktop"
CONTROL_PATH = "/org/projectluma/PortalTest"

XML = """
<node>
  <interface name="org.freedesktop.impl.portal.Access">
    <method name="AccessDialog">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="s" name="subtitle" direction="in"/><arg type="s" name="body" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
  </interface>
  <interface name="org.freedesktop.impl.portal.Background">
    <method name="GetAppState"><arg type="a{sv}" name="apps" direction="out"/></method>
    <method name="NotifyBackground">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <signal name="RunningApplicationsChanged"/>
  </interface>
  <interface name="org.freedesktop.impl.portal.Notification">
    <method name="AddNotification">
      <arg type="s" name="app_id" direction="in"/><arg type="s" name="id" direction="in"/>
      <arg type="a{sv}" name="notification" direction="in"/>
    </method>
    <method name="RemoveNotification">
      <arg type="s" name="app_id" direction="in"/><arg type="s" name="id" direction="in"/>
    </method>
    <signal name="ActionInvoked">
      <arg type="s" name="app_id"/><arg type="s" name="id"/><arg type="s" name="action"/>
      <arg type="av" name="parameter"/>
    </signal>
    <property name="SupportedOptions" type="a{sv}" access="read"/>
    <property name="version" type="u" access="read"/>
  </interface>
</node>
"""

GTK_XML = """
<node>
  <interface name="org.gtk.Notifications">
    <method name="AddNotification">
      <arg type="s" name="app_id" direction="in"/><arg type="s" name="id" direction="in"/>
      <arg type="a{sv}" name="notification" direction="in"/>
    </method>
    <method name="RemoveNotification">
      <arg type="s" name="app_id" direction="in"/><arg type="s" name="id" direction="in"/>
    </method>
  </interface>
</node>
"""

CONTROL_XML = """
<node>
  <interface name="org.projectluma.PortalTest">
    <method name="InvokeAction">
      <arg type="s" name="app_id" direction="in"/><arg type="s" name="id" direction="in"/>
      <arg type="s" name="action" direction="in"/><arg type="av" name="parameter" direction="in"/>
    </method>
  </interface>
</node>
"""


def plain(value):
    """A GVariant as JSON-friendly Python, file descriptors and bytes summarised."""
    if isinstance(value, GLib.Variant):
        if value.get_type_string() == "h":
            return "<fd>"
        if value.get_type_string() == "ay":
            return f"<{len(value.get_data_as_bytes().get_data())} bytes>"
        return plain(value.unpack())
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


class Backend:
    def __init__(self, log_path: str) -> None:
        self.log = open(log_path, "a", encoding="utf-8", buffering=1)
        self.connection: Gio.DBusConnection | None = None
        self.via_gtk: set[tuple[str, str]] = set()

    def record(self, **entry) -> None:
        entry["time"] = time.time()
        self.log.write(json.dumps(plain(entry)) + "\n")

    def method(self, connection, sender, path, interface, name, parameters, invocation):
        args = parameters.unpack()
        if interface == "org.freedesktop.impl.portal.Access" and name == "AccessDialog":
            self.record(call="AccessDialog", app_id=args[1], title=args[3])
            invocation.return_value(GLib.Variant("(ua{sv})", (0, {})))
        elif name == "GetAppState":
            invocation.return_value(GLib.Variant("(a{sv})", ({},)))
        elif name == "NotifyBackground":
            self.record(call="NotifyBackground", app_id=args[1], name=args[2])
            invocation.return_value(GLib.Variant("(ua{sv})", (0, {"result": GLib.Variant("u", 1)})))
        elif name == "AddNotification":
            via = "gtk" if interface == "org.gtk.Notifications" else "portal"
            if via == "gtk":
                self.via_gtk.add((args[0], args[1]))
            self.record(call="AddNotification", via=via, app_id=args[0], id=args[1],
                        notification=parameters.get_child_value(2))
            invocation.return_value(None)
        elif name == "RemoveNotification":
            self.record(call="RemoveNotification", app_id=args[0], id=args[1])
            invocation.return_value(None)
        elif interface == "org.projectluma.PortalTest" and name == "InvokeAction":
            app_id, identifier, action = args[0], args[1], args[2]
            self.record(call="InvokeAction", app_id=app_id, id=identifier, action=action)
            if (app_id, identifier) in self.via_gtk or action.startswith("app."):
                # An exported action: the desktop activates it on the app's own
                # bus name, as GNOME Shell does for org.gtk.Notifications and as
                # the Notification portal specification has backends do.
                target = parameters.get_child_value(3)
                connection.call(
                    app_id, "/" + app_id.replace(".", "/"), "org.freedesktop.Application", "ActivateAction",
                    GLib.Variant.new_tuple(GLib.Variant("s", action.removeprefix("app.")), target,
                                           GLib.Variant("a{sv}", {})),
                    None, Gio.DBusCallFlags.NONE, -1, None, None,
                )
            else:
                # A non-exported action goes back through the portal frontend.
                # The signal carries the arguments exactly as the test gave them.
                connection.emit_signal(
                    None, PATH, "org.freedesktop.impl.portal.Notification", "ActionInvoked", parameters,
                )
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", name)

    def property(self, connection, sender, path, interface, name):
        if name == "version":
            return GLib.Variant("u", 2)
        if name == "SupportedOptions":
            return GLib.Variant("a{sv}", {"category": GLib.Variant("as", ["alarm.ringing"])})
        return None

    def acquired(self, connection, _name) -> None:
        self.connection = connection
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        for interface in info.interfaces:
            connection.register_object(PATH, interface, self.method, self.property, None)
        control = Gio.DBusNodeInfo.new_for_xml(CONTROL_XML)
        connection.register_object(CONTROL_PATH, control.interfaces[0], self.method, None, None)
        if os.environ.get("PORTAL_TEST_GTK_NOTIFICATIONS") == "1":
            gtk = Gio.DBusNodeInfo.new_for_xml(GTK_XML)
            connection.register_object("/org/gtk/Notifications", gtk.interfaces[0], self.method, None, None)
            Gio.bus_own_name_on_connection(connection, "org.gtk.Notifications", Gio.BusNameOwnerFlags.NONE, None, None)


def main() -> int:
    backend = Backend(os.environ.get("PORTAL_TEST_LOG", "portal-test-calls.jsonl"))
    loop = GLib.MainLoop()
    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     backend.acquired, lambda *_a: print("portal-test-backend: name acquired", flush=True),
                     lambda *_a: (print("portal-test-backend: name lost", file=sys.stderr), loop.quit()))
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
