# SPDX-License-Identifier: Apache-2.0
"""The local hierarchy and sync conflict guard share the same library."""
from pathlib import Path
import tempfile
import unittest

from prairie_apps.notes_backend import NoteChangedError, NotesStore


class CombinedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "notes.sqlite3"
        self.store = NotesStore(self.path)
        self.parent = self.store.create_note(title="Parent")
        self.child = self.store.create_note(title="Child")
        self.store.move_note(self.child.id, None, parent_id=self.parent.id)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_conflict_copy_is_kept_once_without_losing_the_hierarchy(self):
        copy = self.store.keep_other_copy("child|remote-version", title="Child (other copy)",
                                          body="remote text", runs=(), folder_id=None)
        self.assertIsNotNone(copy)
        self.assertIsNone(self.store.keep_other_copy("child|remote-version", title="Child (other copy)",
                                                   body="remote text", runs=(), folder_id=None))
        self.store.close()
        self.store = NotesStore(self.path)
        self.assertEqual(self.store.parent_of(self.child.id), self.parent.id)
        self.assertTrue(self.store.has_kept_version("child|remote-version"))
        self.assertEqual(len(self.store.list_notes()), 3)

    def test_a_stale_pull_cannot_overwrite_a_nested_note(self):
        before = self.store.get_note(self.child.id)
        self.store.update_note(before.id, title=before.title, body="local edit", runs=())
        with self.assertRaises(NoteChangedError):
            self.store.put_synced_note(before.id, title=before.title, body="remote edit", runs=(),
                                       folder_id=None, folder_name="", favorite=False,
                                       created_at=before.created_at, modified_at=before.modified_at,
                                       expected=before)
        self.assertEqual(self.store.get_note(before.id).body, "local edit")
        self.assertEqual(self.store.parent_of(before.id), self.parent.id)

    def test_external_root_order_changes_are_visible_to_the_open_connection(self):
        folder = self.store.create_folder("Folder")
        version = self.store.data_version()
        remote = NotesStore(self.path)
        try:
            remote.place_root("folder", folder.id, "note:" + self.parent.id)
        finally:
            remote.close()
        self.assertNotEqual(self.store.data_version(), version)
        self.assertEqual(self.store.root_items()[0].id, folder.id)


if __name__ == "__main__":
    unittest.main()
