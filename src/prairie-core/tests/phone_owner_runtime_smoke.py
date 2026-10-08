"""Private session bus only: no modem, display, audio or phone service."""
import argparse
import time
from gi.repository import Gio, GLib
from prairie_apps.phone_backend import IncomingCallMonitor

parser = argparse.ArgumentParser()
parser.add_argument("--absence-seconds", type=float, default=2)
args = parser.parse_args()
address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
def connection():
    return Gio.DBusConnection.new_for_address_sync(address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
def spin(seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        while GLib.MainContext.default().iteration(False): pass
        time.sleep(.005)
def wait(predicate):
    until = time.monotonic() + 8
    while not predicate() and time.monotonic() < until: spin(.01)
    assert predicate()
def claim(c, own):
    c.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
        "RequestName" if own else "ReleaseName",
        GLib.Variant("(su)", (IncomingCallMonitor.BUS_NAME, 4)) if own else
        GLib.Variant("(s)", (IncomingCallMonitor.BUS_NAME,)),
        None, Gio.DBusCallFlags.NONE, 2000, None)
def result(uid):
    return GLib.Variant("(aa{sv})", ([{"uni": GLib.Variant("s", uid),
        "direction": GLib.Variant("s", "incoming"), "state": GLib.Variant("s", " INCOMING "),
        "number": GLib.Variant("s", "+12025550123")}],))
xml = """<node><interface name='net.catcrafts.IMS1'><method name='GetCalls'>
<arg type='aa{sv}' direction='out'/></method></interface></node>"""
added, incoming, owners, held = [], [], [], []
monitor = IncomingCallMonitor(on_added=lambda *v: added.append(v),
    on_incoming=lambda *v: incoming.append(v), on_owner=owners.append, connection=connection())
monitor.start(); monitor.start()
spin(args.absence_seconds)
assert not incoming and not monitor._pending and not monitor._retry
old, new = connection(), connection()
interface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
old.register_object(monitor.OBJECT_PATH, interface, lambda *v: held.append(v[-1]), None, None)
attempts = []
def respond(*v):
    attempts.append(1)
    if len(attempts) == 1:
        v[-1].return_dbus_error("net.catcrafts.IMS1.NotReady", "synthetic")
    else: v[-1].return_value(result("new"))
new.register_object(monitor.OBJECT_PATH, interface, respond, None, None)
claim(old, True); wait(lambda: bool(held))
claim(old, False); wait(lambda: monitor._owner is None)
claim(new, True); wait(lambda: bool(incoming))
assert incoming == [("new", "+12025550123")] and len(attempts) == 2
held[0].return_value(result("old")); spin(.1)
assert [item[0] for item in added] == ["new"]
new.emit_signal(None, monitor.OBJECT_PATH, monitor.INTERFACE, "CallAdded",
    GLib.Variant("(sa{sv})", ("new", {"direction": GLib.Variant("s", "incoming"),
                                   "number": GLib.Variant("s", "+12025550123")})))
spin(.1); assert len(incoming) == 1
claim(new, False); wait(lambda: monitor._owner is None)
monitor.stop()
owner_count = len(owners)
monitor.stop()
monitor._owner_changed(old.get_unique_name())
monitor._snapshot()
assert len(owners) == owner_count
assert not monitor._subscriptions and not monitor._watch and not monitor._pending and not monitor._retry
claim(old, True); spin(.1); assert len(incoming) == 1
claim(old, False)
old.close_sync(None); new.close_sync(None)
print("phone owner absence/reappearance/replacement/retry/dedup/stop PASS")
