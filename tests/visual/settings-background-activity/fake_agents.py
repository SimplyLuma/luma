# SPDX-License-Identifier: MPL-2.0
"""org.projectluma.Background1 with a realistic set of agents, for rendering Settings."""
import time
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

MB = 1000 * 1000
now = int(time.time() * 1e6)
AGENTS = [
    dict(app="org.projectluma.Calendar", name="Calendar", category="calendar", essential=True, allowed=True, state="running",
         explanation="Get event reminders when Calendar is closed", consequence="You won’t get event reminders while Calendar is closed",
         memory=21_400_000, cpu=0.1, last=now - 90_000_000, wake=["login", "network", "resume", "schedule"]),
    dict(app="org.projectluma.Clock", name="Clock", category="alarms", essential=True, allowed=True, state="idle",
         explanation="Alarms and timers go off when Clock is closed", consequence="Alarms and timers won’t go off while Clock is closed",
         memory=0, cpu=0.0, last=now - 3 * 3600 * 1_000_000, wake=["schedule"]),
    dict(app="org.projectluma.Messages", name="Messages", category="communication", essential=True, allowed=True, state="running",
         explanation="Get messages and calls when Messages is closed", consequence="You won’t get messages or calls while Messages is closed",
         memory=18_200_000, cpu=0.3, last=now - 40_000_000, wake=["login", "network", "resume"]),
    dict(app="org.projectluma.Phone", name="Phone", category="communication", essential=True, allowed=False, state="off",
         explanation="Get messages and calls when Phone is closed", consequence="You won’t get messages or calls while Phone is closed",
         memory=0, cpu=0.0, last=now - 26 * 3600 * 1_000_000, wake=["login", "resume"]),
    dict(app="org.projectluma.Weather", name="Weather", category="widget-data", essential=False, allowed=True, state="paused",
         explanation="Keep Weather’s live information current when it is closed", consequence="Weather’s live information won’t update while it is closed",
         memory=9_800_000, cpu=0.0, last=now - 12 * 60 * 1_000_000, wake=["network", "schedule"]),
]
XML = """<node><interface name="org.projectluma.Background1">
<method name="ListAgents"><arg type="aa{sv}" direction="out"/></method>
<method name="SetAllowed"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
<method name="StopNow"><arg type="s" direction="in"/></method>
<signal name="AgentsChanged"/><signal name="AgentChanged"><arg type="s"/><arg type="a{sv}"/></signal>
</interface></node>"""
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

def variant(a):
    return {"app-id": GLib.Variant("s", a["app"]), "name": GLib.Variant("s", a["name"]),
            "category": GLib.Variant("s", a["category"]), "essential": GLib.Variant("b", a["essential"]),
            "allowed": GLib.Variant("b", a["allowed"]), "state": GLib.Variant("s", a["state"]),
            "explanation": GLib.Variant("s", a["explanation"]), "consequence": GLib.Variant("s", a["consequence"]),
            "memory": GLib.Variant("t", a["memory"]), "memory-max": GLib.Variant("t", 134217728),
            "cpu-percent": GLib.Variant("d", a["cpu"]), "last-run": GLib.Variant("x", a["last"]),
            "wake": GLib.Variant("as", a["wake"]), "decision": GLib.Variant("s", "unset")}

def handle(c, sender, path, iface, method, params, inv):
    if method == "ListAgents":
        inv.return_value(GLib.Variant("(aa{sv})", ([variant(a) for a in AGENTS],)))
        return
    app = params.unpack()[0]
    for a in AGENTS:
        if a["app"] == app:
            if method == "SetAllowed":
                a["allowed"] = params.unpack()[1]
                a["state"] = "running" if a["allowed"] else "off"
            else:
                a["state"] = "idle"
            print(method, params.unpack(), flush=True)
            inv.return_value(None)
            bus.emit_signal(None, "/org/projectluma/Background1", "org.projectluma.Background1", "AgentChanged",
                            GLib.Variant("(sa{sv})", (app, variant(a))))
            return
    inv.return_dbus_error("org.projectluma.Background1.Error.NotFound", app)

bus.register_object("/org/projectluma/Background1", Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], handle, None, None)
Gio.bus_own_name_on_connection(bus, "org.projectluma.Background1", Gio.BusNameOwnerFlags.NONE, None, None)
GLib.MainLoop().run()
