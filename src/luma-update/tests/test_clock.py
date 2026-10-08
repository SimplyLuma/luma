# SPDX-License-Identifier: Apache-2.0
"""A clock that once ran ahead never stops automatic checks or the weekly check-in.

Regression test for the independent review's engine scenario C: a check made
while the clock was a year ahead recorded last_attempt in the future, and
every automatic check was then skipped as "less than an hour ago" until the
real date passed it.
"""

import json
import unittest

import fakes
from fakes import NOW, graph_doc, release
from luma_update import reporting
from luma_update.state import StateStore

DAY = 86400


class ClockRanAhead(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()

    def tearDown(self):
        self.rig.close()

    def publish(self):
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2, released=NOW, importance="security")],
                                   generated=self.rig.now - 60))

    def test_automatic_checks_resume_when_the_clock_goes_back(self):
        self.rig.publish(graph_doc([release("1.0.0", 1)]))
        self.rig.now = NOW + 365 * DAY          # bad RTC or a manual date change
        engine = self.rig.engine()
        engine.automatic()
        self.assertEqual(engine.status().last_error_class, "stale-graph")
        state = json.loads(self.rig.paths.state_file.read_text())
        self.assertEqual(state["last_attempt"], int(NOW + 365 * DAY))
        for days in (1, 30, 180):
            self.rig.now = NOW + days * DAY    # NTP corrected the clock; a security release arrives
            self.publish()
            engine = self.rig.engine()
            engine.automatic()
            self.assertEqual(engine.status().staged_version, "1.0.1", f"+{days} days")
            self.assertEqual(engine.status().last_check, int(self.rig.now))
            # The next check an hour later is still spaced normally.
            self.rig.backend.list = [d for d in self.rig.backend.list if not d.staged]
            state = json.loads(self.rig.paths.state_file.read_text())
            state["pending"] = None
            self.rig.paths.state_file.write_text(json.dumps(state))

    def test_spacing_still_applies_to_a_normal_clock(self):
        self.publish()
        engine = self.rig.engine()
        engine.automatic()
        attempts = json.loads(self.rig.paths.state_file.read_text())["last_attempt"]
        self.rig.now += 600
        engine.automatic()
        self.assertEqual(json.loads(self.rig.paths.state_file.read_text())["last_attempt"], attempts)

    def test_weekly_check_in_resumes_after_a_future_window(self):
        store = StateStore(self.rig.paths.state_file)
        future = NOW + 365 * DAY
        with store.locked():
            self.assertIsNotNone(reporting.countme_payload(store, channel="stable", version="1.0.0", arch="x86_64",
                                                           now=future))
            store.data["countme"]["counted_window"] = reporting.window_start(future)
        with store.locked():
            payload = reporting.countme_payload(store, channel="stable", version="1.0.0", arch="x86_64", now=NOW)
        self.assertEqual(payload, {"channel": "stable", "version": "1.0.0", "arch": "x86_64", "countme_bucket": 1})
        with store.locked():
            store.data["countme"]["counted_window"] = reporting.window_start(NOW)
            self.assertIsNone(reporting.countme_payload(store, channel="stable", version="1.0.0", arch="x86_64",
                                                        now=NOW + 3600))

    def test_decision_from_the_future_is_not_reused(self):
        self.publish()
        engine = self.rig.engine()
        self.rig.now = NOW + 365 * DAY
        engine._decision_at = self.rig.now
        engine._decision = None
        self.rig.now = NOW
        engine.check()
        engine._decision_at = NOW + 365 * DAY  # as if decided while the clock was ahead
        self.rig.publish(graph_doc([release("1.0.0", 1)], generated=NOW))  # the release was pulled since
        self.assertFalse(engine.download(user_initiated=True))
        self.assertEqual(self.rig.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
