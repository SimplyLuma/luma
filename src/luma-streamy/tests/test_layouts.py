# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from luma_streamy.layouts import PRESETS, TILES, LayoutStore, split, valid


def leaves(node):
    return [node] if isinstance(node, str) else leaves(node["start"]) + leaves(node["end"])


class LayoutTest(unittest.TestCase):
    def test_presets_hold_every_tile_once(self):
        for _label, layout in PRESETS.values():
            self.assertEqual(sorted(leaves(layout)), sorted(TILES))
            self.assertEqual(valid(layout), layout)

    def test_valid(self):
        self.assertEqual(valid(None), PRESETS["default"][1])
        self.assertEqual(valid("junk"), PRESETS["default"][1])
        tree = valid(split("v", 7, "audio", split("h", 0.3, "audio", "ghost")))
        self.assertEqual(tree, split("h", 0.75, split("h", 0.75, "audio", "preview"), "widgets"))
        self.assertEqual(valid(split("x", 0.5, "audio", "preview")), PRESETS["default"][1])
        # A layout from before the Widgets inset: Widgets joins Audio; a moved seam keeps its size.
        old = {**split("v", 0.7, "preview", "audio"), "size": 180}
        self.assertEqual(valid(old), {**split("v", 0.7, "preview", split("h", 0.5, "widgets", "audio")), "size": 180})

    def test_tab_groups_are_kept(self):
        tree = split("v", 0.7, "preview", {"tabs": ["widgets", "audio", "widgets", "nope"], "current": "audio"})
        self.assertEqual(valid(tree), split("v", 0.7, "preview", {"tabs": ["widgets", "audio"], "current": "audio"}))
        # One tab left is that tile; the missing one joins.
        self.assertEqual(valid(split("v", 0.7, "preview", {"tabs": ["widgets"], "current": "widgets"})),
                         split("h", 0.75, split("v", 0.7, "preview", "widgets"), "audio"))

    def test_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layouts.json"
            store = LayoutStore(path)
            self.assertEqual(store.current, PRESETS["default"][1])
            mine = split("h", 0.6, "audio", split("v", 0.5, "preview", "widgets"))
            store.remember(mine)
            key = store.save_as("Talk show", split("v", 0.8, "preview", split("h", 0.5, "audio", "widgets")))
            self.assertEqual(key, "saved:Talk show")
            with self.assertRaises(ValueError):
                store.save_as("compact", mine)
            with self.assertRaises(ValueError):
                store.save_as(" ", mine)
            again = LayoutStore(path)
            self.assertEqual(again.current, mine)
            self.assertEqual(again.get(key)["ratio"], 0.8)
            self.assertEqual([label for _k, label in again.names()],
                             ["Default", "Insets beside", "Compact", "Talk show"])
            preset = again.get("side")
            preset["ratio"] = 0.1
            self.assertEqual(again.get("side")["ratio"], 0.72)  # presets are copied, never changed
            again.forget("Talk show")
            self.assertEqual(len(LayoutStore(path).names()), 3)
            with self.assertRaises(KeyError):
                again.get("saved:Talk show")
            path.write_text("[1, 2]")
            self.assertEqual(LayoutStore(path).current, PRESETS["default"][1])


if __name__ == "__main__":
    unittest.main()
