# SPDX-License-Identifier: Apache-2.0
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
try:
    from gi.repository import GLib
    from luma_monitor import integration
    from luma_monitor.model import Identity
except ImportError:
    integration = None


@unittest.skipIf(integration is None, 'PyGObject runtime required')
class ShellIntegrationTests(unittest.TestCase):
    def catalog(self, reply):
        catalog = integration.DesktopCatalog.__new__(integration.DesktopCatalog)
        catalog.entries = {'org.example.Editor': Identity('org.example.Editor.desktop', 'Editor', 'editor', '')}
        catalog.hidden_entries = {}
        catalog._shell_pids = {}
        catalog._shell_dirty = True
        catalog._shell_retry = 0
        class Bus:
            calls = 0
            def call_sync(self, *args):
                self.calls += 1
                return GLib.Variant('(a{sau})', (reply,))
        catalog._bus = Bus()
        return catalog

    def test_shell_identity_and_event_cache(self):
        catalog = self.catalog({'org.example.Editor.desktop': [os.getpid()]})
        catalog.refresh_running()
        self.assertEqual(catalog.resolve('', os.getpid(), '').name, 'Editor')
        catalog.refresh_running()
        self.assertEqual(catalog._bus.calls, 1)
        catalog._shell_changed()
        catalog.refresh_running()
        self.assertEqual(catalog._bus.calls, 2)

    def test_hidden_launcher_with_real_window_is_app(self):
        catalog = self.catalog({'org.example.Editor.desktop': [os.getpid()]})
        catalog.hidden_entries = catalog.entries
        catalog.entries = {}
        catalog.refresh_running()
        self.assertEqual(catalog.resolve('', os.getpid(), '').name, 'Editor')

    def test_unknown_and_recycled_pid_not_attributed(self):
        catalog = self.catalog({'unknown.desktop': [os.getpid()]})
        catalog.refresh_running()
        self.assertIsNone(catalog.resolve('', os.getpid(), ''))
        catalog._shell_pids[os.getpid()] = (-1, catalog.entries['org.example.Editor'])
        self.assertIsNone(catalog.resolve('', os.getpid(), ''))

    def test_quit_false_is_not_fallback_or_kill(self):
        identity = Identity('org.example.Editor.desktop', 'Editor', 'editor', '')
        row = {'members': [SimpleNamespace(pid=os.getpid(), uid=os.getuid(), identity=identity)]}
        class Bus:
            calls = []
            def call_sync(self, *args):
                self.calls.append(args)
                return GLib.Variant('(b)', (False,))
        bus = Bus()
        with patch.object(integration, '_same_process'), patch.object(integration.Gio, 'bus_get_sync', return_value=bus):
            self.assertFalse(integration.quit_row(row))
        self.assertEqual(len(bus.calls), 1)
        self.assertEqual(bus.calls[0][3], 'RequestMonitorQuit')

    def test_performance_profile_is_read_only_and_unknown_stays_unavailable(self):
        class Bus:
            calls=[]
            profile='performance'
            def call_sync(self,*args):
                self.calls.append(args)
                if args[3]!='Get':raise AssertionError('profile write')
                return GLib.Variant('(v)',(GLib.Variant('s',self.profile),))
        bus=Bus()
        with patch.object(integration.Gio,'bus_get_sync',return_value=bus):
            self.assertEqual(integration.performance_profile(),'perf')
            bus.profile='power-saver'
            self.assertEqual(integration.performance_profile(),'saver')
            bus.profile='unknown'
            self.assertIsNone(integration.performance_profile())
        self.assertTrue(all(args[4].unpack()==('net.hadess.PowerProfiles','ActiveProfile') for args in bus.calls))


if __name__ == '__main__':
    unittest.main()
