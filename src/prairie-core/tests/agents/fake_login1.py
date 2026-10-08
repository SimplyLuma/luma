#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Stand in for systemd-logind's PrepareForSleep on the system bus (as root, logind stopped).

    fake_login1.py sleep     emits PrepareForSleep(true), then exits
    fake_login1.py resume    emits PrepareForSleep(false), then exits
"""
import sys
import time

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

going_to_sleep = sys.argv[1] == "sleep"
connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
reply = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                             GLib.Variant("(su)", ("org.freedesktop.login1", 4)), GLib.VariantType("(u)"),
                             Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
if reply not in (1, 4):
    sys.exit(f"could not own org.freedesktop.login1 (reply {reply}); stop systemd-logind first")
connection.emit_signal(None, "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                       GLib.Variant("(b)", (going_to_sleep,)))
connection.flush_sync(None)
time.sleep(0.5)
