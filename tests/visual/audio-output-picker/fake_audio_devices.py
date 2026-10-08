#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
# Render fixture: org.projectluma.AudioDevices1 with canned receivers, so the
# Shell's picker can be photographed in each state. Behaviour is tested
# against the real service elsewhere; this only feeds the presenter.
import sys
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

XML = open(sys.argv[1]).read()
TEST_XML = """<node><interface name="org.projectluma.RenderTest">
<method name="SetScenario"><arg type="s" direction="in"/></method></interface></node>"""

RECEIVERS = [
    dict(id="aa0000000001", name="Living Room", model="AudioAccessory5,1", kind="speaker", state="connected",
         remembered=True, requires_password=False, supported=True, node_name="luma_airplay.aa0000000001", error=""),
    dict(id="aa0000000002", name="Kitchen", model="AudioAccessory6,1", kind="speaker", state="available",
         remembered=False, requires_password=False, supported=True, node_name="luma_airplay.aa0000000002", error=""),
    dict(id="aa0000000003", name="Office Mac", model="Mac15,9", kind="computer", state="available",
         remembered=False, requires_password=True, supported=True, node_name="luma_airplay.aa0000000003", error=""),
    dict(id="aa0000000004", name="Bedroom TV", model="AppleTV14,1", kind="tv", state="available",
         remembered=False, requires_password=False, supported=True, node_name="luma_airplay.aa0000000004", error="denied"),
    dict(id="aa0000000005", name="Studio", model="AudioAccessory1,1", kind="speaker", state="unavailable",
         remembered=True, requires_password=False, supported=True, node_name="luma_airplay.aa0000000005", error=""),
]
state = {"scenario": "full", "browsing": False}


def variant(entry):
    return {k: GLib.Variant("b", v) if isinstance(v, bool) else GLib.Variant("s", v) for k, v in entry.items()}


def receivers():
    if state["scenario"] == "empty":
        return []
    return RECEIVERS


def on_call(connection, sender, path, interface, method, params, invocation):
    if method == "StartAirPlayBrowsing":
        state["browsing"] = True
        invocation.return_value(None)
    elif method == "StopAirPlayBrowsing":
        state["browsing"] = False
        invocation.return_value(None)
    elif method == "GetAirPlayReceivers":
        invocation.return_value(GLib.Variant("(aa{sv})", ([variant(r) for r in receivers()],)))
    elif method == "ConnectAirPlayReceiver":
        rid, password = params.unpack()
        if rid == "aa0000000003" and password != "luma":
            if password:
                invocation.return_dbus_error("org.projectluma.AudioDevices1.Error.PasswordIncorrect",
                                             "The password for “Office Mac” isn’t right.")
            else:
                invocation.return_dbus_error("org.projectluma.AudioDevices1.Error.PasswordRequired",
                                             "“Office Mac” needs a password.")
        else:
            invocation.return_value(None)
    elif method == "SetScenario":
        (state["scenario"],) = params.unpack()
        connection.emit_signal(None, "/org/projectluma/AudioDevices", "org.projectluma.AudioDevices1",
                               "AirPlayReceiversChanged", None)
        invocation.return_value(None)
    elif method in ("GetOutputDevices",):
        invocation.return_value(GLib.Variant("(aa{sv})", ([],)))
    else:
        invocation.return_value(None)


def get_property(connection, sender, path, interface, name):
    return {"Version": GLib.Variant("u", 1), "AirPlayAvailable": GLib.Variant("b", True),
            "AirPlayBrowsing": GLib.Variant("b", state["browsing"]),
            "HiddenOutputs": GLib.Variant("as", ["raop_sink.Neighbour-Mac.local.192.0.2.9.7000"])}.get(name)


bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
info = Gio.DBusNodeInfo.new_for_xml(XML).lookup_interface("org.projectluma.AudioDevices1")
bus.register_object("/org/projectluma/AudioDevices", info, on_call, get_property, None)
test = Gio.DBusNodeInfo.new_for_xml(TEST_XML).lookup_interface("org.projectluma.RenderTest")
bus.register_object("/org/projectluma/RenderTest", test, on_call, None, None)
Gio.bus_own_name_on_connection(bus, "org.projectluma.AudioDevices", Gio.BusNameOwnerFlags.NONE, None, None)
print("fake audio devices ready", flush=True)
GLib.MainLoop().run()
