# SPDX-License-Identifier: MPL-2.0
"""A notification server that records every notification and answers prompts.

The answer to an actionable notification is read from
$XDG_RUNTIME_DIR/bga-control/notification-answer: an action id to invoke, or
"dismiss" to close it unanswered, or "wait" to leave it open.
"""

import json
import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

CONTROL = Path(os.environ["XDG_RUNTIME_DIR"]) / "bga-control"
LOG = CONTROL / "notifications.jsonl"
XML = """
<node><interface name="org.freedesktop.Notifications">
  <method name="Notify">
    <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
    <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/>
  </method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method>
  <method name="GetCapabilities"><arg type="as" direction="out"/></method>
  <method name="GetServerInformation">
    <arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/>
  </method>
  <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
  <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
</interface></node>
"""
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
counter = [0]


def emit(signal, parameters):
    connection.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                           signal, parameters)


def handle(_c, sender, _path, _interface, method, parameters, invocation):
    if method == "Notify":
        app, _replaces, icon, summary, body, actions, hints, _timeout = parameters.unpack()
        counter[0] += 1
        identifier = counter[0]
        CONTROL.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as stream:
            stream.write(json.dumps({"id": identifier, "app": app, "icon": icon, "summary": summary,
                                     "body": body, "actions": actions,
                                     "hints": {k: str(v) for k, v in hints.items()}}) + "\n")
        invocation.return_value(GLib.Variant("(u)", (identifier,)))
        if actions:
            answer_file = CONTROL / "notification-answer"
            answer = answer_file.read_text().strip() if answer_file.exists() else "wait"

            def respond():
                if answer == "dismiss":
                    emit("NotificationClosed", GLib.Variant("(uu)", (identifier, 2)))
                elif answer in actions[::2]:
                    emit("ActionInvoked", GLib.Variant("(us)", (identifier, answer)))
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(500, respond)
    elif method == "CloseNotification":
        (identifier,) = parameters.unpack()
        invocation.return_value(None)
        emit("NotificationClosed", GLib.Variant("(uu)", (identifier, 3)))
    elif method == "GetCapabilities":
        invocation.return_value(GLib.Variant("(as)", (["actions", "body", "persistence"],)))
    else:
        invocation.return_value(GLib.Variant("(ssss)", ("bga-fake", "test", "1", "1.2")))


node = Gio.DBusNodeInfo.new_for_xml(XML)
connection.register_object("/org/freedesktop/Notifications", node.interfaces[0], handle, None, None)
Gio.bus_own_name_on_connection(connection, "org.freedesktop.Notifications", Gio.BusNameOwnerFlags.NONE, None, None)
GLib.MainLoop().run()
