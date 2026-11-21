#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""org.gnome.DisplayManager on the system bus, for run.sh in a container.

The Shell creates its screen shield only where it can lock: with systemd
(/run/systemd/seats) and a display manager that answers its Version. A
container has neither, so this answers for one and run.sh makes the seats
directory. Nothing else of GDM is provided.
"""

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

XML = """
<node>
  <interface name='org.gnome.DisplayManager.Manager'>
    <property name='Version' type='s' access='read'/>
    <method name='RegisterSession'><arg type='a{sv}' direction='in'/></method>
  </interface>
</node>
"""
node = Gio.DBusNodeInfo.new_for_xml(XML)
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
bus.register_object("/org/gnome/DisplayManager/Manager", node.interfaces[0],
                    lambda *a: a[-1].return_value(None),
                    lambda *_a: GLib.Variant("s", "50.0"), None)
Gio.bus_own_name_on_connection(bus, "org.gnome.DisplayManager", Gio.BusNameOwnerFlags.NONE,
                               None, None)
GLib.MainLoop().run()
