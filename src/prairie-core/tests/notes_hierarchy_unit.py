#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Local sidebar hierarchy: transactional moves, cycles and durable ordering."""
import tempfile
import unittest
from pathlib import Path
from prairie_apps.notes_backend import NotesStore

class HierarchyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'notes.sqlite3'
        self.store = NotesStore(self.path)
        self.a, self.b, self.c = [self.store.create_note(title=t) for t in 'ABC']
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def test_nesting_survives_reopen_and_can_be_undone(self):
        self.store.move_note(self.b.id, None, parent_id=self.a.id)
        self.store.close()
        self.store = NotesStore(self.path)
        self.assertEqual(self.store.parent_of(self.b.id), self.a.id)
        self.store.move_note(self.b.id, None, before_id=self.a.id)
        self.assertIsNone(self.store.parent_of(self.b.id))
        self.assertEqual(self.store.list_notes()[0].id, self.b.id)
    def test_cycles_fail_without_changing_anything(self):
        self.store.move_note(self.b.id, None, parent_id=self.a.id)
        self.store.move_note(self.c.id, None, parent_id=self.b.id)
        for parent in (self.a.id, self.b.id, self.c.id):
            with self.assertRaises(ValueError):
                self.store.move_note(self.a.id, None, parent_id=parent)
        self.assertIsNone(self.store.parent_of(self.a.id))
    def test_subtree_moves_between_folders(self):
        self.store.move_note(self.b.id, None, parent_id=self.a.id)
        f = self.store.create_folder('Folder')
        self.store.move_note(self.a.id, f.id)
        self.assertEqual(self.store.get_note(self.b.id).folder_id, f.id)
        self.assertEqual(self.store.parent_of(self.b.id), self.a.id)
    def test_deleted_parent_does_not_hide_children(self):
        self.store.move_note(self.b.id, None, parent_id=self.a.id)
        self.store.soft_delete_note(self.a.id)
        self.assertIsNone(self.store.parent_of(self.b.id))
        self.assertIn(self.b.id, [n.id for n in self.store.list_notes()])
    def test_reordering_children_preserves_other_scopes(self):
        self.store.move_note(self.b.id, None, parent_id=self.a.id)
        self.store.move_note(self.c.id, None, parent_id=self.a.id)
        order = self.store.get_note(self.a.id).sort_order
        self.store.reorder_note(self.c.id, self.b.id)
        self.assertEqual(self.store.get_note(self.a.id).sort_order, order)
        children = [n.id for n in self.store.list_notes() if self.store.parent_of(n.id) == self.a.id]
        self.assertEqual(children, [self.c.id, self.b.id])
    def test_folder_order_is_durable(self):
        folders = [self.store.create_folder(t) for t in 'XYZ']
        self.store.reorder_folder(folders[0].id, None)
        self.store.close()
        self.store = NotesStore(self.path)
        self.assertEqual([f.id for f in self.store.list_folders()], [f.id for f in folders[1:] + folders[:1]])
    def test_folders_and_notes_share_a_persistent_root_order(self):
        folder = self.store.create_folder('Mixed')
        self.store.place_root('folder', folder.id, 'note:' + self.b.id)
        self.assertEqual([item.id for item in self.store.root_items()],
                         [self.a.id, folder.id, self.b.id, self.c.id])
        self.store.close()
        self.store = NotesStore(self.path)
        self.assertEqual([item.id for item in self.store.root_items()],
                         [self.a.id, folder.id, self.b.id, self.c.id])
    def test_note_can_leave_last_folder_and_follow_it(self):
        folder = self.store.create_folder('Last')
        self.store.move_note(self.a.id, folder.id)
        self.store.place_root('note', self.a.id, None)
        self.assertIsNone(self.store.get_note(self.a.id).folder_id)
        self.assertEqual([item.id for item in self.store.root_items()][-2:], [folder.id, self.a.id])
    def test_hidden_sync_relationship_cannot_form_a_cycle(self):
        self.store.move_note(self.b.id, None, parent_id=self.a.id)
        folder = self.store.create_folder('Remote move')
        with self.store.connection:
            self.store.connection.execute('UPDATE notes SET folder_id=? WHERE id=?',
                                          (folder.id, self.a.id))
        self.assertIsNone(self.store.parent_of(self.b.id))
        with self.assertRaises(ValueError):
            self.store.move_note(self.a.id, None, parent_id=self.b.id)
    def test_failed_move_rolls_back_hierarchy_and_descendants(self):
        self.store.move_note(self.b.id, None, parent_id=self.a.id)
        folder = self.store.create_folder('Target')
        self.store.connection.execute('''CREATE TRIGGER refuse_move BEFORE UPDATE OF folder_id ON notes
            WHEN NEW.id = '%s' BEGIN SELECT RAISE(ABORT, 'test failure'); END''' % self.a.id)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.move_note(self.a.id, folder.id)
        self.assertIsNone(self.store.get_note(self.b.id).folder_id)
        self.assertEqual(self.store.parent_of(self.b.id), self.a.id)

if __name__ == '__main__':
    unittest.main()
