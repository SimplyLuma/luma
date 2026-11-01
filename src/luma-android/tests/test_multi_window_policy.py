# SPDX-License-Identifier: Apache-2.0
import subprocess
import unittest
from unittest.mock import call, patch

from luma_android import android_settings
from luma_android.engine import CommandResult, WaydroidEngine
from luma_android.errors import RuntimeUnavailableError


class MultiWindowPolicy(unittest.TestCase):
    def test_repeated_launch_policy_does_not_replay_android_init_mount_action(self):
        engine = WaydroidEngine("waydroid")
        with patch.object(engine, "_run", return_value=CommandResult("true\n", "", 0)) as run:
            for _ in range(20):
                engine.set_multi_window_policy(True)
        self.assertEqual(run.call_count, 20)
        self.assertTrue(all(c.args[:2] == ("prop", "get") for c in run.call_args_list))

    def test_changed_policy_writes_once(self):
        engine = WaydroidEngine("waydroid")
        with patch.object(engine, "_run", return_value=CommandResult("false\n", "", 0)) as run:
            engine.set_multi_window_policy(True)
        self.assertEqual(run.call_args_list, [
            call("prop", "get", "persist.waydroid.multi_windows", timeout=15),
            call("prop", "set", "persist.waydroid.multi_windows", "true", timeout=30),
        ])

    def test_failed_read_does_not_blindly_reapply_mount_action(self):
        engine = WaydroidEngine("waydroid")
        with patch.object(engine, "_run", side_effect=RuntimeUnavailableError("unavailable")) as run:
            with self.assertRaises(RuntimeUnavailableError):
                engine.set_multi_window_policy(True)
        run.assert_called_once()


class ForceResizablePolicy(unittest.TestCase):
    def test_writes_through_the_unprivileged_platform_helper(self):
        engine = WaydroidEngine("waydroid")
        with patch("luma_android.engine.subprocess.run") as run:
            engine.set_force_resizable()
        command = run.call_args.args[0]
        self.assertEqual(command[1:], ["-I", "-m", "luma_android.android_settings",
                                       "force_resizable_activities", "1"])
        self.assertNotIn("pkexec", " ".join(command))

    def test_failure_is_logged_not_raised_and_other_engines_are_skipped(self):
        engine = WaydroidEngine("waydroid")
        with patch("luma_android.engine.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "x")):
            with self.assertLogs("luma_android.engine", "WARNING"):
                engine.set_force_resizable()
        with patch("luma_android.engine.subprocess.run") as run:
            WaydroidEngine("/opt/other/engine").set_force_resizable()
        run.assert_not_called()


class FakePlatform:
    def __init__(self, value, accept=True):
        self.value, self.accept, self.writes = value, accept, []

    def settingsGetString(self, mode, key):
        assert (mode, key) == (android_settings.SETTINGS_GLOBAL, "force_resizable_activities")
        return self.value

    def settingsPutString(self, mode, key, value):
        self.writes.append((mode, key, value))
        if self.accept:
            self.value = value


class AndroidGlobalSetting(unittest.TestCase):
    def setUp(self):
        probe = patch.object(android_settings, "is_set", lambda platform, key: platform.value is not None)
        probe.start()
        self.addCleanup(probe.stop)

    def test_unchanged_value_is_not_rewritten(self):
        platform = FakePlatform("1")
        self.assertFalse(android_settings.put(platform, "force_resizable_activities", "1"))
        self.assertEqual(platform.writes, [])

    def test_changed_value_is_written_and_verified(self):
        platform = FakePlatform(None)
        self.assertTrue(android_settings.put(platform, "force_resizable_activities", "1"))
        self.assertEqual(platform.writes, [(2, "force_resizable_activities", "1")])
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            android_settings.put(FakePlatform("0", accept=False), "force_resizable_activities", "1")

    def test_only_admitted_settings_reach_android(self):
        for arguments in (["adb_enabled", "1"], ["force_resizable_activities", "2"]):
            with self.assertRaises(SystemExit):
                android_settings.main(arguments)
