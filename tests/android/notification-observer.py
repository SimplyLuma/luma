#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Observe only the source-owned APK's host notification traffic in a test VM.

Run as the graphical session user. Records protocol metadata, not message text.
A monitor connection does not impersonate the notification server or emit actions.
"""
import argparse
import json
import os
import time
from gi.repository import Gio, GLib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error('Observation must be bounded to at most 300 seconds')
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    stream = os.fdopen(descriptor, 'w', buffering=1)
    connection = Gio.DBusConnection.new_for_address_sync(
        os.environ['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    def record(kind, **values):
        stream.write(json.dumps({'event': kind, 'monotonic_ns': time.monotonic_ns(), **values}) + '\n')

    def message_filter(_connection, message, incoming, _data):
        if not incoming or message.get_body() is None:
            return message
        values = message.get_body().unpack()
        kind = message.get_message_type()
        if (kind == Gio.DBusMessageType.METHOD_CALL
                and message.get_interface() == 'org.freedesktop.Notifications'
                and message.get_member() == 'Notify' and values[0] == 'Android Integration Test'):
            record('notify', sender=message.get_sender(), replacement=values[1],
                   desktop_entry=values[6].get('desktop-entry'), action_ids=values[5][::2])
        return message

    connection.add_filter(message_filter, None)
    rules = ["type='method_call',interface='org.freedesktop.Notifications',member='Notify',arg0='Android Integration Test'"]
    connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus.Monitoring', 'BecomeMonitor', GLib.Variant('(asu)', (rules, 0)),
        None, Gio.DBusCallFlags.NONE, 5000, None)
    record('monitor-ready')
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(args.seconds, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    try:
        loop.run()
    finally:
        connection.close_sync(None)
        stream.close()


if __name__ == '__main__':
    main()
