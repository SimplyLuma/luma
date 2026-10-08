#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))
from prairie_apps.notes_backend import create_note, delete_note, list_notes, save_note


class NotesBackendTests(unittest.TestCase):
    def test_note_is_portable_private_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Notes"
            note = create_note(root)
            saved = save_note(note.path, "# Field notes\n\nA portable note.\n", root)
            self.assertEqual(saved.title, "Field notes")
            self.assertEqual(list_notes(root)[0].preview, "A portable note.")
            self.assertEqual(stat.S_IMODE(saved.path.stat().st_mode), 0o600)

    def test_save_rejects_paths_outside_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Notes"
            outside = Path(directory) / "outside.md"
            outside.write_text("no", encoding="utf-8")
            with self.assertRaises(ValueError):
                save_note(outside, "changed", root)

    def test_search_and_recoverable_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Notes"
            first = create_note(root); save_note(first.path, "# Garden\n\nTomatoes\n", root)
            second = create_note(root); save_note(second.path, "# Office\n\nMeeting\n", root)
            self.assertEqual([note.title for note in list_notes(root, "tomato")], ["Garden"])
            trashed = delete_note(first.path, root)
            self.assertFalse(first.path.exists()); self.assertTrue(trashed.exists())
            self.assertEqual([note.title for note in list_notes(root)], ["Office"])


if __name__ == "__main__": unittest.main()
