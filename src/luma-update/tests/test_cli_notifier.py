# SPDX-License-Identifier: Apache-2.0
import io
import json
import os
from contextlib import redirect_stdout
import tempfile
import unittest
from unittest import mock

import fakes  # noqa: F401
from luma_update import cli, notifier
from luma_update.status import Status


class CliStatus(unittest.TestCase):
    def test_status_json_falls_back_to_published_file(self):
        with tempfile.TemporaryDirectory() as root:
            status = Status(state="staged", channel="stable", booted_version="1.0.0", staged_version="1.0.1",
                            staged_commit="a" * 64, importance="security", managed=True)
            path = os.path.join(root, "run/luma-update/status.json")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as stream:
                stream.write(status.to_json())
            with mock.patch.dict(os.environ, {"LUMA_UPDATE_ROOT": root}), \
                    mock.patch.object(cli, "Client", side_effect=RuntimeError("no bus")):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(cli.main(["status", "--json"]), 0)
                data = json.loads(out.getvalue())
                self.assertEqual((data["state"], data["staged_version"], data["schema_version"]), ("staged", "1.0.1", 1))
                out = io.StringIO()
                with redirect_stdout(out):
                    cli.main(["status"])
                # An agent too old to publish names: named by the same rules, never "Luma 1.0.1".
                self.assertIn("Luma (Version 1.0.1, Prairie) is ready. Restart to finish updating", out.getvalue())
                self.assertIn("Important security update.", out.getvalue())

    def test_unavailable_exit_code(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, {"LUMA_UPDATE_ROOT": root}), \
                mock.patch.object(cli, "Client", side_effect=RuntimeError("no bus")):
            self.assertEqual(cli.main(["status", "--json"]), cli.EXIT_UNAVAILABLE)

    def test_dbus_properties_map_to_json_names(self):
        data = cli._status_dict({"State": "idle", "BootedVersion": "1.0.0", "AvailableChannels": ["stable"]})
        self.assertEqual(data, {"state": "idle", "booted_version": "1.0.0", "available_channels": ["stable"],
                                "schema_version": 1})


class NotifierDecisions(unittest.TestCase):
    def staged(self, importance="normal", commit="c" * 64):
        return {"state": "staged", "staged_version": "1.0.1", "staged_commit": commit, "importance": importance}

    def test_first_notification_then_daily_reminder(self):
        state = notifier.NotifierState()
        plan, state = notifier.decide(self.staged(), state, 1000.0)
        self.assertTrue(plan.show_ready)
        self.assertEqual((plan.title, plan.body), ("Luma (Version 1.0.1, Prairie) is ready", "Restart to finish updating."))
        plan, state = notifier.decide(self.staged(), state, 1000.0 + 3600)
        self.assertFalse(plan.show_ready)
        plan, state = notifier.decide(self.staged(), state, 1000.0 + notifier.REMIND_NORMAL)
        self.assertTrue(plan.show_ready)

    def test_security_reminds_every_four_hours(self):
        plan, state = notifier.decide(self.staged("security"), notifier.NotifierState(), 0.0)
        self.assertEqual(plan.title, "Important security update")
        self.assertIn("Restart to finish updating", plan.body)
        plan, state = notifier.decide(self.staged("security"), state, notifier.REMIND_SECURITY - 1)
        self.assertFalse(plan.show_ready)
        plan, state = notifier.decide(self.staged("security"), state, notifier.REMIND_SECURITY)
        self.assertTrue(plan.show_ready)

    def test_later_snoozes_and_new_update_notifies_at_once(self):
        _, state = notifier.decide(self.staged(), notifier.NotifierState(), 0.0)
        # Later, pressed after 20 hours: the reminder waits a full day from then.
        state.ready_shown_at = 20 * 3600
        state.snoozed_until = 20 * 3600 + notifier.REMIND_NORMAL
        plan, state = notifier.decide(self.staged(), state, 30 * 3600)
        self.assertFalse(plan.show_ready)
        plan, state = notifier.decide(self.staged(commit="d" * 64), state, 30 * 3600)
        self.assertTrue(plan.show_ready)

    def test_clock_that_ran_ahead_does_not_silence_reminders(self):
        now = 1_000_000.0
        _, state = notifier.decide(self.staged(), notifier.NotifierState(), now + 365 * 86400)
        state.snoozed_until = now + 366 * 86400  # Later pressed while the clock was a year ahead
        plan, state = notifier.decide(self.staged(), state, now)
        self.assertTrue(plan.show_ready)
        plan, state = notifier.decide(self.staged(), state, now + 3600)
        self.assertFalse(plan.show_ready)

    def test_withdraw_when_no_longer_staged_and_rollback_once(self):
        _, state = notifier.decide(self.staged(), notifier.NotifierState(), 0.0)
        status = {"state": "idle", "rolled_back_version": "1.0.1", "rolled_back_at": 1234}
        plan, state = notifier.decide(status, state, 10.0)
        self.assertTrue(plan.withdraw_ready)
        self.assertTrue(plan.show_rollback)
        plan, state = notifier.decide(status, state, 20.0)
        self.assertFalse(plan.show_rollback)
        self.assertFalse(plan.withdraw_ready)


if __name__ == "__main__":
    unittest.main()
