#!/usr/bin/python3
"""Stand-ins for Filer (FileManager1.ShowItems) and Settings (launch-panel)
that record what Beam asks them to open."""
import sys
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

ACTIONS = """<node><interface name="org.gtk.Actions">
<method name="Activate"><arg type="s" direction="in"/><arg type="av" direction="in"/><arg type="a{sv}" direction="in"/></method>
</interface></node>"""
XML = """<node><interface name="org.freedesktop.FileManager1">
<method name="ShowItems"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
<method name="ShowFolders"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>"""
out = sys.argv[1]


def call(_c, _s, _p, _i, method, params, invocation):
    with open(out, "a") as handle:
        values = params.unpack()
        handle.write(f"{method} {values[0]} {values[1] if method == 'Activate' else ''}\n")
    invocation.return_value(None)


def acquired(connection, _name):
    connection.register_object("/org/freedesktop/FileManager1",
                               Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], call, None, None)


def settings_acquired(connection, _name):
    connection.register_object("/org/gnome/Settings",
                               Gio.DBusNodeInfo.new_for_xml(ACTIONS).interfaces[0], call, None, None)


Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.FileManager1", 0, acquired, None, None)
Gio.bus_own_name(Gio.BusType.SESSION, "org.gnome.Settings", 0, settings_acquired, None, None)
GLib.MainLoop().run()
