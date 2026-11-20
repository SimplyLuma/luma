# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from luma_streamy.layouts import PRESETS, LayoutStore, valid


class LayoutTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(valid([("audio", "left", True), ("audio", "right", False), ("nope", "left", False),
                                ("widgets", "top", False), "junk"]),
                         [("audio", "left", True), ("widgets", "left", False), ("inspector", "right", True)])
        for _label, layout in PRESETS.values():
            self.assertEqual(sorted(k for k, _s, _f in valid(layout)), ["audio", "inspector", "widgets"])

    def test_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layouts.json"
            store = LayoutStore(path)
            self.assertEqual(store.current, PRESETS["default"][1])
            store.remember([("inspector", "left", False)])
            key = store.save_as("Talk show", [("audio", "bottom", True)])
            self.assertEqual(key, "saved:Talk show")
            with self.assertRaises(ValueError):
                store.save_as("compact", [])
            with self.assertRaises(ValueError):
                store.save_as(" ", [])
            again = LayoutStore(path)
            self.assertEqual(again.current[0], ("inspector", "left", False))
            self.assertEqual(again.get(key)[0], ("audio", "bottom", True))
            self.assertEqual([label for _k, label in again.names()], ["Default", "Compact", "Studio", "Talk show"])
            again.forget("Talk show")
            self.assertEqual(len(LayoutStore(path).names()), 3)
            with self.assertRaises(KeyError):
                again.get("saved:Talk show")
            path.write_text("[1, 2]")
            self.assertEqual(LayoutStore(path).current, PRESETS["default"][1])


if __name__ == "__main__":
    unittest.main()
