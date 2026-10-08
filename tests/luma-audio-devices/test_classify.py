# SPDX-License-Identifier: MPL-2.0
import json
import unittest

import _paths  # noqa: F401
from luma_audio_devices import classify


class DeviceKeyVectors(unittest.TestCase):
    """The vectors are shared with the WirePlumber Lua policy (test_lua_policy.py)."""

    def setUp(self):
        self.vectors = json.loads((_paths.FIXTURES / "device-keys.json").read_text(encoding="utf-8"))

    def test_keys(self):
        for vector in self.vectors:
            with self.subTest(vector["case"]):
                self.assertEqual(classify.device_key(vector["props"], vector.get("route")), vector["key"])

    def test_network(self):
        for vector in self.vectors:
            with self.subTest(vector["case"]):
                self.assertEqual(classify.is_network(vector["props"]), vector["network"])

    def test_personal_names_and_icons(self):
        for vector in self.vectors:
            with self.subTest(vector["case"]):
                route = vector.get("route")
                self.assertEqual(classify.is_personal_listening(vector["props"], route), vector["personal"])
                self.assertEqual(classify.display_name(vector["props"], route), vector["name"])
                self.assertEqual(classify.icon_name(vector["props"], route), vector["icon"])

    def test_hidden(self):
        for vector in self.vectors:
            if "hidden" in vector:
                with self.subTest(vector["case"]):
                    self.assertEqual(classify.is_hidden_output(vector["props"]), vector["hidden"])

    def test_network_keys_never_carry_a_resolved_address_for_luma_receivers(self):
        props = {"node.name": "luma_airplay.aabbccddeeff", "luma.airplay.id": "AABBCCDDEEFF",
                 "raop.ip": "192.0.2.40", "node.network": "true"}
        self.assertEqual(classify.device_key(props), "airplay:aabbccddeeff")


class LocalHardware(unittest.TestCase):
    def test_alsa_and_bluez_sinks_are_local(self):
        self.assertTrue(classify.is_local_hardware({"device.api": "alsa", "media.class": "Audio/Sink"}))
        self.assertTrue(classify.is_local_hardware({"device.api": "bluez5", "media.class": "Audio/Sink"}))

    def test_virtual_and_network_sinks_are_not(self):
        self.assertFalse(classify.is_local_hardware({"media.class": "Audio/Sink", "node.virtual": "true"}))
        self.assertFalse(classify.is_local_hardware({"device.api": "alsa", "media.class": "Audio/Sink",
                                                     "node.network": "true"}))
        self.assertFalse(classify.is_local_hardware({"device.api": "alsa", "media.class": "Audio/Source"}))

    def test_describe_reads_priority(self):
        output = classify.describe({"node.name": "n", "device.api": "alsa", "media.class": "Audio/Sink",
                                    "priority.session": "712"})
        self.assertEqual(output.priority, 712)
        self.assertTrue(output.local)


if __name__ == "__main__":
    unittest.main()
