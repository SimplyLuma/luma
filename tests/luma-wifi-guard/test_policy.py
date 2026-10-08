# SPDX-License-Identifier: Apache-2.0
import json
import stat
import tempfile
import unittest
from pathlib import Path

from luma_wifi_guard.policy import BASE_SECONDS, MAX_SECONDS, Avoidance, lease_since, network_ssid

GOOD, BAD, OTHER = "22:0b:8b:90:cc:4f", "74:83:c2:d1:23:90", "1c:0b:8b:90:cc:4d"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class AvoidanceTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.avoidance = Avoidance(clock=self.clock)

    def test_a_failure_is_avoided_only_when_another_access_point_is_proven(self):
        self.assertEqual(self.avoidance.failed("Home", BAD, {GOOD, BAD}), 0)
        self.avoidance.succeeded("Home", GOOD)
        self.assertEqual(self.avoidance.failed("Home", BAD, {GOOD, BAD}), BASE_SECONDS)
        self.assertEqual(self.avoidance.avoided("Home"), {BAD})

    def test_the_last_visible_access_point_is_never_avoided(self):
        self.avoidance.succeeded("Home", GOOD)
        self.assertEqual(self.avoidance.failed("Home", BAD, {BAD}), 0)
        self.assertEqual(self.avoidance.avoided("Home"), set())

    def test_repeated_failures_double_up_to_a_day_and_expire(self):
        self.avoidance.succeeded("Home", GOOD)
        durations = []
        for _ in range(12):
            durations.append(self.avoidance.failed("Home", BAD, {GOOD, BAD}))
        self.assertEqual(durations[:3], [BASE_SECONDS, 2 * BASE_SECONDS, 4 * BASE_SECONDS])
        self.assertEqual(durations[-1], MAX_SECONDS)
        self.clock.now += MAX_SECONDS + 1
        self.assertEqual(self.avoidance.avoided("Home"), set())

    def test_a_success_lifts_the_avoidance(self):
        self.avoidance.succeeded("Home", GOOD)
        self.avoidance.failed("Home", BAD, {GOOD, BAD})
        self.assertTrue(self.avoidance.succeeded("Home", BAD))
        self.assertEqual(self.avoidance.avoided("Home"), set())

    def test_networks_are_separate(self):
        self.avoidance.succeeded("Home", GOOD)
        self.avoidance.failed("Home", BAD, {GOOD, BAD})
        self.assertEqual(self.avoidance.avoided("Office"), set())
        self.assertEqual(self.avoidance.failed("Office", BAD, {GOOD, BAD}), 0)

    def test_stale_knowledge_is_forgotten(self):
        self.avoidance.succeeded("Home", GOOD)
        self.clock.now += 31 * 24 * 3600
        self.avoidance.forget_stale()
        self.assertEqual(self.avoidance.failed("Home", BAD, {GOOD, BAD}), 0)

    def test_state_is_private_and_survives_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "avoidance.json"
            first = Avoidance(path, clock=self.clock)
            first.succeeded("Home", GOOD)
            first.failed("Home", BAD, {GOOD, BAD, OTHER})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(Avoidance(path, clock=self.clock).avoided("Home"), {BAD})
            path.write_text("not json")
            self.assertEqual(Avoidance(path, clock=self.clock).avoided("Home"), set())
            path.write_text(json.dumps({"schema_version": 99, "networks": {"Home": {}}}))
            self.assertEqual(Avoidance(path, clock=self.clock).networks, {})


class ParsingTest(unittest.TestCase):
    def test_a_lease_counts_only_if_it_began_after_joining(self):
        options = {"expiry": "1789519465", "dhcp_lease_time": "86400"}
        began = 1789519465 - 86400
        self.assertTrue(lease_since(options, began))
        self.assertTrue(lease_since(options, began + 1))
        self.assertFalse(lease_since(options, began + 5))
        self.assertFalse(lease_since({}, began))
        self.assertFalse(lease_since({"expiry": "x", "dhcp_lease_time": "1"}, began))

    def test_supplicant_ssids(self):
        self.assertEqual(network_ssid('"Hands Across"'), "Hands Across")
        self.assertEqual(network_ssid("486f6d65"), "Home")


if __name__ == "__main__":
    unittest.main()
