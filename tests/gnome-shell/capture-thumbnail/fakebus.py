#!/usr/bin/python3
"""Fake session services for the thumbnail oracle: org.freedesktop.FileManager1
(Show in Filer) and the desktop portal's OpenURI (Other Application...). Each
call is appended to $ORACLE_OUT/fakebus.jsonl."""
import json, os
from gi.repository import Gio, GLib

LOG = os.path.join(os.environ.get('ORACLE_OUT', '/tmp'), 'fakebus.jsonl')
FM = '''<node><interface name="org.freedesktop.FileManager1">
<method name="ShowFolders"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
<method name="ShowItems"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
<method name="ShowItemProperties"><arg type="as" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>'''
PORTAL = '''<node><interface name="org.freedesktop.portal.OpenURI">
<method name="OpenURI"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="OpenFile"><arg type="s" direction="in"/><arg type="h" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<property name="version" type="u" access="read"/>
</interface></node>'''


def record(entry):
    with open(LOG, 'a') as f:
        f.write(json.dumps(entry) + '\n')


def call(conn, sender, path, iface, method, params, invocation):
    args = params.unpack()
    entry = {'iface': iface, 'method': method}
    if method == 'OpenFile':
        fds = invocation.get_message().get_unix_fd_list()
        target = None
        if fds is not None:
            fd = fds.get(args[1])
            target = os.readlink(f'/proc/self/fd/{fd}')
            os.close(fd)
        entry.update(parent=args[0], file=target, options={k: v for k, v in args[2].items()})
        invocation.return_value(GLib.Variant('(o)', ('/org/freedesktop/portal/desktop/request/1_1/t',)))
    elif method == 'OpenURI':
        entry.update(parent=args[0], uri=args[1], options={k: v for k, v in args[2].items()})
        invocation.return_value(GLib.Variant('(o)', ('/org/freedesktop/portal/desktop/request/1_1/t',)))
    else:
        entry.update(uris=args[0], startup=args[1])
        invocation.return_value(None)
    record(entry)


def get_property(conn, sender, path, iface, name):
    return GLib.Variant('u', 4)


def on_bus(conn, _name):
    for xml, path in ((FM, '/org/freedesktop/FileManager1'), (PORTAL, '/org/freedesktop/portal/desktop')):
        info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
        register = getattr(conn, 'register_object_with_closures2', None) or conn.register_object_with_closures
        register(path, info, call, get_property, None)


Gio.bus_own_name(Gio.BusType.SESSION, 'org.freedesktop.FileManager1', 0, on_bus, None, None)
Gio.bus_own_name(Gio.BusType.SESSION, 'org.freedesktop.portal.Desktop', 0, None,
                 lambda *_: record({'owned': 'portal'}), None)
record({'event': 'ready'})
GLib.MainLoop().run()
