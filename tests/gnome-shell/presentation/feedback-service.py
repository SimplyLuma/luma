# SPDX-License-Identifier: Apache-2.0
"""A stand-in feedbackd for the Keycap key-path check: D-Bus activated as
org.sigxcpu.Feedback, it appends one line per TriggerFeedback call
("app event profile") to the file given as its argument and plays nothing,
like feedbackd on a device without a vibrator."""
import sys

from gi.repository import Gio, GLib

LOG = sys.argv[1]
XML = """
<node>
  <interface name="org.sigxcpu.Feedback">
    <method name="TriggerFeedback">
      <arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="event" direction="in"/>
      <arg type="a{sv}" name="hints" direction="in"/>
      <arg type="i" name="timeout" direction="in"/>
      <arg type="u" name="id" direction="out"/>
    </method>
  </interface>
</node>
"""
serial = 0


def call(_connection, _sender, _path, _interface, method, parameters, invocation):
    global serial
    app_id, event, hints, _timeout = parameters.unpack()
    with open(LOG, "a", encoding="utf-8") as stream:
        stream.write(f"{app_id} {event} {hints.get('profile', '')}\n")
    serial += 1
    invocation.return_value(GLib.Variant("(u)", (serial,)))


def acquired(connection, _name):
    connection.register_object("/org/sigxcpu/Feedback",
                               Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], call)


Gio.bus_own_name(Gio.BusType.SESSION, "org.sigxcpu.Feedback", Gio.BusNameOwnerFlags.NONE,
                 acquired, None, lambda *_: loop.quit())
loop = GLib.MainLoop()
loop.run()
