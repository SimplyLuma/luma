# SPDX-License-Identifier: Apache-2.0
"""Lease ownership/ordering checks; actual signed client admission is separate."""
from pathlib import Path
from types import SimpleNamespace, MethodType
import os
import unittest
from unittest import mock
from prairie_apps.clock import ClockApplication


class ClockForegroundTests(unittest.TestCase):
    def owner(self):
        app = SimpleNamespace(_host_pending=True, _host_generation=1,
            _host_boot=mock.Mock(), _host_required=True, _host_ready=False,
            _foreground_acquired=False, _host_boot_transition=False,
            _pending_tab=None, alarms=None, release=mock.Mock(),
            _show_host_boot=mock.Mock(), _request_clock_foreground=mock.Mock(),
            _window=mock.Mock(), get_dbus_connection=mock.Mock(return_value=object()))
        app._release_clock_foreground = MethodType(ClockApplication._release_clock_foreground, app)
        return app

    def test_denied_or_missing_host_never_opens_alarm_store(self):
        app = self.owner()
        with mock.patch('prairie_apps.clock.AlarmService') as alarms, \
             mock.patch('prairie_apps.background_agent.release_foreground') as release:
            ClockApplication._clock_foreground_ready(app, 1, False)
        alarms.assert_not_called()
        release.assert_called_once_with(app.get_dbus_connection.return_value)
        app._show_host_boot.assert_called_once_with(failed=True)
        app.release.assert_called_once()
        self.assertFalse(app._host_ready)

    def test_acknowledged_lease_precedes_store_and_ui_and_releases_on_close(self):
        app = self.owner()
        def create(owner):
            self.assertTrue(owner._foreground_acquired)
            return mock.Mock()
        with mock.patch('prairie_apps.clock.AlarmService', side_effect=create) as alarms, \
             mock.patch('prairie_apps.background_agent.release_foreground') as release:
            ClockApplication._clock_foreground_ready(app, 1, True)
            service = app.alarms
            self.assertTrue(app._host_ready)
            app._window.return_value.present.assert_called_once()
            app._release_clock_foreground()
            app._release_clock_foreground()
        alarms.assert_called_once_with(app)
        service.start.assert_called_once()
        service.shutdown.assert_called_once()
        release.assert_called_once_with(app.get_dbus_connection.return_value)
        self.assertFalse(app._foreground_acquired)

    def test_late_reply_after_close_releases_without_store_or_window(self):
        app = self.owner(); app._host_boot = None; app._host_generation = 2
        with mock.patch('prairie_apps.clock.AlarmService') as alarms, \
             mock.patch('prairie_apps.background_agent.release_foreground') as release:
            ClockApplication._clock_foreground_ready(app, 1, True)
        release.assert_called_once()
        alarms.assert_not_called(); app._window.assert_not_called()

    def test_reopen_while_old_request_pending_reacquires_after_stale_reply(self):
        app = self.owner(); app._host_generation = 2
        with mock.patch('prairie_apps.clock.AlarmService') as alarms, \
             mock.patch('prairie_apps.background_agent.release_foreground'):
            ClockApplication._clock_foreground_ready(app, 1, True)
        alarms.assert_not_called()
        app._request_clock_foreground.assert_called_once()

    def test_alarm_plan_read_failure_releases_and_offers_repair(self):
        app = self.owner()
        with mock.patch('prairie_apps.clock.AlarmService') as alarms, \
             mock.patch('prairie_apps.background_agent.release_foreground') as release:
            alarms.return_value.start.side_effect = OSError('Host unavailable')
            ClockApplication._clock_foreground_ready(app, 1, True)
        release.assert_called_once()
        alarms.return_value.shutdown.assert_called_once()
        app._show_host_boot.assert_called_once_with(failed=True)
        app._window.assert_not_called()

    def test_bus_activation_cannot_spawn_an_unmanaged_alarm_agent(self):
        path = Path(__file__).resolve().parents[1] / 'data/org.projectluma.ClockHost1.service.in'
        fields = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)
        self.assertEqual(fields['SystemdService'], 'app-org.projectluma.Clock-agent.service')
        self.assertEqual(fields['Exec'], '/usr/bin/false')

    def test_real_retry_window_constructs_without_opening_alarm_store(self):
        from gi.repository import Gtk
        Gtk.init()
        with mock.patch.dict(os.environ, {'LUMA_CLOCK_FIXTURE': 'unit-no-data-read'}), \
             mock.patch('prairie_apps.clock.AlarmService') as alarms:
            app = ClockApplication()
            app.register(None)
            app._host_required = True; app._host_ready = False
            app._show_host_boot(failed=True)
            self.assertTrue(app._host_boot.get_visible())
            self.assertIsNone(app.alarms)
            alarms.assert_not_called()
            app._host_boot.close()
            app.do_shutdown()


if __name__ == '__main__': unittest.main()
