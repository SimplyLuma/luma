# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import call, patch

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
