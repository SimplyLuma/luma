# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import Mock, patch

from luma_android import recovery
from luma_android.errors import RuntimeUnavailableError


class RecoveryWait(unittest.TestCase):
    def test_status_query_is_read_only_and_owner_scoped(self):
        with patch.object(recovery.pwd, "getpwuid") as owner, \
             patch.object(recovery.subprocess, "run") as run:
            owner.return_value.pw_name = "nick"
            run.return_value.returncode = 0
            run.return_value.stdout = "active\n"
            self.assertEqual(recovery._recovery_state(), "active")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "show",
            "luma-android-recover@nick.service", "--property=ActiveState", "--value"])

    def test_running_recovery_finishes_before_returning(self):
        with patch.object(recovery, "_recovery_state",
                          side_effect=["deactivating", "activating", "active"]), \
             patch.object(recovery.time, "sleep") as sleep:
            self.assertTrue(recovery.wait_for_recovery())
        self.assertEqual(sleep.call_count, 2)

    def test_recovery_wait_has_a_deadline(self):
        with patch.object(recovery, "_recovery_state", return_value="activating"), \
             patch.object(recovery.time, "monotonic", side_effect=[0, 0, 1]), \
             patch.object(recovery.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeUnavailableError, "still recovering"):
                recovery.wait_for_recovery(timeout=0.1)
        sleep.assert_called_once()

    def test_healthy_runtime_is_not_delayed(self):
        with patch.object(recovery, "_recovery_state", return_value="active"), \
             patch.object(recovery.time, "sleep") as sleep:
            self.assertFalse(recovery.wait_for_recovery())
        sleep.assert_not_called()

    def test_failed_or_lost_recovery_is_not_success(self):
        for states in (["failed"], ["activating", None]):
            with self.subTest(states=states), \
                 patch.object(recovery, "_recovery_state", side_effect=states), \
                 patch.object(recovery.time, "sleep"):
                with self.assertRaises(RuntimeUnavailableError):
                    recovery.wait_for_recovery()


class InterruptedLaunch(unittest.TestCase):
    def setUp(self):
        self.engine = Mock(executable="waydroid")
        self.restore = Mock(return_value=True)

    def launch(self):
        recovery.launch_with_recovery(self.engine, "org.example.App", self.restore)

    def test_healthy_launch_is_dispatched_once_without_recovery_probe(self):
        with patch.object(recovery, "_session_identity", return_value="old"), \
             patch.object(recovery, "_recovery_state") as query:
            self.launch()
        self.engine.launch.assert_called_once_with("org.example.App")
        self.restore.assert_called_once_with("org.example.App")
        query.assert_not_called()

    def test_lost_window_is_relaunched_after_generation_change(self):
        self.restore.side_effect = [False, True]
        with patch.object(recovery, "_session_identity", side_effect=["old", "new"]), \
             patch.object(recovery, "_recovery_state", return_value="active"):
            self.launch()
        self.assertEqual(self.engine.launch.call_count, 2)
        self.assertEqual(self.restore.call_count, 2)

    def test_real_app_error_in_unchanged_runtime_is_not_retried(self):
        self.engine.launch.side_effect = RuntimeUnavailableError("app error")
        with patch.object(recovery, "_session_identity", return_value="same"), \
             patch.object(recovery, "_recovery_state", return_value="active"):
            with self.assertRaisesRegex(RuntimeUnavailableError, "app error"):
                self.launch()
        self.engine.launch.assert_called_once()
        self.restore.assert_not_called()

    def test_a_second_failure_is_returned_without_another_replay(self):
        self.engine.launch.side_effect = [RuntimeUnavailableError("restart"),
                                          RuntimeUnavailableError("still broken")]
        with patch.object(recovery, "_session_identity", return_value="old"), \
             patch.object(recovery, "recovered_since", return_value=True) as recovered:
            with self.assertRaisesRegex(RuntimeUnavailableError, "still broken"):
                self.launch()
        self.assertEqual(self.engine.launch.call_count, 2)
        recovered.assert_called_once()

    def test_missing_window_without_recovery_is_not_a_reason_to_relaunch(self):
        self.restore.return_value = False
        with patch.object(recovery, "_session_identity", return_value="same"), \
             patch.object(recovery, "_recovery_state", return_value="active"):
            self.launch()
        self.engine.launch.assert_called_once()

    def test_alternative_engines_keep_their_existing_launch_path(self):
        self.engine.executable = "alternative-engine"
        with patch.object(recovery, "_session_identity") as identity:
            self.launch()
        identity.assert_not_called()
        self.engine.launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
