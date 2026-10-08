# SPDX-License-Identifier: Apache-2.0
"""Actual isolated D-Bus name owners; no installed sandbox auth claim.

Run with dbus-run-session. All named owners are disposable connections from
the same actual Unix user, independent of installed apps or desktop sessions.
"""
import os
import unittest
from gi.repository import Gio, GLib
from luma_installer.app_data_broker import _native_owner
from luma_installer.app_data_migration import MigrationError

BUS = 'org.freedesktop.DBus'
PATH = '/org/freedesktop/DBus'
ALIASES = {'org.projectluma.Grid': 'io.luma.Grid',
           'org.projectluma.Stage': 'io.luma.Stage'}


class NativeOwnerRuntime(unittest.TestCase):
    def setUp(self):
        self.assertIn('DBUS_SESSION_BUS_ADDRESS', os.environ,
                      'Use a new isolated dbus-run-session.')
        self.connections = []
        self.observer = self.connection()

    def tearDown(self):
        for connection in reversed(self.connections):
            if not connection.is_closed():
                connection.close_sync(None)

    def connection(self):
        connection = Gio.DBusConnection.new_for_address_sync(
            os.environ['DBUS_SESSION_BUS_ADDRESS'],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        connection.set_exit_on_close(False)
        self.connections.append(connection)
        self.assertTrue(connection.get_unique_name().startswith(':'))
        return connection

    def call(self, connection, method, parameters, response):
        return connection.call_sync(BUS, PATH, BUS, method, parameters,
            GLib.VariantType.new(response), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]

    def own(self, name, connection=None):
        connection = connection or self.connection()
        self.assertEqual(self.call(connection, 'RequestName',
            GLib.Variant('(su)', (name, 4)), '(u)'), 1,
            'Real private owner must acquire the name without queuing.')
        self.assertEqual(self.call(self.observer, 'GetConnectionUnixUser',
            GLib.Variant('(s)', (connection.get_unique_name(),)), '(u)'), os.getuid())
        return connection

    def test_actual_canonical_running_owner_blocks_initial_handoff(self):
        for app in ALIASES:
            owner = self.own(app)
            self.assertEqual(_native_owner(self.observer, app), owner.get_unique_name())
            self.assertNotEqual(owner.get_unique_name(), self.observer.get_unique_name())
            owner.close_sync(None)

    def test_actual_legacy_running_owner_blocks_initial_handoff(self):
        for app, legacy in ALIASES.items():
            owner = self.own(legacy)
            self.assertEqual(_native_owner(self.observer, app), owner.get_unique_name())
            self.assertNotEqual(owner.get_unique_name(), self.observer.get_unique_name())
            owner.close_sync(None)

    def test_canonical_requester_cannot_hide_another_live_legacy_owner(self):
        for app, legacy in ALIASES.items():
            self.own(app, self.observer)
            native = self.own(legacy)
            self.assertEqual(_native_owner(self.observer, app), native.get_unique_name())
            self.assertNotEqual(_native_owner(self.observer, app), self.observer.get_unique_name())
            self.assertEqual(self.call(self.observer, 'ReleaseName',
                GLib.Variant('(s)', (app,)), '(u)'), 1)
            native.close_sync(None)

    def test_truly_absent_names_and_unrelated_owner_allow_handoff(self):
        self.own('org.projectluma.Grid.Unrelated')
        self.own('io.luma.Stage.Unrelated')
        for app in ALIASES:
            self.assertEqual(_native_owner(self.observer, app), '')

    def test_real_release_removes_legacy_blocker(self):
        for app, legacy in ALIASES.items():
            owner = self.own(legacy)
            self.assertTrue(_native_owner(self.observer, app))
            self.assertEqual(self.call(owner, 'ReleaseName',
                GLib.Variant('(s)', (legacy,)), '(u)'), 1)
            self.assertEqual(_native_owner(self.observer, app), '')

    def test_actual_disconnected_lookup_fails_closed(self):
        self.observer.close_sync(None)
        for app in ALIASES:
            with self.assertRaises(MigrationError):
                _native_owner(self.observer, app)

    def test_other_existing_names_preserve_their_exact_namespace(self):
        owner = self.own('org.projectluma.Viola.NativeIntegration')
        self.assertEqual(_native_owner(self.observer, 'com.rhyme.viola'), owner.get_unique_name())
        viewer = self.own('org.projectluma.Viewer')
        self.assertEqual(_native_owner(self.observer, 'org.projectluma.Viewer'), viewer.get_unique_name())


if __name__ == '__main__':
    unittest.main()
