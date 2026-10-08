"""Maps' existing favourites and recents only edit their own latest JSON list."""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-maps"))

from luma_maps.store import Place, PlaceStore  # noqa: E402


class MapsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"XDG_DATA_HOME": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.path = pathlib.Path(self.temp.name) / "luma-maps/places.json"
        self.path.parent.mkdir()
        self.place = Place("Duende", "Restaurant", "468 19th St", 37.8, -122.27)

    def test_save_preserves_latest_recents_and_unknown_fields(self):
        self.path.write_text(json.dumps({"saved": [], "recents": [], "future": {"keep": True}}))
        store = PlaceStore()
        self.path.write_text(json.dumps({"saved": [], "recents": [{"name": "Later", "latitude": 1,
                                "longitude": 2, "extra": "preserve"}], "future": {"keep": True}}))
        store.save(self.place)
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["future"], {"keep": True})
        self.assertEqual(payload["recents"][0]["extra"], "preserve")
        self.assertEqual(payload["saved"][0]["name"], "Duende")
        self.assertTrue(self.path.with_name("places.json.bak-before-lumaui").exists())

    def test_corrupt_store_is_never_replaced(self):
        self.path.write_text("{broken")
        store = PlaceStore()
        with self.assertRaises(json.JSONDecodeError):
            store.save(self.place)
        self.assertEqual(self.path.read_text(), "{broken")

    def test_remember_keeps_unknown_fields_on_reordered_place(self):
        recent = {"name": "Duende", "kind": "Restaurant", "address": "old address",
                  "latitude": 37.8, "longitude": -122.27, "future_note": "keep"}
        self.path.write_text(json.dumps({"saved": [], "recents": [{"name": "Another"}, recent]}))
        store = PlaceStore()
        store.remember(self.place)
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["recents"][0], recent)

    def test_clear_commands_only_change_the_requested_list(self):
        saved = {"name": "Saved", "future_note": "keep"}
        recent = {"name": "Recent", "future_note": "keep"}
        self.path.write_text(json.dumps({"saved": [saved], "recents": [recent],
                                         "future": {"keep": True}}))
        store = PlaceStore()
        store.clear_recents()
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload, {"saved": [saved], "recents": [], "future": {"keep": True}})
        store.clear_saved()
        self.assertEqual(json.loads(self.path.read_text()),
                         {"saved": [], "recents": [], "future": {"keep": True}})

    def test_manual_view_preserves_latest_places_and_future_data(self):
        self.path.write_text(json.dumps({"saved": [], "recents": [], "future": "keep"}))
        store = PlaceStore()
        self.path.write_text(json.dumps({"saved": [{"future": "later"}], "recents": [], "future": "keep"}))
        store.remember_viewport(41.8, -87.6, 12)
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["saved"], [{"future": "later"}])
        self.assertEqual(payload["future"], "keep")
        self.assertEqual(PlaceStore().viewport, (41.8, -87.6, 12))

    def test_bad_viewport_is_not_loaded_or_written(self):
        self.path.write_text(json.dumps({"viewport": [100, 0, 12]}))
        store = PlaceStore()
        self.assertIsNone(store.viewport)
        before = self.path.read_text()
        store.remember_viewport(float('nan'), 0, 12)
        self.assertEqual(self.path.read_text(), before)
        self.path.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            store.remember_viewport(0, 0, 12)
        self.assertEqual(self.path.read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
