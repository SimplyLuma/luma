# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from luma_darkroom.library_metadata import MemoryMetadataStore, MetadataStore


class MetadataStoreTests(unittest.TestCase):
    def test_two_writers_preserve_other_fields_and_first_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            first, second = MetadataStore(path), MetadataStore(path)
            first.update("photo-1", {"stars": 4, "keywords": ["terrace"]})
            baseline = first.backup.read_bytes()
            second.update("photo-1", {"flag": 1})
            self.assertEqual(first.get("photo-1"), {"stars": 4, "keywords": ["terrace"], "flag": 1})
            self.assertEqual(first.backup.read_bytes(), baseline)
            self.assertEqual(json.loads(baseline)["photos"], {})
            self.assertEqual(len(first.all()), 1)

    def test_existing_metadata_is_backed_up_before_first_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            original = {"version": 1, "photos": {"photo-1": {"stars": 5, "flag": 1}}}
            path.write_text(json.dumps(original), encoding="utf-8")
            store = MetadataStore(path)
            store.update("photo-1", {"label": "red"})
            self.assertEqual(json.loads(store.backup.read_text()), original)
            self.assertEqual(store.get("photo-1"), {"stars": 5, "flag": 1, "label": "red"})

    def test_invalid_and_unsupported_changes_never_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "library.json")
            for changes in ({"stars": 6}, {"flag": 2}, {"source_path": "/home/nick/photo.jpg"}):
                with self.assertRaises(ValueError):
                    store.update("photo-1", changes)
            self.assertFalse(store.path.exists())
            self.assertFalse(store.backup.exists())

    def test_fixture_store_has_no_file_and_returns_copies(self) -> None:
        store = MemoryMetadataStore()
        changed = store.update("photo-1", {"stars": 3})
        changed["stars"] = 0
        self.assertEqual(store.get("photo-1")["stars"], 3)


if __name__ == "__main__":
    unittest.main()
