#!/usr/bin/python3
"""Ask geoclue for an exact location and report what it serves, without the location.

  luma-fold-gnss-geoclue.py <desktop id> <timeout s> <hold s>

Starts a GeoClue2 client at accuracy level EXACT (8, the level that enables the
NMEA source), waits up to <timeout> s for the first LocationUpdated, keeps the
client for <hold> s more, and prints one line per milestone: time to the first
location, its accuracy, the number of updates and the best accuracy. Latitude
and longitude are never read.
"""
import os
import sys
import time

from gi.repository import Gio, GLib

DEST = "org.freedesktop.GeoClue2"
desktop_id, timeout, hold = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def call(path, iface, method, args=None, rtype=None):
    return bus.call_sync(DEST, path, iface, method, args, rtype,
                         Gio.DBusCallFlags.NONE, 30000, None)


def set_prop(path, name, value):
    call(path, "org.freedesktop.DBus.Properties", "Set",
         GLib.Variant("(ssv)", ("org.freedesktop.GeoClue2.Client", name, value)))


def accuracy(loc_path):
    v = call(loc_path, "org.freedesktop.DBus.Properties", "Get",
             GLib.Variant("(ss)", ("org.freedesktop.GeoClue2.Location", "Accuracy")))
    return v.unpack()[0]


t0 = time.monotonic()
print(f"geoclue: asking as uid {os.getuid()} for app id {desktop_id}", flush=True)
try:
    client = call("/org/freedesktop/GeoClue2/Manager", "org.freedesktop.GeoClue2.Manager",
                  "GetClient", None, GLib.VariantType("(o)")).unpack()[0]
    set_prop(client, "DesktopId", GLib.Variant("s", desktop_id))
    set_prop(client, "RequestedAccuracyLevel", GLib.Variant("u", 8))
except GLib.Error as e:
    print(f"geoclue: GetClient/setup failed after {time.monotonic() - t0:.1f} s: {e.message}", flush=True)
    sys.exit(1)
print(f"geoclue: client {client} after {time.monotonic() - t0:.1f} s", flush=True)
state = {"n": 0, "first": None, "best": None}
loop = GLib.MainLoop()


def on_signal(conn, sender, path, iface, signal, params):
    if signal != "LocationUpdated":
        return
    acc = accuracy(params.unpack()[1])
    state["n"] += 1
    if state["first"] is None:
        state["first"] = time.monotonic() - t0
        print(f"geoclue: first location after {state['first']:.1f} s, accuracy {acc:.1f} m", flush=True)
        GLib.timeout_add_seconds(hold, loop.quit)
    if state["best"] is None or acc < state["best"]:
        state["best"] = acc


bus.signal_subscribe(DEST, "org.freedesktop.GeoClue2.Client", "LocationUpdated", client,
                     None, Gio.DBusSignalFlags.NONE, on_signal)
try:
    call(client, "org.freedesktop.GeoClue2.Client", "Start")
except GLib.Error as e:
    print(f"geoclue: Start failed: {e.message}", flush=True)
    sys.exit(1)
active = call(client, "org.freedesktop.DBus.Properties", "Get",
              GLib.Variant("(ss)", ("org.freedesktop.GeoClue2.Client", "Active"))).unpack()[0]
print(f"geoclue: client started (EXACT), active {active}", flush=True)
GLib.timeout_add_seconds(timeout, lambda: (state["first"] is None and loop.quit()) or False)
loop.run()
try:
    call(client, "org.freedesktop.GeoClue2.Client", "Stop")
except GLib.Error:
    pass
if state["first"] is None:
    print(f"geoclue: no location within {timeout} s", flush=True)
    sys.exit(2)
print(f"geoclue: {state['n']} updates, best accuracy {state['best']:.1f} m", flush=True)
