#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Private session-bus authentication/ABI test; no modem or notification server."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from gi.repository import Gio, GLib
from prairie_apps.messages_reply import ReplyEndpoint, ReplySender


def main():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    def client():
        return Gio.DBusConnection.new_for_address_sync(address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
    shell = client()
    attacker = client()
    # Claim only the isolated test bus's shell identity. No system/session shell is touched.
    shell.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                    'RequestName', GLib.Variant('(su)', ('org.gnome.Shell', 4)),
                    GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
    loop = GLib.MainLoop()
    errors = []
    sends = []
    token = 'a' * 32
    request = 'b' * 32
    with tempfile.TemporaryDirectory() as temporary:
        endpoint = ReplyEndpoint(bus, Path(temporary) / 'messages.db',
            lambda notification_id, supplied: '+12025550100' if notification_id == 7 and supplied == token else None)
        endpoint.sender = ReplySender(endpoint.sender.path, lambda: SimpleNamespace(
            inspect=lambda: SimpleNamespace(available=True), send=lambda address, text: sends.append((address, text))))
        def call(connection, notification_id=7, supplied=token, key=request, text='private reply'):
            return connection.call_sync(bus.get_unique_name(), endpoint.PATH, endpoint.INTERFACE, 'Reply',
                GLib.Variant('(usss)', (notification_id, supplied, key, text)), GLib.VariantType('(ss)'),
                Gio.DBusCallFlags.NO_AUTO_START, 3000, None).unpack()
        def rejected(connection, code, **kwargs):
            try:
                call(connection, **kwargs)
            except GLib.Error as error:
                assert Gio.DBusError.get_remote_error(error) == endpoint.INTERFACE + '.Error.' + code
            else:
                raise AssertionError('Request was not rejected: ' + code)
        def exercise():
            try:
                rejected(attacker, 'Unauthorized')
                rejected(shell, 'StaleNotification', supplied='c' * 32)
                rejected(shell, 'StaleNotification', notification_id=8)
                rejected(shell, 'InvalidInput', text='界' * 1366)
                result = call(shell)
                assert result[1] == 'sent'
                assert call(shell) == result
                rejected(shell, 'RequestConflict', text='changed body')
                assert sends == [('+12025550100', 'private reply')]
                shell.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                    'ReleaseName', GLib.Variant('(s)', ('org.gnome.Shell',)), GLib.VariantType('(u)'),
                    Gio.DBusCallFlags.NONE, 2000, None)
                # Barrier through the endpoint's owning main context, after the bus-owner change.
                done = threading.Event()
                def owner_gone():
                    if endpoint.owner is None:
                        done.set()
                        return False
                    return True
                GLib.idle_add(owner_gone)
                assert done.wait(2)
                rejected(shell, 'Unauthorized', key='d' * 32)
            except Exception as error:
                errors.append(type(error).__name__ + ': ' + str(error))
            finally:
                GLib.idle_add(loop.quit)
        def ready():
            if endpoint.owner != shell.get_unique_name():
                return True
            threading.Thread(target=exercise, daemon=True).start()
            return False
        GLib.timeout_add(10, ready)
        def timeout():
            errors.append('Runtime test timed out')
            loop.quit()
            return False
        timer = GLib.timeout_add_seconds(10, timeout)
        loop.run()
        GLib.source_remove(timer)
        endpoint.close()
    shell.close_sync(None)
    attacker.close_sync(None)
    assert not errors, errors
    print('PASS: targeted Reply ABI, Shell-owner authentication/revocation, stale IDs/tokens, size limits, durable dedup')


if __name__ == '__main__':
    main()
