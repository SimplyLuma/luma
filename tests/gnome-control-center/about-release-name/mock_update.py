# SPDX-License-Identifier: GPL-2.0-or-later
"""Stand-in org.projectluma.Update1 on the system bus: BootedName and BootedVersion only."""
import sys

from gi.repository import Gio, GLib

XML = """<node><interface name="org.projectluma.Update1">
<property name="BootedName" type="s" access="read"/>
<property name="BootedVersion" type="s" access="read"/>
</interface></node>"""
NAME, VERSION = sys.argv[1], sys.argv[2]


def get_property(connection, sender, path, interface, prop):
    print("Get", prop, "from", sender, flush=True)
    return GLib.Variant("s", NAME if prop == "BootedName" else VERSION)


def acquired(connection, name):
    connection.register_object("/org/projectluma/Update1", Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
                               None, get_property, None)
    print("owned", name, flush=True)


Gio.bus_own_name(Gio.BusType.SYSTEM, "org.projectluma.Update1", Gio.BusNameOwnerFlags.NONE, None, acquired, None)
GLib.MainLoop().run()
