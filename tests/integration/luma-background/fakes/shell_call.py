# SPDX-License-Identifier: MPL-2.0
"""Run as /usr/bin/gnome-shell (a copy of the Python interpreter) owning org.gnome.Shell.

It stands in for the dock menu and for the Shell reading Live Extensions, so the
real identity checks in luma-background and the Semantic Broker are exercised
rather than bypassed. Usage: gnome-shell shell_call.py METHOD [ARGS...]
"""

import json
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
loop = GLib.MainLoop()
result = {}


def acquired(_c, _name):
    method, arguments = sys.argv[1], sys.argv[2:]
    try:
        if method == "ListLiveExtensions":
            reply = connection.call_sync("org.projectluma.SemanticBroker1", "/org/projectluma/SemanticBroker1",
                                         "org.projectluma.SemanticBroker1", "ListLiveExtensions", None,
                                         GLib.VariantType.new("(aa{sv})"), Gio.DBusCallFlags.NONE, 10000, None)
            result["value"] = reply.unpack()[0]
        elif method == "SetAllowed":
            connection.call_sync("org.projectluma.Background1", "/org/projectluma/Background1",
                                 "org.projectluma.Background1", "SetAllowed",
                                 GLib.Variant("(sb)", (arguments[0], arguments[1] == "true")), None,
                                 Gio.DBusCallFlags.NONE, 30000, None)
            result["value"] = True
        elif method in {"StopNow"}:
            connection.call_sync("org.projectluma.Background1", "/org/projectluma/Background1",
                                 "org.projectluma.Background1", method, GLib.Variant("(s)", (arguments[0],)), None,
                                 Gio.DBusCallFlags.NONE, 30000, None)
            result["value"] = True
        elif method == "Wake":
            connection.call_sync("org.projectluma.Background1", "/org/projectluma/Background1",
                                 "org.projectluma.Background1", "Wake", GLib.Variant("(ss)", tuple(arguments)), None,
                                 Gio.DBusCallFlags.NONE, 30000, None)
            result["value"] = True
        elif method in {"GetLoginItem", "SetLoginItem"}:
            parameters = (GLib.Variant("(s)", (arguments[0],)) if method == "GetLoginItem"
                          else GLib.Variant("(sb)", (arguments[0], arguments[1] == "true")))
            reply = connection.call_sync("org.projectluma.Background1", "/org/projectluma/Background1",
                                         "org.projectluma.Background1", method, parameters,
                                         GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 30000, None)
            result["value"] = reply.unpack()[0]
    except GLib.Error as error:
        result["error"] = error.message
    loop.quit()


Gio.bus_own_name_on_connection(connection, "org.gnome.Shell", Gio.BusNameOwnerFlags.NONE, acquired, None)
GLib.timeout_add_seconds(40, loop.quit)
loop.run()
print(json.dumps(result, default=str))
sys.exit(1 if "error" in result else 0)
