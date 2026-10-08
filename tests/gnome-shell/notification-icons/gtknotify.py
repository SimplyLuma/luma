#!/usr/bin/env python3
"""org.gtk.Notifications.AddNotification, as the portal forwards a Flatpak app's."""
import sys, time
from gi.repository import Gio, GLib
app, nid, title, body, icon = sys.argv[1:6]
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
n = {"title": GLib.Variant("s", title), "body": GLib.Variant("s", body)}
if icon:
    n["icon"] = Gio.ThemedIcon.new(icon).serialize()
bus.call_sync("org.gtk.Notifications", "/org/gtk/Notifications", "org.gtk.Notifications",
              "AddNotification", GLib.Variant("(ssa{sv})", (app, nid, n)), None, 0, 5000, None)
print(1, flush=True)
time.sleep(600)
