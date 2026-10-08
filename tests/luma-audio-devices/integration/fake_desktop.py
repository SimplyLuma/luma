#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""Stand-ins for the parts of a GNOME session the service talks to.

* org.freedesktop.Notifications: records every Notify/CloseNotification in
  $FAKE_DESKTOP_LOG (one JSON object per line) and, on request, invokes an
  action the way a notification server does (ActionInvoked).
* org.gnome.ScreenSaver: GetActive/ActiveChanged, lockable on request.

Test control lives on org.projectluma.Test at /org/projectluma/Test:
InvokeAction(u id, s action), SetLocked(b).
"""

import json
import os
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

LOG = os.environ.get("FAKE_DESKTOP_LOG", "/tmp/fake-desktop.jsonl")

XML = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities"><arg type="as" direction="out"/></method>
    <method name="Notify">
      <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
      <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
      <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/>
    </method>
    <method name="CloseNotification"><arg type="u" direction="in"/></method>
    <method name="GetServerInformation">
      <arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/>
    </method>
    <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
    <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
  </interface>
  <interface name="org.gnome.ScreenSaver">
    <method name="GetActive"><arg type="b" direction="out"/></method>
    <signal name="ActiveChanged"><arg type="b"/></signal>
  </interface>
  <interface name="org.projectluma.Test">
    <method name="InvokeAction"><arg type="u" direction="in"/><arg type="s" direction="in"/></method>
    <method name="SetLocked"><arg type="b" direction="in"/></method>
  </interface>
</node>
"""

state = {"next": 1, "locked": False}


def log(event):
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def on_call(connection, sender, path, interface, method, params, invocation):
    if method == "GetCapabilities":
        invocation.return_value(GLib.Variant("(as)", (["actions", "body", "persistence"],)))
    elif method == "GetServerInformation":
        invocation.return_value(GLib.Variant("(ssss)", ("fake", "luma-tests", "1", "1.2")))
    elif method == "Notify":
        app, replaces, icon, summary, body, actions, hints, timeout = params.unpack()
        nid = replaces or state["next"]
        if not replaces:
            state["next"] += 1
        log({"event": "notify", "id": nid, "app": app, "replaces": replaces, "icon": icon, "summary": summary,
             "body": body, "actions": actions, "hints": {k: str(v) for k, v in hints.items()}})
        invocation.return_value(GLib.Variant("(u)", (nid,)))
    elif method == "CloseNotification":
        (nid,) = params.unpack()
        log({"event": "close", "id": nid})
        connection.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                               "NotificationClosed", GLib.Variant("(uu)", (nid, 3)))
        invocation.return_value(None)
    elif method == "GetActive":
        invocation.return_value(GLib.Variant("(b)", (state["locked"],)))
    elif method == "InvokeAction":
        nid, action = params.unpack()
        log({"event": "invoke", "id": nid, "action": action})
        connection.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                               "ActionInvoked", GLib.Variant("(us)", (nid, action)))
        invocation.return_value(None)
    elif method == "SetLocked":
        (locked,) = params.unpack()
        state["locked"] = locked
        connection.emit_signal(None, "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "ActiveChanged",
                               GLib.Variant("(b)", (locked,)))
        log({"event": "locked", "value": locked})
        invocation.return_value(None)


def main():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    for path, iface in (("/org/freedesktop/Notifications", "org.freedesktop.Notifications"),
                        ("/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
                        ("/org/projectluma/Test", "org.projectluma.Test")):
        bus.register_object(path, node.lookup_interface(iface), on_call, None, None)
    for name in ("org.freedesktop.Notifications", "org.gnome.ScreenSaver", "org.projectluma.Test"):
        Gio.bus_own_name_on_connection(bus, name, Gio.BusNameOwnerFlags.NONE, None, None)
    print("fake desktop ready", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    sys.exit(main())
