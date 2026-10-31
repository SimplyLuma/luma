#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Real private-bus reply authorization; Android listener is an explicit fixture.

Run with dbus-run-session. No actual desktop name is claimed outside that bus.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

NAME = 'org.projectluma.NotificationReplyFixture'
CONTROL_PATH = '/org/projectluma/NotificationReplyFixture'


def server():
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    from types import SimpleNamespace
    from luma_android.notification_bridge import Notification, NotificationBridge
    from luma_android.notification_reply_dbus import export_reply_endpoint
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    bridge = NotificationBridge(SimpleNamespace(notify=lambda *_: 42, close=lambda *_: None))
    class Listener:
        count = 0
        uncertain = False
        def reply(self, notification_id, action, request_id, text, done):
            assert notification_id == 42 and action == 'reply'
            assert text == 'synthetic reply'
            self.count += 1
            result = None if self.uncertain else True
            GLib.timeout_add(5, lambda: (done(result), False)[1])
    listener = Listener()
    bridge.register(listener)
    endpoint = export_reply_endpoint(bus, bridge)
    watch = bus.watch_name_owner('org.gnome.Shell', endpoint.set_owner)
    class Control(dbus.service.Object):
        @dbus.service.method(NAME, in_signature='', out_signature='us')
        def Post(self):
            record = Notification('org.example.Chat', 'Chat', 'Synthetic title', 'Synthetic body',
                                  (('reply', 'Reply'),), reply_action='reply')
            bridge.post(record, 42 if 42 in bridge.records else 0, listener)
            return 42, bridge.records[42].reply_token
        @dbus.service.method(NAME, in_signature='', out_signature='uub')
        def Stats(self):
            return listener.count, bridge.reply_ledger.pending, bool(endpoint.owner)
        @dbus.service.method(NAME, in_signature='b', out_signature='')
        def Uncertain(self, value):
            listener.uncertain = bool(value)
    control = Control(bus, CONTROL_PATH)
    name = dbus.service.BusName(NAME, bus=bus, do_not_queue=True)
    try:
        GLib.MainLoop().run()
    finally:
        watch.cancel(); endpoint.close()
        control.remove_from_connection()
        del name


def main():
    from gi.repository import Gio, GLib
    from luma_android.notification_reply_dbus import INTERFACE, PATH
    connection = Gio.bus_get_sync(Gio.BusType.SESSION)
    def bus_call(method, values):
        return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', method, values, None, Gio.DBusCallFlags.NONE, 2000, None).unpack()
    assert bus_call('RequestName', GLib.Variant('(su)', ('org.gnome.Shell', 4)))[0] == 1
    producer = subprocess.Popen([sys.executable, __file__, '--server'])
    foreign = Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    def control(method, values=None):
        return connection.call_sync(NAME, CONTROL_PATH, NAME, method, values, None,
                                    Gio.DBusCallFlags.NONE, 3000, None).unpack()
    def wait_ready(expected):
        for _ in range(50):
            try:
                if bool(control('Stats')[2]) == expected:
                    return
            except GLib.Error:
                pass
            time.sleep(.02)
        raise RuntimeError('Private reply fixture owner did not become ready')
    def reply(client, token, request_id, expected=None):
        values = GLib.Variant('(usss)', (42, token, request_id, 'synthetic reply'))
        try:
            result = client.call_sync(NAME, PATH, INTERFACE, 'Reply', values,
                GLib.VariantType.new('(ss)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()
        except GLib.Error as error:
            remote = Gio.DBusError.get_remote_error(error)
            assert expected and remote == INTERFACE + '.Error.' + expected, remote
            return None
        assert expected is None, 'Unexpected authorized reply'
        return result
    try:
        wait_ready(True)
        _, token = control('Post')
        request_id = str(uuid.uuid4())
        reply(foreign, token, request_id, 'Unauthorized')
        assert control('Stats')[0] == 0
        first = reply(connection, token, request_id)
        assert first[1] == 'sent'
        assert reply(connection, token, request_id) == first
        assert control('Stats')[:2] == (1, 0)
        _, replacement = control('Post')
        assert replacement != token
        reply(connection, token, str(uuid.uuid4()), 'StaleNotification')
        second = reply(connection, replacement, str(uuid.uuid4()))
        assert second[1] == 'sent' and control('Stats')[0] == 2
        assert bus_call('ReleaseName', GLib.Variant('(s)', ('org.gnome.Shell',)))[0] == 1
        wait_ready(False)
        reply(connection, replacement, str(uuid.uuid4()), 'Unauthorized')
        assert bus_call('RequestName', GLib.Variant('(su)', ('org.gnome.Shell', 4)))[0] == 1
        wait_ready(True)
        reply(connection, replacement, str(uuid.uuid4()), 'StaleNotification')
        _, token = control('Post')
        control('Uncertain', GLib.Variant('(b)', (True,)))
        request_id = str(uuid.uuid4())
        uncertain = reply(connection, token, request_id)
        assert uncertain[1] == 'uncertain'
        assert reply(connection, token, request_id) == uncertain
        assert control('Stats')[:2] == (3, 0)
        report = {'qualification': 'real private D-Bus endpoint/credentials/idempotency; Android listener fixture only; no renderer/device/Binder proof',
                  'foreign_caller_rejected': True, 'same_request_dispatched_once': True,
                  'replacement_revoked_old_token': True, 'shell_owner_loss_revoked_grants': True,
                  'uncertain_reply_not_replayed': True, 'accepted_fixture_dispatches': 3}
        root = os.environ.get('DEV_ARTIFACT_DIR')
        if root:
            (Path(root) / 'notification-reply-dbus.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)
    finally:
        foreign.close_sync(None)
        producer.terminate()
        try:
            producer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            producer.kill(); producer.wait(timeout=5)


if __name__ == '__main__':
    server() if '--server' in sys.argv else main()
