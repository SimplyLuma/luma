# SPDX-License-Identifier: Apache-2.0
"""Real GIO errors cannot turn an unavailable native-owner check into absence."""
import unittest
from gi.repository import Gio, GLib
from luma_installer.app_data_broker import _native_owner
from luma_installer.app_data_migration import MigrationError


class NativeOwnerAdmission(unittest.TestCase):
    def connection(self, error=None, owner=':1.42'):
        class Connection:
            def call_sync(inner, *arguments):
                self.assertEqual(arguments[3], 'GetNameOwner')
                self.assertEqual(arguments[0], 'org.freedesktop.DBus')
                inner.requested = arguments[4].unpack()[0]
                if error is not None: raise error
                return GLib.Variant('(s)', (owner,))
        return Connection()

    def test_confirmed_owner_and_browser_native_name(self):
        connection = self.connection()
        self.assertEqual(_native_owner(connection,'org.projectluma.Viewer'),':1.42')
        self.assertEqual(connection.requested,'org.projectluma.Viewer')
        self.assertEqual(_native_owner(connection,'com.rhyme.viola'),':1.42')
        self.assertEqual(connection.requested,'org.projectluma.Viola.NativeIntegration')

    def test_only_remote_name_has_no_owner_is_absence(self):
        error = Gio.DBusError.new_for_dbus_error('org.freedesktop.DBus.Error.NameHasNoOwner','absent')
        self.assertEqual(_native_owner(self.connection(error),'org.projectluma.Viewer'),'')

    def test_timeout_denial_disconnect_and_unavailable_bus_fail_closed(self):
        errors = [Gio.DBusError.new_for_dbus_error(name,'native state unavailable') for name in (
            'org.freedesktop.DBus.Error.AccessDenied','org.freedesktop.DBus.Error.NoReply',
            'org.freedesktop.DBus.Error.ServiceUnknown','org.freedesktop.DBus.Error.Disconnected')]
        errors.append(GLib.Error.new_literal(Gio.io_error_quark(),'connection closed',Gio.IOErrorEnum.CLOSED))
        for error in errors:
            with self.subTest(error=error), self.assertRaises(MigrationError):
                _native_owner(self.connection(error),'org.projectluma.Viewer')


if __name__ == '__main__': unittest.main()
