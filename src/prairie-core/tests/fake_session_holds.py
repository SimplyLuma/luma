#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Stand-ins for systemd-logind's Inhibit and GNOME's session manager Inhibit, for clock_alarms_unit.py.

    fake_session_holds.py RECORD [--logind-after SECONDS]

Owns org.freedesktop.login1 and org.gnome.SessionManager on the session bus
(the test points SessionInhibitor's "system" bus there too) and appends one
JSON line per event to RECORD. Like logind, Inhibit hands back the write end
of a pipe and the hold ends when every copy of it is closed. Prints "ready"
once the session manager's name is owned.
"""

import json
import os
import sys

from gi.repository import Gio, GLib

record = open(sys.argv[1], "a", buffering=1)
logind_after = float(sys.argv[sys.argv.index("--logind-after") + 1]) if "--logind-after" in sys.argv else 0.0

LOGIND_XML = """
<node><interface name="org.freedesktop.login1.Manager">
  <method name="Inhibit">
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
    <arg type="h" direction="out"/>
  </method>
</interface></node>"""
SESSION_XML = """
<node><interface name="org.gnome.SessionManager">
  <method name="Inhibit">
    <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/><arg type="u" direction="in"/>
    <arg type="u" direction="out"/>
  </method>
  <method name="Uninhibit"><arg type="u" direction="in"/></method>
</interface></node>"""


def log(**event):
    record.write(json.dumps(event) + "\n")


def logind_call(_connection, _sender, _path, _interface, method, parameters, invocation):
    what, who, why, mode = parameters.unpack()
    read_end, write_end = os.pipe()

    def released(fd, _condition):
        log(event="logind-released", why=why)
        os.close(fd)
        return GLib.SOURCE_REMOVE

    GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, read_end, GLib.IOCondition.IN | GLib.IOCondition.HUP, released)
    fds = Gio.UnixFDList.new()
    fds.append(write_end)
    os.close(write_end)
    log(event="logind-inhibit", what=what, who=who, why=why, mode=mode)
    invocation.return_value_with_unix_fd_list(GLib.Variant("(h)", (0,)), fds)


cookies = iter(range(41, 1000))


def session_call(_connection, _sender, _path, _interface, method, parameters, invocation):
    if method == "Inhibit":
        app_id, _xid, reason, flags = parameters.unpack()
        cookie = next(cookies)
        log(event="gsm-inhibit", app_id=app_id, reason=reason, flags=flags, cookie=cookie)
        invocation.return_value(GLib.Variant("(u)", (cookie,)))
    else:
        (cookie,) = parameters.unpack()
        log(event="gsm-uninhibit", cookie=cookie)
        invocation.return_value(None)


connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
connection.register_object("/org/freedesktop/login1", Gio.DBusNodeInfo.new_for_xml(LOGIND_XML).interfaces[0],
                           logind_call, None, None)
connection.register_object("/org/gnome/SessionManager", Gio.DBusNodeInfo.new_for_xml(SESSION_XML).interfaces[0],
                           session_call, None, None)


def own(name):
    reply = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "RequestName", GLib.Variant("(su)", (name, 4)), GLib.VariantType("(u)"),
                                 Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    if reply != 1:
        sys.exit(f"could not own {name}: {reply}")
    return GLib.SOURCE_REMOVE


own("org.gnome.SessionManager")
if logind_after:
    GLib.timeout_add(int(logind_after * 1000), lambda: own("org.freedesktop.login1"))
else:
    own("org.freedesktop.login1")
print("ready", flush=True)
GLib.MainLoop().run()
