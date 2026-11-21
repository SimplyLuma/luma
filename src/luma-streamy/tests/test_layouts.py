# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from luma_streamy.layouts import CLOSED_AT_FIRST, INSPECTOR_SHARE, PRESETS, TILES, LayoutStore, split, valid


def leaves(node):
    if isinstance(node, dict) and "layout" in node:
        node = node["layout"]
    if isinstance(node, str):
        return [node]
    if "tabs" in node:
        return list(node["tabs"])
    return leaves(node["start"]) + leaves(node["end"])


def tree(layout):
    return layout["layout"] if isinstance(layout, dict) and "layout" in layout else layout


OPEN = sorted(set(TILES) - set(CLOSED_AT_FIRST))
BOTH = split("h", 0.7, "landscape", "vertical")


def right(node):
    """``node`` with the Inspector at the right, as a layout from before it gets it."""
    return split("h", INSPECTOR_SHARE, node, "inspector")


class LayoutTest(unittest.TestCase):
    def test_presets_hold_every_open_tile_once(self):
        for _label, layout in PRESETS.values():
            self.assertEqual(sorted(leaves(layout)), OPEN)
            self.assertEqual(tree(valid(layout)), layout)
            self.assertEqual(sorted(valid(layout)["closed"]), sorted(CLOSED_AT_FIRST))

    def test_valid(self):
        self.assertEqual(tree(valid(None)), PRESETS["default"][1])
        self.assertEqual(tree(valid("junk")), PRESETS["default"][1])
        self.assertEqual(sorted(leaves(valid(split("v", 7, "audio", split("h", 0.3, "audio", "ghost"))))), OPEN)
        # From before two canvases, Widgets and Destinations: "preview" is Landscape, Vertical opens to its
        # right, Widgets and Destinations join Audio, the Inspector takes the right edge; a moved seam
        # keeps its size.
        old = {**split("v", 0.7, "preview", "audio"), "size": 180}
        self.assertEqual(tree(valid(old)), right({**split("v", 0.7, BOTH, split(
            "h", 0.5, {"tabs": ["widgets", "destinations"], "current": "widgets"}, "audio")), "size": 180}))

    def test_closed_tiles_stay_closed(self):
        base = split("v", 0.7, "landscape", split("h", 0.5, "widgets", split("h", 0.5, "audio", "destinations")))
        closed = {"layout": base, "closed": {"vertical": {"beside": "landscape", "edge": "right", "share": 0.3}}}
        got = valid(closed)
        self.assertEqual(got["layout"], right(base))
        self.assertEqual(got["closed"]["vertical"]["edge"], "right")
        # Both previews closed: Landscape opens again; an open tile is never also closed.
        none = {"layout": split("h", 0.5, "widgets", "audio"), "closed": {"landscape": {}, "vertical": {}}}
        self.assertNotIn("landscape", valid(none)["closed"])
        self.assertIn("landscape", leaves(valid(none)))
        opened = valid({"layout": split("h", 0.5, base, "stats"), "closed": {"stats": {}, "landscape": {}}})
        self.assertNotIn("stats", opened["closed"])
        self.assertIn("landscape", leaves(opened))

    def test_tab_groups_are_kept(self):
        t = split("v", 0.7, BOTH, {"tabs": ["widgets", "audio", "destinations", "widgets", "nope"],
                                   "current": "audio"})
        self.assertEqual(tree(valid(t)), right(split("v", 0.7, BOTH, {"tabs": ["widgets", "audio", "destinations"],
                                                                      "current": "audio"})))

    def test_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layouts.json"
            store = LayoutStore(path)
            self.assertEqual(tree(store.current), PRESETS["default"][1])
            mine = right(split("h", 0.6, "audio", split("v", 0.5, BOTH, split("h", 0.5, "widgets", "destinations"))))
            store.remember(mine)
            key = store.save_as("Talk show", split("v", 0.8, "landscape", split("h", 0.5, "audio", split(
                "h", 0.5, "widgets", split("h", 0.5, "vertical", "destinations")))))
            self.assertEqual(key, "saved:Talk show")
            with self.assertRaises(ValueError):
                store.save_as("compact", mine)
            with self.assertRaises(ValueError):
                store.save_as(" ", mine)
            again = LayoutStore(path)
            self.assertEqual(tree(again.current), mine)
            self.assertEqual(tree(again.get(key))["start"]["ratio"], 0.8)  # the Inspector joined at the right
            self.assertEqual([label for _k, label in again.names()],
                             ["Default", "Insets beside", "Compact", "Talk show"])
            preset = again.get("side")
            preset["ratio"] = 0.1
            self.assertEqual(again.get("side")["start"]["ratio"], 0.72)  # presets are copied, never changed
            again.forget("Talk show")
            self.assertEqual(len(LayoutStore(path).names()), 3)
            with self.assertRaises(KeyError):
                again.get("saved:Talk show")
            path.write_text("[1, 2]")
            self.assertEqual(tree(LayoutStore(path).current), PRESETS["default"][1])


if __name__ == "__main__":
    unittest.main()
