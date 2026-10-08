# SPDX-License-Identifier: MPL-2.0
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

import _paths  # noqa: F401
from luma_audio_devices import routing
from luma_audio_devices.state import MAX_OUTPUTS, PRUNE_AFTER, RememberedReceiver, Store


class Persistence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "luma-audio-devices" / "state.json"

    def tearDown(self):
        self.directory.cleanup()

    def test_round_trip(self):
        store = Store.load(self.path)
        store.policy.devices["bluez:78:C1"] = routing.memory_from_dict({"auto_switch": False, "name": "Buds"})
        store.receivers["0615378145af"] = RememberedReceiver("0615378145af", "Receiver One’s MacBook Pro",
                                                              "0615378145AF@Receiver One’s MacBook Pro",
                                                              "Mac15,9", 5.0, 6.0, True)
        store.save()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        again = Store.load(self.path)
        self.assertFalse(again.policy.devices["bluez:78:C1"].auto_switch)
        self.assertFalse(again.legacy_answers)
        self.assertEqual(again.receivers["0615378145af"].name, "Receiver One’s MacBook Pro")
        self.assertTrue(again.receivers["0615378145af"].has_password)
        self.assertNotIn("password\"", self.path.read_text(encoding="utf-8").replace("has_password", ""))

    def test_legacy_answers_are_rewritten_without_them(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 1, "outputs": {
            "usb:0x17ef:0xa396": {"policy": "never", "snoozed_until": 9.0, "name": "Dock", "last_seen": 5.0}}}),
            encoding="utf-8")
        store = Store.load(self.path)
        self.assertTrue(store.legacy_answers)
        store.save()
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("policy", text)
        self.assertNotIn("snoozed_until", text)
        self.assertFalse(Store.load(self.path).legacy_answers)

    def test_missing_file_is_empty(self):
        store = Store.load(self.path)
        self.assertEqual(store.policy.devices, {})
        self.assertEqual(store.receivers, {})

    def test_unreadable_file_is_moved_aside_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ not json", encoding="utf-8")
        store = Store.load(self.path)
        self.assertEqual(store.policy.devices, {})
        leftovers = [p.name for p in self.path.parent.iterdir()]
        self.assertTrue(any(name.startswith("state.json.unreadable-") for name in leftovers), leftovers)

    def test_newer_version_is_neither_misread_nor_overwritten(self):
        self.path.parent.mkdir(parents=True)
        newer = json.dumps({"version": 99, "outputs": {"x": {"policy": "never"}}})
        self.path.write_text(newer, encoding="utf-8")
        store = Store.load(self.path)
        self.assertEqual(store.policy.devices, {})
        store.policy.devices["y"] = routing.memory_from_dict({})
        store.save()
        self.assertEqual(self.path.read_text(encoding="utf-8"), newer)

    def test_bad_entries_are_tolerated(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 1, "outputs": {"a": {"policy": "sometimes", "last_seen": "x"}},
                                         "airplay": {"b": {"name": 3}}}), encoding="utf-8")
        store = Store.load(self.path)
        self.assertTrue(store.policy.devices["a"].auto_switch)
        self.assertTrue(store.legacy_answers)
        self.assertEqual(store.receivers["b"].name, "3")

    def test_prune_keeps_remembered_choices(self):
        store = Store.load(self.path)
        now = 10 * PRUNE_AFTER
        store.policy.devices["stays-device"] = routing.memory_from_dict({"auto_switch": False, "last_seen": 1.0})
        store.policy.devices["old"] = routing.memory_from_dict({"last_seen": 1.0})
        store.policy.devices["recent"] = routing.memory_from_dict({"last_seen": now - 10})
        store.prune(now)
        self.assertEqual(sorted(store.policy.devices), ["recent", "stays-device"])

    def test_prune_caps_size(self):
        store = Store.load(self.path)
        for index in range(MAX_OUTPUTS + 25):
            store.policy.devices[f"d{index}"] = routing.memory_from_dict({"last_seen": 1000.0 + index})
        store.prune(2000.0)
        self.assertEqual(len(store.policy.devices), MAX_OUTPUTS)
        self.assertNotIn("d0", store.policy.devices)


if __name__ == "__main__":
    unittest.main()
