# SPDX-License-Identifier: Apache-2.0
"""Fixtures must load the full sample and never open a user's library."""
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from prairie_apps.notes_fixture import DocumentParser, FixtureStore, source_from_environment

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/notes-v70.json"


class FixtureTests(unittest.TestCase):
    def test_full_sample_loads_without_creating_directories_or_a_disk_database(self):
        original = sqlite3.connect
        with patch("pathlib.Path.mkdir", side_effect=AssertionError("fixture mkdir")), \
                patch("prairie_apps.notes_fixture.sqlite3.connect", wraps=original) as connect, \
                patch("prairie_apps.notes_backend.notes_data_directory", side_effect=AssertionError("real store")):
            store = source_from_environment({"LUMA_NOTES_FIXTURE": str(FIXTURE)})
            try:
                connect.assert_called_once_with(":memory:")
                self.assertEqual(len(store.list_notes()), 11)
                self.assertEqual(len(store.list_folders()), 5)
                self.assertEqual(store.get_note(store.selected).title, "Launch checklist")
                self.assertEqual(store.parent_of("8"), "1")
                self.assertEqual(store.parent_of("10"), "4")
                self.assertEqual(store.folder_meta["press"]["parent"], "launch")
                self.assertEqual(store.raw_notes["1"]["live"], "PR")
                self.assertIn("class=\"done\"", store.raw_notes["1"]["html"])
                self.assertEqual(len(store.picture_paths), 1)
                self.assertTrue(all(path.is_file() for path in store.picture_paths.values()))
            finally:
                store.close()

    def test_changes_are_disposable_and_the_fixture_file_is_unchanged(self):
        before = FIXTURE.read_bytes()
        store = FixtureStore(FIXTURE)
        store.update_note("1", title="Changed", body="Only in memory", runs=())
        store.soft_delete_note("2")
        store.create_note(title="Disposable")
        store.close()
        reopened = FixtureStore(FIXTURE)
        try:
            self.assertEqual(reopened.get_note("1").title, "Launch checklist")
            self.assertIsNone(reopened.get_note("2").deleted_at)
            self.assertEqual(len(reopened.list_notes()), 11)
            self.assertEqual(FIXTURE.read_bytes(), before)
        finally:
            reopened.close()

    def test_invalid_fixture_never_falls_back_to_real_data(self):
        with patch("prairie_apps.notes_fixture.NotesStore", side_effect=AssertionError("real store")):
            with self.assertRaises(FileNotFoundError):
                source_from_environment({"LUMA_NOTES_FIXTURE": str(FIXTURE.parent / "missing.json")})

    def test_image_paths_cannot_escape_the_fixture(self):
        parser = DocumentParser(FIXTURE.parent)
        with self.assertRaises(ValueError):
            parser.feed('<img src="../../outside.webp">')


if __name__ == "__main__":
    unittest.main()
