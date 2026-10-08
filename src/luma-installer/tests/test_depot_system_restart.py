# SPDX-License-Identifier: Apache-2.0
"""Depot's "Restart to finish updating" never restarts past the person or the session.

gnome-session answers org.gnome.SessionManager.Reboot only when its end-session
dialog is settled, and answers G_IO_ERROR_CANCELLED when the person cancels.
Depot once gave the call 30 seconds and then fell back to the agent's Apply()
on any error, which restarted the computer after a cancel or a slow dialog.
"""

import unittest
from unittest import mock

from luma_installer import depot_system_update as su

try:
    from gi.repository import Gio, GLib
    from luma_depot import system_updates
except (ImportError, ValueError):  # no GObject introspection here
    system_updates = None

CANCELLED = 'org.gtk.GDBus.UnmappedGError.Quark._g_2dio_2derror_2dquark.Code19'


class Outcome(unittest.TestCase):
    def test_only_a_missing_session_manager_falls_back_to_the_agent(self):
        for name in ('org.freedesktop.DBus.Error.ServiceUnknown', 'org.freedesktop.DBus.Error.NameHasNoOwner',
                     'org.freedesktop.DBus.Error.UnknownMethod'):
            self.assertEqual(su.restart_outcome(name), 'agent')

    def test_cancel_and_everything_else_never_fall_back(self):
        self.assertEqual(su.restart_outcome(CANCELLED), 'cancelled')
        for name in ('org.freedesktop.DBus.Error.NoReply', 'org.freedesktop.DBus.Error.Timeout',
                     'org.freedesktop.DBus.Error.TimedOut', 'org.freedesktop.DBus.Error.AccessDenied',
                     'org.gnome.SessionManager.Error.LockedDown', 'org.freedesktop.DBus.Error.Failed', ''):
            self.assertEqual(su.restart_outcome(name), 'refused', name)


class FakeSession:
    def __init__(self):
        self.calls = []

    def call(self, *args):
        self.calls.append(args)


class FakeReply:
    def __init__(self, error):
        self.error = error

    def call_finish(self, _result):
        if self.error is not None:
            raise self.error
        return None


@unittest.skipIf(system_updates is None, 'PyGObject is not available')
class Restart(unittest.TestCase):
    def setUp(self):
        self.updates = system_updates.SystemUpdates.__new__(system_updates.SystemUpdates)
        self.updates.on_change = mock.Mock()
        self.updates.error = ''
        self.updates.busy = ''
        self.updates.call = mock.Mock()
        self.session = FakeSession()

    def restart_and_answer(self, error):
        with mock.patch.object(system_updates.Gio, 'bus_get_sync', return_value=self.session):
            self.updates.restart()
        self.assertEqual(len(self.session.calls), 1)
        args = self.session.calls[0]
        self.assertEqual(args[:4], ('org.gnome.SessionManager', '/org/gnome/SessionManager',
                                    'org.gnome.SessionManager', 'Reboot'))
        # No timeout: the end-session dialog takes as long as the person needs.
        self.assertEqual(args[7], GLib.MAXINT)
        callback = args[9]
        callback(FakeReply(error), None)

    def test_accepted_reboot_does_nothing_more(self):
        self.restart_and_answer(None)
        self.updates.call.assert_not_called()

    def test_cancelled_dialog_does_not_restart(self):
        self.restart_and_answer(Gio.DBusError.new_for_dbus_error(CANCELLED, 'Operation was cancelled'))
        self.updates.call.assert_not_called()
        self.assertEqual(self.updates.error, '')

    def test_timeout_or_refusal_does_not_restart(self):
        for name in ('org.freedesktop.DBus.Error.NoReply', 'org.freedesktop.DBus.Error.Timeout',
                     'org.gnome.SessionManager.Error.LockedDown'):
            self.updates.call.reset_mock()
            self.session = FakeSession()
            self.restart_and_answer(Gio.DBusError.new_for_dbus_error(name, 'Logout has been locked down'))
            self.updates.call.assert_not_called()
            self.assertEqual(self.updates.error, 'Luma did not restart: Logout has been locked down')

    def test_no_session_manager_asks_the_agent(self):
        self.restart_and_answer(Gio.DBusError.new_for_dbus_error('org.freedesktop.DBus.Error.ServiceUnknown',
                                                                 'The name is not activatable'))
        self.updates.call.assert_called_once_with('Apply')

    def test_no_session_bus_asks_the_agent(self):
        failure = GLib.Error.new_literal(Gio.io_error_quark(), 'no session bus', Gio.IOErrorEnum.NOT_FOUND)
        with mock.patch.object(system_updates.Gio, 'bus_get_sync', side_effect=failure):
            self.updates.restart()
        self.updates.call.assert_called_once_with('Apply')

    def test_remote_error_text_loses_the_gdbus_prefix(self):
        error = Gio.DBusError.new_for_dbus_error('org.projectluma.Update1.Error.Busy', 'Luma is busy')
        self.assertEqual(system_updates._remote_text(error), 'Luma is busy')


if __name__ == '__main__':
    unittest.main()
