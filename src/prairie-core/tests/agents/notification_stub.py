#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A session notification server and screen lock for agent tests.

Owns org.freedesktop.Notifications (records every Notify/CloseNotification as
a JSON line in $LUMA_TEST_NOTIFICATIONS) and org.gnome.ScreenSaver (GetActive,
SetActive, ActiveChanged), and exports org.projectluma.Test.Notifications on
/org/projectluma/Test with Invoke(u id, s action) to press a button the way the
shell does, and Lock(b) to lock or unlock. It also answers org.gtk.Notifications
(recorded as gtk-add and gtk-remove), which is how GNotification reaches GNOME
Shell.
"""
import json
import os
import sys

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

RECORD = os.environ.get("LUMA_TEST_NOTIFICATIONS", "/tmp/notifications.jsonl")
XML = """<node>
<interface name="org.freedesktop.Notifications">
  <method name="GetCapabilities"><arg type="as" direction="out"/></method>
  <method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
    <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/></method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method>
  <method name="GetServerInformation"><arg type="s" direction="out"/><arg type="s" direction="out"/>
    <arg type="s" direction="out"/><arg type="s" direction="out"/></method>
  <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
  <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
</interface>
<interface name="org.gnome.ScreenSaver">
  <method name="GetActive"><arg type="b" direction="out"/></method>
  <method name="SetActive"><arg type="b" direction="in"/></method>
  <signal name="ActiveChanged"><arg type="b"/></signal>
</interface>
<interface name="org.gtk.Notifications">
  <method name="AddNotification"><arg type="s" direction="in"/><arg type="s" direction="in"/>
    <arg type="a{sv}" direction="in"/></method>
  <method name="RemoveNotification"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
</interface>
<interface name="org.projectluma.Test.Notifications">
  <method name="Invoke"><arg type="u" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Lock"><arg type="b" direction="in"/></method>
</interface>
</node>"""

state = {"next": 1, "locked": False, "senders": {}}


def record(entry):
    with open(RECORD, "a") as stream:
        stream.write(json.dumps(entry) + "\n")


def plain(value):
    if isinstance(value, GLib.Variant):
        return value.unpack()
    return value


def call(connection, sender, path, interface, method, parameters, invocation):
    args = parameters.unpack()
    if method == "GetCapabilities":
        invocation.return_value(GLib.Variant("(as)", (["actions", "body", "persistence", "sound"],)))
    elif method == "GetServerInformation":
        invocation.return_value(GLib.Variant("(ssss)", ("luma-test", "Project Luma", "1", "1.2")))
    elif method == "Notify":
        app, replaces, icon, summary, body, actions, hints, timeout = args
        identifier = replaces or state["next"]
        if not replaces:
            state["next"] += 1
        state["senders"][identifier] = sender
        record({"event": "notify", "id": identifier, "replaces": replaces, "app": app, "icon": icon,
                "summary": summary, "body": body, "actions": actions,
                "hints": {key: plain(value) for key, value in hints.items()}, "sender": sender,
                "locked": state["locked"]})
        invocation.return_value(GLib.Variant("(u)", (identifier,)))
    elif method == "CloseNotification":
        record({"event": "close", "id": args[0], "sender": sender})
        connection.emit_signal(state["senders"].get(args[0]), "/org/freedesktop/Notifications",
                               "org.freedesktop.Notifications", "NotificationClosed", GLib.Variant("(uu)", (args[0], 3)))
        invocation.return_value(None)
    elif method == "AddNotification":
        app, identifier, notification = args
        record({"event": "gtk-add", "app": app, "id": identifier,
                "notification": {key: plain(value) for key, value in notification.items()},
                "sender": sender, "locked": state["locked"]})
        invocation.return_value(None)
    elif method == "RemoveNotification":
        record({"event": "gtk-remove", "app": args[0], "id": args[1], "sender": sender})
        invocation.return_value(None)
    elif method == "GetActive":
        invocation.return_value(GLib.Variant("(b)", (state["locked"],)))
    elif method in {"SetActive", "Lock"}:
        state["locked"] = bool(args[0])
        connection.emit_signal(None, "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "ActiveChanged",
                               GLib.Variant("(b)", (state["locked"],)))
        record({"event": "lock", "locked": state["locked"]})
        invocation.return_value(None)
    elif method == "Invoke":
        identifier, action = args
        record({"event": "invoke", "id": identifier, "action": action})
        connection.emit_signal(state["senders"].get(identifier), "/org/freedesktop/Notifications",
                               "org.freedesktop.Notifications", "ActionInvoked",
                               GLib.Variant("(us)", (identifier, action)))
        invocation.return_value(None)


def main():
    loop = GLib.MainLoop()
    node = Gio.DBusNodeInfo.new_for_xml(XML)

    def acquired(connection, name):
        pass

    def bus(connection, name):
        connection.register_object("/org/freedesktop/Notifications", node.interfaces[0], call, None, None)
        connection.register_object("/org/gnome/ScreenSaver", node.interfaces[1], call, None, None)
        connection.register_object("/org/gtk/Notifications", node.interfaces[2], call, None, None)
        connection.register_object("/org/projectluma/Test", node.interfaces[3], call, None, None)

    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus(connection, None)
    owners = [Gio.bus_own_name_on_connection(connection, name, Gio.BusNameOwnerFlags.NONE, acquired, None)
              for name in ("org.freedesktop.Notifications", "org.gtk.Notifications", "org.gnome.ScreenSaver",
                           "org.projectluma.Test")]
    print("notification stub ready", flush=True)
    loop.run()
    del owners


if __name__ == "__main__":
    sys.exit(main())
