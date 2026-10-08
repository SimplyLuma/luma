#!/usr/bin/python3
"""Stand-in mail app search provider (like Charlie): an app with
Categories=Email whose id says nothing about mail, returning many subjects
that contain the query."""
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

XML = """<node><interface name="org.gnome.Shell.SearchProvider2">
<method name="GetInitialResultSet"><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
<method name="GetSubsearchResultSet"><arg type="as" direction="in"/><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
<method name="GetResultMetas"><arg type="as" direction="in"/><arg type="aa{sv}" direction="out"/></method>
<method name="ActivateResult"><arg type="s" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
<method name="LaunchSearch"><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
</interface></node>"""
SUBJECTS = [f"Re: question about the Viola release {i}" for i in range(12)]


def call(_c, _s, _p, _i, method, params, invocation):
    args = params.unpack()
    if method in ("GetInitialResultSet", "GetSubsearchResultSet"):
        terms = [t.lower() for t in args[-1]]
        ids = [str(i) for i, s in enumerate(SUBJECTS) if all(t in s.lower() for t in terms)]
        invocation.return_value(GLib.Variant("(as)", (ids,)))
    elif method == "GetResultMetas":
        metas = [{"id": GLib.Variant("s", i), "name": GLib.Variant("s", SUBJECTS[int(i)]),
                  "description": GLib.Variant("s", "ada@example.org")} for i in args[0]]
        invocation.return_value(GLib.Variant("(aa{sv})", (metas,)))
    else:
        invocation.return_value(None)


def acquired(connection, _name):
    connection.register_object("/org/oracle/Post/SearchProvider",
                               Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], call, None, None)


Gio.bus_own_name(Gio.BusType.SESSION, "org.oracle.Post", 0, acquired, None, None)
GLib.MainLoop().run()
