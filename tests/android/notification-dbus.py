#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the production bridge over a real, private session bus.

Run with dbus-run-session. The host notification server is a separate process;
Binder is supplied by a listener fixture. This is not an Android/device test.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib

NAME = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'
CONTROL = 'org.projectluma.NotificationFixture'
XML = '''<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
<arg type="u" direction="out"/></method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
<signal name="ActivationToken"><arg type="u"/><arg type="s"/></signal>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
</interface><interface name="org.projectluma.NotificationFixture">
<method name="Records"><arg type="s" direction="out"/></method>
<method name="Emit"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="s" direction="in"/><arg type="u" direction="in"/></method>
</interface></node>'''


def server():
    connection = Gio.bus_get_sync(Gio.BusType.SESSION)
    records = []
    counter = 200
    node = Gio.DBusNodeInfo.new_for_xml(XML)

    def call(bus, sender, path, interface, method, parameters, invocation):
        nonlocal counter
        values = parameters.unpack()
        if method == 'Notify':
            counter += 1
            notification_id = values[1] or counter
            records.append({'id': notification_id, 'replacement': values[1],
                            'body': values[4], 'hints': values[6], 'actions': values[5]})
            invocation.return_value(GLib.Variant('(u)', (notification_id,)))
        elif method == 'CloseNotification':
            bus.emit_signal(None, PATH, NAME, 'NotificationClosed',
                            GLib.Variant('(uu)', (values[0], 3)))
            invocation.return_value(None)
        elif method == 'Records':
            invocation.return_value(GLib.Variant('(s)', (json.dumps(records),)))
        elif method == 'Emit':
            signal, notification_id, text, reason = values
            assert signal in ('ActionInvoked', 'ActivationToken', 'NotificationClosed')
            signature, payload = ('(uu)', (notification_id, reason)) if signal == 'NotificationClosed' else ('(us)', (notification_id, text))
            bus.emit_signal(None, PATH, NAME, signal, GLib.Variant(signature, payload))
            invocation.return_value(None)

    for interface in node.interfaces:
        connection.register_object(PATH, interface, call, None, None)
    connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'RequestName', GLib.Variant('(su)', (NAME, 4)),
        None, Gio.DBusCallFlags.NONE, 2000, None)
    GLib.MainLoop().run()


def main():
    import dbus.mainloop.glib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / 'src/luma-android'))
    from luma_android import waydroid_notifications as transport
    from luma_android.notification_bridge import Notification
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    args = SimpleNamespace()
    listener = Mock()
    processes = []

    def wait(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            while GLib.MainContext.default().iteration(False):
                pass
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('Private notification bus did not reach the expected state')

    def owner():
        return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (NAME,)),
            None, Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    def launch():
        process = subprocess.Popen([sys.executable, __file__, '--server'])
        processes.append(process)
        wait(owner)
        return process

    def control(method, parameters=None):
        return bus.call_sync(NAME, PATH, CONTROL, method, parameters, None,
                             Gio.DBusCallFlags.NONE, 2000, None)

    def emit(signal, notification_id, text='', reason=0):
        control('Emit', GLib.Variant('(susu)', (signal, notification_id, text, reason)))

    def binder_fixture(args, bridge, stopped):
        bridge.register(listener)
        stopped.wait(15)

    try:
        first = launch()
        with patch.object(transport, 'add_service', binder_fixture):
            transport.start(args, {})
            wait(lambda: listener in args.luma_notifications[3].listeners)
            bridge = args.luma_notifications[3]
            wait(lambda: bridge.generation > 0)
            record = Notification('org.example.Chat', 'Chat', 'Test', '<b>private & literal',
                                  (('default', ''), ('read', 'Mark read')),
                                  image=SimpleNamespace(width=2, height=2, rowstride=8,
                                      has_alpha=True, data=bytes(range(16))))
            notification_id = bridge.post(record)
            observed = json.loads(control('Records').unpack()[0])[-1]
            assert observed['hints']['desktop-entry'] == 'waydroid.org.example.Chat'
            assert observed['body'] == '&lt;b&gt;private &amp; literal'
            assert observed['replacement'] == 0
            assert observed['hints']['image-data'][6] == list(range(16))
            emit('ActivationToken', notification_id, 'verified-token')
            emit('ActionInvoked', notification_id, 'read')
            wait(lambda: listener.action.call_count == 1)
            listener.action.assert_called_with(notification_id, 'read', 'verified-token')
            emit('NotificationClosed', notification_id, reason=1)
            wait(lambda: notification_id not in bridge.records)
            listener.dismissed.assert_not_called()
            notification_id = bridge.post(record)
            emit('NotificationClosed', notification_id, reason=2)
            wait(lambda: listener.dismissed.call_count == 1)
            listener.dismissed.assert_called_with(notification_id)
            notification_id = bridge.post(record)
            bridge.withdraw(notification_id)
            wait(lambda: notification_id not in bridge.records)
            assert listener.dismissed.call_count == 1
            stale = bridge.post(record)
            old_generation = bridge.generation
            first.terminate()
            first.wait(timeout=5)
            wait(lambda: bridge.generation > old_generation)
            assert not bridge.records
            launch()
            wait(lambda: bridge.generation > old_generation + 1)
            emit('ActionInvoked', stale, 'default')
            bridge.post(record, stale)
            observed = json.loads(control('Records').unpack()[0])[-1]
            assert observed['replacement'] == 0
            report = {'passed': True, 'transport': 'real private D-Bus',
                      'binder': 'fixture; not Android runtime qualification',
                      'checks': ['identity', 'image bytes', 'markup', 'action/token', 'expiry',
                                 'dismissal', 'withdrawal', 'server restart', 'stale replacement']}
            output = Path(os.environ.get('DEV_ARTIFACT_DIR', '.')) / 'notification-dbus.json'
            output.write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report))
    finally:
        transport.stop(args)
        if hasattr(args, 'notification_manager'):
            args.notification_manager.join(timeout=2)
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    server() if '--server' in sys.argv else main()
