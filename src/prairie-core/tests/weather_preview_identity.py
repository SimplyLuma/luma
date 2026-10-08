# SPDX-License-Identifier: Apache-2.0
"""No-window identity proof; run under a fresh dbus-run-session on the host.

Set WEATHER_ORIGINAL_BUS to the caller's DBUS_SESSION_BUS_ADDRESS before
starting the private session. Registration exercises GApplication ownership
without activating Weather, opening its stores, or creating a window. The
private bus and all its names disappear when dbus-run-session exits.

From the worktree, on the host after the capsule recovery pause is lifted:
    WEATHER_ORIGINAL_BUS="${DBUS_SESSION_BUS_ADDRESS:-}" dbus-run-session -- \
        python3 src/prairie-core/tests/weather_preview_identity.py
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src/prairie-core'), str(ROOT / 'src/luma-platform/appkit')]
from gi.repository import Gio, GLib
from prairie_apps import weather


def production_sentinel():
    """Own the production name in a separate process on this private bus."""
    activations = []
    application = Gio.Application(application_id='org.projectluma.Weather')
    application.connect('activate', lambda *_: activations.append('production'))
    if not application.register(None) or application.get_is_remote():
        raise RuntimeError('Private production sentinel did not own its name')
    loop = GLib.MainLoop()
    def close(*_):
        loop.quit()
        return False
    GLib.io_add_watch(sys.stdin.fileno(), GLib.IOCondition.HUP | GLib.IOCondition.IN, close)
    GLib.timeout_add_seconds(30, close)
    print('PRODUCTION-NAME-OWNED', flush=True)
    loop.run()
    print('PRODUCTION-ACTIVATIONS=' + str(len(activations)), flush=True)


class PrivateBusIdentityTests(unittest.TestCase):
    def test_preview_owns_a_distinct_name_and_never_activates_production(self):
        self.assertIn('WEATHER_ORIGINAL_BUS', os.environ,
                      'Run through dbus-run-session with the original bus recorded')
        private_bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
        self.assertTrue(private_bus)
        self.assertNotEqual(private_bus, os.environ['WEATHER_ORIGINAL_BUS'])
        production_id = 'org.projectluma.Weather'
        preview_id = production_id + '.LumaUIPreview'
        self.assertEqual(weather.APP_ID, production_id)
        sentinel = subprocess.Popen([sys.executable, __file__, '--sentinel'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(sentinel.stdout.readline().strip(), 'PRODUCTION-NAME-OWNED')
            # Same override as sync-lumaui-preview.sh's generated Prairie runner.
            with patch.object(weather, 'APP_ID', getattr(weather, 'APP_ID', production_id)
                              + '.LumaUIPreview'):
                preview = weather.WeatherApplication()
                self.assertEqual(preview.get_application_id(), preview_id)
                self.assertTrue(preview.register(None))
                self.assertFalse(preview.get_is_remote(), 'Preview collided with the production instance')
                connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                owner = connection.call_sync(
                    'org.freedesktop.DBus', '/org/freedesktop/DBus',
                    'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (preview_id,)),
                    GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 5000, None)
                self.assertEqual(owner.unpack()[0], connection.get_unique_name())
                production_owner = connection.call_sync(
                    'org.freedesktop.DBus', '/org/freedesktop/DBus',
                    'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (production_id,)),
                    GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 5000, None)
                self.assertNotEqual(production_owner.unpack()[0], owner.unpack()[0])
                self.assertEqual(preview.get_windows(), [])
            # A launcher overriding the wrong variable would collide. Detect
            # that failure on this private bus without activating either app.
            with patch.object(weather, 'APPLICATION_ID', preview_id, create=True):
                mismatched = weather.WeatherApplication()
                self.assertEqual(mismatched.get_application_id(), production_id)
                self.assertTrue(mismatched.register(None))
                self.assertTrue(mismatched.get_is_remote(), 'Wrong-hook control must detect the production collision')
                self.assertEqual(mismatched.get_windows(), [])
        finally:
            sentinel.stdin.close()
            sentinel.stdin = None
            output, errors = sentinel.communicate(timeout=40)
        self.assertEqual(sentinel.returncode, 0, errors)
        self.assertEqual(output.strip(), 'PRODUCTION-ACTIVATIONS=0')
        self.assertEqual(weather.APP_ID, production_id)
        self.assertEqual(weather.ICON_NAME, production_id)
        print('PASS: private bus; production and preview names distinct; wrong-hook collision detected; production activations=0; windows=0')


if __name__ == '__main__':
    if '--sentinel' in sys.argv:
        production_sentinel()
    else:
        unittest.main()
