#!/usr/bin/python3
"""Installed service proof: a caller disappearing closes its read-only preview."""
import os
import sys
import time
import gi
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
name = 'org.gnome.NautilusPreviewer'
path = '/org/gnome/NautilusPreviewer'
def visible():
    result = bus.call_sync(name, path, 'org.freedesktop.DBus.Properties', 'Get',
                          GLib.Variant('(ss)', ('org.gnome.NautilusPreviewer2', 'Visible')),
                          None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    return result.unpack() if isinstance(result, GLib.Variant) else result
caller = Gio.DBusConnection.new_for_address_sync(
    os.environ['DBUS_SESSION_BUS_ADDRESS'],
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None, None)
caller.call_sync(name, path, 'org.gnome.NautilusPreviewer2', 'ShowFile',
                 GLib.Variant('(ssbs)', (Gio.File.new_for_path(sys.argv[1]).get_uri(), '', False, '')),
                 None, Gio.DBusCallFlags.NONE, 5000, None)
assert visible(), 'Preview was not created for active caller'
caller.close_sync(None)
end = time.monotonic() + 3
while visible() and time.monotonic() < end:
    time.sleep(.05)
assert not visible(), 'Preview outlived its disconnected caller'
print('PASS active caller opens preview; disconnected caller closes preview')
