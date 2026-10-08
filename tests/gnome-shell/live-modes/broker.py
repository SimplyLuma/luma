#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in org.projectluma.SemanticBroker1 publishing one timer Live Extension."""
import datetime
from gi.repository import Gio, GLib
XML = """<node><interface name="org.projectluma.SemanticBroker1">
<method name="ListLiveExtensions"><arg type="aa{sv}" direction="out"/></method>
<signal name="LiveExtensionsChanged"/></interface></node>"""
now = datetime.datetime.now(datetime.timezone.utc)
iso = lambda d: d.isoformat()
S = lambda v: GLib.Variant('s', v)
ext = {'id': S('timer.tea'), 'app_id': S('org.projectluma.Clock'), 'category': S('timer'),
       'title': S('Tea'), 'subtitle': S('4:12 left'), 'schema_version': S('0.1'), 'privacy': S('private'),
       'progress': GLib.Variant('d', -1.0), 'starts_at': S(iso(now - datetime.timedelta(minutes=1))),
       'expires_at': S(iso(now + datetime.timedelta(minutes=5)))}
item = {'application_id': S('org.projectluma.Clock'), 'publication_id': S('p-timer.tea'),
        'extension': GLib.Variant('a{sv}', ext)}
def call(conn, sender, path, iface, method, params, inv):
    inv.return_value(GLib.Variant('(aa{sv})', ([item],)))
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
bus.register_object('/org/projectluma/SemanticBroker1', Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], call, None, None)
def acquired(*_):
    bus.emit_signal(None, '/org/projectluma/SemanticBroker1', 'org.projectluma.SemanticBroker1', 'LiveExtensionsChanged', None)
    print('broker up', flush=True)
Gio.bus_own_name_on_connection(bus, 'org.projectluma.SemanticBroker1', 0, acquired, None)
GLib.MainLoop().run()
