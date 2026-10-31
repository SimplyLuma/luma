# SPDX-License-Identifier: Apache-2.0
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from luma_android.app_preferences import AppPreferences, CAPABILITY, LEGACY_TOUCH, TOUCH_PREFIX
from luma_android.app_settings_transport import AppSettingsRequests, launch_command
from luma_android.errors import LumaAndroidError


class Engine:
    def __init__(self, properties=None):
        self.properties = dict(properties or {})
        self.lifecycle_lock = threading.RLock()
        self.live_launchable_packages = lambda: {"org.example.Game", "org.example.Other"}
        self.writes = []

    def _run(self, command, operation, name, *args, timeout):
        assert command == "prop"
        if operation == "set":
            self.writes.append((name, args[0]))
            self.properties[name] = args[0]
        return SimpleNamespace(stdout=self.properties.get(name, ""))


class PreferencesTests(unittest.TestCase):
    def test_scoped_override_can_disable_one_app_under_a_legacy_wildcard(self):
        engine = Engine({CAPABILITY: "1", LEGACY_TOUCH: "org.example.*"})
        one, other = (AppPreferences(engine, name) for name in
                      ("org.example.Game", "org.example.Other"))
        self.assertTrue(one.touch())
        one.set_touch(False)
        self.assertFalse(one.touch())
        self.assertTrue(other.touch())
        self.assertEqual(engine.writes, [(TOUCH_PREFIX + "org.example.Game", "false")])
        self.assertEqual(engine.properties[LEGACY_TOUCH], "org.example.*")

    def test_old_image_retains_other_rules_and_does_not_repeat_an_unchanged_write(self):
        engine = Engine({LEGACY_TOUCH: "org.example.Other"})
        preferences = AppPreferences(engine, "org.example.Game")
        preferences.set_touch(True)
        preferences.set_touch(True)
        self.assertEqual(len(engine.writes), 1)
        self.assertTrue(AppPreferences(engine, "org.example.Other").touch())
        preferences.set_touch(False)
        self.assertEqual(engine.properties[LEGACY_TOUCH], "org.example.Other")

    def test_old_image_never_removes_another_apps_wildcard_to_disable_one_app(self):
        engine = Engine({LEGACY_TOUCH: "org.example.*"})
        with self.assertRaises(LumaAndroidError):
            AppPreferences(engine, "org.example.Game").set_touch(False)
        self.assertEqual(engine.writes, [])

    def test_old_image_limit_rejects_before_any_mutation(self):
        prior = "org.example." + "x" * 70
        engine = Engine({LEGACY_TOUCH: prior})
        with self.assertRaises(LumaAndroidError):
            AppPreferences(engine, "org.example.Game").set_touch(True)
        self.assertEqual(engine.properties[LEGACY_TOUCH], prior)
        self.assertEqual(engine.writes, [])

    def test_removed_application_is_rejected_before_any_mutation(self):
        engine = Engine()
        engine.live_launchable_packages = lambda: set()
        with self.assertRaises(LumaAndroidError):
            AppPreferences(engine, "org.example.Game").set_touch(True)
        self.assertEqual(engine.writes, [])


class SettingsRequestsTests(unittest.TestCase):
    def test_native_window_leaves_transport_sandbox_without_copying_private_environment(self):
        args = launch_command("org.example.Game", {"WAYLAND_DISPLAY": "wayland-1",
                              "DISPLAY": ":0", "UNRELATED_SECRET": "private"})
        self.assertEqual(args[:4], ["/usr/bin/systemd-run", "--user", "--quiet", "--collect"])
        self.assertIn("--setenv=WAYLAND_DISPLAY=wayland-1", args)
        self.assertEqual(args[-3:], ["/usr/bin/luma-android-settings", "--app", "org.example.Game"])
        self.assertFalse(any("private" in item or "UNRELATED_SECRET" in item for item in args))
        with self.assertRaises(LumaAndroidError):
            launch_command("org.example.Game;touch /tmp/x")

    @staticmethod
    def request(uid, package="org.example.Game"):
        request = Mock()
        request.sender_euid.return_value = uid
        request.init_reader.return_value.read_string16.return_value = package
        return request

    def test_ordinary_app_is_denied_before_parsing(self):
        present = Mock()
        receiver = AppSettingsRequests(present)
        request = self.request(10133)
        self.assertEqual(receiver.receive(request, 1), -1)
        request.init_reader.assert_not_called()
        present.assert_not_called()

    def test_only_a_valid_package_can_be_presented_with_bounded_frequency(self):
        present, clock = Mock(), Mock(return_value=0)
        receiver = AppSettingsRequests(present, clock=clock)
        for value in ("org.example.Game;touch /tmp/x", "../app", "x" * 256, None):
            self.assertEqual(receiver.receive(self.request(1000, value), 1), -22)
        self.assertEqual(receiver.receive(self.request(1000), 7), -38)
        self.assertEqual(receiver.receive(self.request(1000), 1), 0)
        self.assertEqual(receiver.receive(self.request(1000), 1), -11)
        clock.return_value = 1.1
        self.assertEqual(receiver.receive(self.request(1000), 1), 0)
        self.assertEqual(present.call_count, 2)
