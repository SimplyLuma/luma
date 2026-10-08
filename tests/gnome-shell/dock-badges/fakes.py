#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Test doubles for the dock badge oracle: luma-background (Background1), one
# connection per background agent (BackgroundAgent1), and a LauncherEntry
# emitter on its own connection so it can "quit". Controlled over
# org.luma.BadgeTest on the Background1 connection.
import json
import sys

from gi.repository import Gio, GLib

ADDRESS = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
FLAGS = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
         Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)

BG_XML = '''<node>
<interface name="org.projectluma.Background1">
  <method name="ListAgents"><arg name="agents" type="aa{sv}" direction="out"/></method>
  <signal name="AgentsChanged"/>
  <signal name="AgentChanged"><arg name="app_id" type="s"/><arg name="agent" type="a{sv}"/></signal>
  <property name="Version" type="u" access="read"/>
</interface>
<interface name="org.luma.BadgeTest">
  <method name="SetValues"><arg name="app" type="s" direction="in"/><arg name="json" type="s" direction="in"/></method>
  <method name="Launcher"><arg name="uri" type="s" direction="in"/><arg name="json" type="s" direction="in"/></method>
  <method name="QuitLauncher"/>
  <method name="Notify">
    <arg name="app_id" type="s" direction="in"/>
    <arg name="path" type="s" direction="in"/>
    <arg name="title" type="s" direction="in"/>
  </method>
  <method name="StopAgent"><arg name="app" type="s" direction="in"/></method>
</interface>
</node>'''
AGENT_XML = '''<node><interface name="org.projectluma.BackgroundAgent1">
  <property name="AppId" type="s" access="read"/>
  <property name="Values" type="a{sv}" access="read"/>
</interface></node>'''

AGENTS = {
    'org.projectluma.Messages': ['unread-count', 'badge'],
    'org.projectluma.Phone': ['missed-calls', 'badge'],
    # An agent that never declared `badge`: its values must be ignored.
    'org.projectluma.Weather': ['temperature'],
}


def variant(value):
    if isinstance(value, bool):
        return GLib.Variant('b', value)
    if isinstance(value, int):
        return GLib.Variant('x', value)
    return GLib.Variant('s', str(value))


class Agent:
    def __init__(self, app_id, publishes):
        self.app_id = app_id
        self.publishes = publishes
        self.values = {}
        self.conn = None
        self.start()

    def start(self):
        self.conn = Gio.DBusConnection.new_for_address_sync(ADDRESS, FLAGS, None, None)
        info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
        self.conn.register_object('/org/projectluma/BackgroundAgent1', info, None, self.get_property, None)
        self.conn.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                            'RequestName', GLib.Variant('(su)', (f'{self.app_id}.Agent', 4)),
                            None, Gio.DBusCallFlags.NONE, -1, None)

    def stop(self):
        self.conn.close_sync(None)
        self.conn = None

    def packed(self):
        return GLib.Variant('a{sv}', {k: variant(v) for k, v in self.values.items()})

    def get_property(self, conn, sender, path, iface, name):
        return GLib.Variant('s', self.app_id) if name == 'AppId' else self.packed()

    def set(self, values):
        self.values = values
        if self.conn:
            self.conn.emit_signal(None, '/org/projectluma/BackgroundAgent1', 'org.freedesktop.DBus.Properties',
                                  'PropertiesChanged', GLib.Variant('(sa{sv}as)', (
                                      'org.projectluma.BackgroundAgent1', {'Values': self.packed()}, [])))


agents = {app: Agent(app, pub) for app, pub in AGENTS.items()}
launcher = None


def agent_dict(agent):
    return {
        'app-id': GLib.Variant('s', agent.app_id),
        'agent': GLib.Variant('s', f'{agent.app_id}.Agent'),
        'publishes': GLib.Variant('as', agent.publishes),
        'state': GLib.Variant('s', 'running'),
    }


def call(conn, sender, path, iface, method, params, invocation):
    global launcher
    try:
        if method == 'ListAgents':
            invocation.return_value(GLib.Variant('(aa{sv})', ([agent_dict(a) for a in agents.values()],)))
            return
        if method == 'SetValues':
            app, data = params.unpack()
            agents[app].set(json.loads(data))
        elif method == 'StopAgent':
            agents[params.unpack()[0]].stop()
        elif method == 'Launcher':
            uri, data = params.unpack()
            if launcher is None:
                launcher = Gio.DBusConnection.new_for_address_sync(ADDRESS, FLAGS, None, None)
            props = {k: variant(v) for k, v in json.loads(data).items()}
            launcher.emit_signal(None, '/com/example/launcher', 'com.canonical.Unity.LauncherEntry',
                                 'Update', GLib.Variant('(sa{sv})', (uri, props)))
            launcher.flush_sync(None)
        elif method == 'Notify':
            # Posted from here, not from the Shell: the Shell owns both
            # notification services and cannot call itself and wait.
            app_id, path, title = params.unpack()
            if path == 'fdo':
                conn.call_sync('org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                               'org.freedesktop.Notifications', 'Notify',
                               GLib.Variant('(susssasa{sv}i)', ('Luma', 0, app_id, title, 'body', [],
                                                                {'desktop-entry': GLib.Variant('s', app_id)}, -1)),
                               None, Gio.DBusCallFlags.NONE, 5000, None)
            else:
                conn.call_sync('org.gtk.Notifications', '/org/gtk/Notifications',
                               'org.gtk.Notifications', 'AddNotification',
                               GLib.Variant('(ssa{sv})', (app_id, 'test',
                                                          {'title': GLib.Variant('s', title),
                                                           'body': GLib.Variant('s', 'body')})),
                               None, Gio.DBusCallFlags.NONE, 5000, None)
        elif method == 'QuitLauncher':
            if launcher is not None:
                launcher.close_sync(None)
                launcher = None
        invocation.return_value(None)
    except Exception as error:  # report to the caller
        invocation.return_dbus_error('org.luma.BadgeTest.Error', str(error))


def main():
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    node = Gio.DBusNodeInfo.new_for_xml(BG_XML)
    for info in node.interfaces:
        conn.register_object('/org/projectluma/Background1', info, call,
                             lambda *a: GLib.Variant('u', 2), None)
    Gio.bus_own_name_on_connection(conn, 'org.projectluma.Background1', 0, None, None)
    print('fakes ready', flush=True)
    GLib.MainLoop().run()


if __name__ == '__main__':
    sys.exit(main())
