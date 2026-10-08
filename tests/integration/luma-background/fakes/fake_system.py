# SPDX-License-Identifier: MPL-2.0
"""Stand-ins for NetworkManager, logind and power-profiles-daemon on a private bus.

A container cannot suspend or change power profile, and the real logind's
signals cannot be forged, so luma-background is pointed at this bus with
DBUS_SYSTEM_BUS_ADDRESS. Each fake speaks the real interface names, paths and
signals the service subscribes to; the test drives them through
org.example.BgaTest.
"""

import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

ADDRESS = sys.argv[1]
connection = Gio.DBusConnection.new_for_address_sync(
    ADDRESS, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None, None)
state = {"nm": 20, "profile": "balanced"}

XML = """
<node>
  <interface name="org.freedesktop.NetworkManager">
    <property name="State" type="u" access="read"/>
    <signal name="StateChanged"><arg type="u"/></signal>
  </interface>
  <interface name="org.freedesktop.login1.Manager">
    <signal name="PrepareForSleep"><arg type="b"/></signal>
  </interface>
  <interface name="org.freedesktop.UPower.PowerProfiles">
    <property name="ActiveProfile" type="s" access="read"/>
  </interface>
  <interface name="org.example.BgaTest">
    <method name="SetNetwork"><arg type="u" direction="in"/></method>
    <method name="Sleep"><arg type="b" direction="in"/></method>
    <method name="SetProfile"><arg type="s" direction="in"/></method>
  </interface>
</node>
"""
node = Gio.DBusNodeInfo.new_for_xml(XML)
interfaces = {info.name: info for info in node.interfaces}


def get_property(_c, _sender, _path, interface, name):
    if interface == "org.freedesktop.NetworkManager" and name == "State":
        return GLib.Variant("u", state["nm"])
    if name == "ActiveProfile":
        return GLib.Variant("s", state["profile"])
    return None


def control(_c, _sender, _path, _interface, method, parameters, invocation):
    (value,) = parameters.unpack()
    if method == "SetNetwork":
        state["nm"] = value
        connection.emit_signal(None, "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                               "StateChanged", GLib.Variant("(u)", (value,)))
    elif method == "Sleep":
        connection.emit_signal(None, "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                               "PrepareForSleep", GLib.Variant("(b)", (value,)))
    elif method == "SetProfile":
        state["profile"] = value
        connection.emit_signal(None, "/org/freedesktop/UPower/PowerProfiles", "org.freedesktop.DBus.Properties",
                               "PropertiesChanged", GLib.Variant("(sa{sv}as)", (
                                   "org.freedesktop.UPower.PowerProfiles",
                                   {"ActiveProfile": GLib.Variant("s", value)}, [])))
    invocation.return_value(None)


connection.register_object("/org/freedesktop/NetworkManager", interfaces["org.freedesktop.NetworkManager"],
                           None, get_property, None)
connection.register_object("/org/freedesktop/login1", interfaces["org.freedesktop.login1.Manager"],
                           None, None, None)
connection.register_object("/org/freedesktop/UPower/PowerProfiles",
                           interfaces["org.freedesktop.UPower.PowerProfiles"], None, get_property, None)
connection.register_object("/org/example/BgaTest", interfaces["org.example.BgaTest"], control, None, None)
for name in ("org.freedesktop.NetworkManager", "org.freedesktop.login1",
             "org.freedesktop.UPower.PowerProfiles", "org.example.BgaTest"):
    Gio.bus_own_name_on_connection(connection, name, Gio.BusNameOwnerFlags.NONE, None, None)
GLib.MainLoop().run()
