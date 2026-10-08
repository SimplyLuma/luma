# SPDX-License-Identifier: MPL-2.0
"""org.projectluma.Background1 with fixed agents, for rendering the dock menu."""
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

AGENTS = {
    "org.projectluma.Messages": dict(name="Messages", category="communication", essential=True, allowed=True,
        explanation="Get messages and calls when Messages is closed",
        consequence="You won’t get messages or calls while Messages is closed"),
    "org.projectluma.Weather": dict(name="Weather", category="widget-data", essential=False, allowed=False,
        explanation="Keep Weather’s live information current when it is closed",
        consequence="Weather’s live information won’t update while it is closed"),
}
XML = """<node><interface name="org.projectluma.Background1">
<method name="GetAgent"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
<method name="SetAllowed"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
<signal name="AgentChanged"><arg type="s"/><arg type="a{sv}"/></signal>
</interface></node>"""
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
calls = []

def describe(app):
    a = AGENTS[app]
    return {"app-id": GLib.Variant("s", app), "name": GLib.Variant("s", a["name"]),
            "category": GLib.Variant("s", a["category"]), "allowed": GLib.Variant("b", a["allowed"]),
            "essential": GLib.Variant("b", a["essential"]), "explanation": GLib.Variant("s", a["explanation"]),
            "consequence": GLib.Variant("s", a["consequence"]), "state": GLib.Variant("s", "running" if a["allowed"] else "off")}

def handle(c, sender, path, iface, method, params, inv):
    if method == "GetAgent":
        (app,) = params.unpack()
        if app not in AGENTS:
            inv.return_dbus_error("org.projectluma.Background1.Error.NotFound", app)
            return
        inv.return_value(GLib.Variant("(a{sv})", (describe(app),)))
    else:
        app, allowed = params.unpack()
        calls.append((app, allowed))
        print("SetAllowed", app, allowed, flush=True)
        AGENTS[app]["allowed"] = allowed
        inv.return_value(None)
        bus.emit_signal(None, "/org/projectluma/Background1", "org.projectluma.Background1", "AgentChanged",
                        GLib.Variant("(sa{sv})", (app, describe(app))))

bus.register_object("/org/projectluma/Background1", Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], handle, None, None)
Gio.bus_own_name_on_connection(bus, "org.projectluma.Background1", Gio.BusNameOwnerFlags.NONE, None, None)
GLib.MainLoop().run()
