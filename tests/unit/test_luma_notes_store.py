#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))

from prairie_apps.notes_backend import NotesStore  # noqa: E402


class NotesStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = NotesStore(Path(self.temporary.name) / "notes.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_note_lifecycle_and_rich_runs_are_durable(self) -> None:
        note = self.store.create_note(title="Field notes")
        note = self.store.update_note(
            note.id,
            title="Field notes",
            body="Prairie ships quietly.",
            runs=({"start": 0, "end": 7, "style": "bold"},),
        )
        self.assertEqual(note.preview, "Prairie ships quietly.")
        self.assertEqual(note.runs[0]["style"], "bold")
        self.assertEqual(self.store.list_notes(search="quietly")[0].id, note.id)

        self.store.soft_delete_note(note.id)
        self.assertEqual(self.store.list_notes(), ())
        self.assertEqual(self.store.list_notes(deleted=True)[0].id, note.id)
        self.store.restore_note(note.id)
        self.assertEqual(self.store.list_notes()[0].id, note.id)

    def test_folders_favorites_and_expansion_are_real_state(self) -> None:
        folder = self.store.create_folder("Release")
        note = self.store.create_note(folder_id=folder.id, title="Checklist")
        self.store.set_folder_favorite(folder.id, True)
        self.store.set_folder_expanded(folder.id, False)
        self.store.set_note_favorite(note.id, True)
        self.assertTrue(self.store.get_folder(folder.id).favorite)
        self.assertFalse(self.store.get_folder(folder.id).expanded)
        self.assertTrue(self.store.get_note(note.id).favorite)

    def test_reorder_persists_authoritative_order(self) -> None:
        first = self.store.create_note(title="First")
        second = self.store.create_note(title="Second")
        third = self.store.create_note(title="Third")
        self.store.reorder_note(third.id, first.id)
        self.assertEqual(
            [note.id for note in self.store.list_notes()],
            [third.id, first.id, second.id],
        )
        self.store.close()
        self.store = NotesStore(Path(self.temporary.name) / "notes.sqlite3")
        self.assertEqual(
            [note.id for note in self.store.list_notes()],
            [third.id, first.id, second.id],
        )

    def test_invalid_rich_run_is_rejected_without_partial_save(self) -> None:
        note = self.store.create_note(title="Safe")
        with self.assertRaises(ValueError):
            self.store.update_note(
                note.id,
                title="Unsafe",
                body="short",
                runs=({"start": 0, "end": 99, "style": "bold"},),
            )
        self.assertEqual(self.store.get_note(note.id).title, "Safe")

    def test_untitled_is_a_placeholder_not_persisted_user_text(self) -> None:
        note = self.store.create_note()
        self.assertEqual(note.title, "")
        self.assertEqual(note.display_title, "Untitled page")

    def test_move_between_folders_and_folder_deletion_are_lossless(self) -> None:
        first = self.store.create_folder("First")
        second = self.store.create_folder("Second")
        note = self.store.create_note(folder_id=first.id, title="Portable")
        moved = self.store.move_note(note.id, second.id)
        self.assertEqual(moved.folder_id, second.id)
        self.store.delete_folder(second.id)
        self.assertIsNone(self.store.get_note(note.id).folder_id)

    def test_reorder_never_crosses_favorite_partition(self) -> None:
        favorite = self.store.create_note(title="Favorite")
        ordinary = self.store.create_note(title="Ordinary")
        self.store.set_note_favorite(favorite.id, True)
        self.store.reorder_note(ordinary.id, favorite.id)
        self.assertEqual(
            [note.id for note in self.store.list_notes()],
            [favorite.id, ordinary.id],
        )


if __name__ == "__main__":
    unittest.main()
