# SPDX-License-Identifier: Apache-2.0
"""New document writes keep a recoverable library and preserve other fields."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prairie_apps.notes_backend import NotesStore
from prairie_apps.notes_export import to_markdown


class NewWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = NotesStore(Path(self.temp.name) / 'notes.sqlite3')
        self.note = self.store.create_note(title='Original')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_first_creation_preserves_existing_rows_and_is_atomic_with_parent(self):
        folder = self.store.create_folder('Launch')
        parent = self.store.create_note(folder_id=folder.id, title='Parent')
        sibling = self.store.create_note(folder_id=folder.id, title='Sibling')
        before = {n.id: n for n in self.store.list_notes()}
        first = self.store.create_note(folder_id=folder.id, first=True)
        newer = self.store.create_note(folder_id=folder.id, first=True)
        self.assertLess(newer.sort_order, first.sort_order)
        self.assertLess(first.sort_order, min(parent.sort_order, sibling.sort_order))
        child = self.store.create_note(folder_id=folder.id, parent_id=parent.id, first=True)
        self.assertEqual(self.store.parent_of(child.id), parent.id)
        self.assertTrue(all(self.store.get_note(key) == value for key, value in before.items()))
        count = len(self.store.list_notes())
        with self.assertRaises(ValueError):
            self.store.create_note(parent_id=parent.id, first=True)
        self.assertEqual(len(self.store.list_notes()), count)
        runs = ({'start': 0, 'end': 4, 'style': 'bold'},)
        copied = self.store.create_note(folder_id=folder.id, parent_id=parent.id,
                                        title='Copy', body='Copy text', runs=runs, first=True)
        self.assertEqual((copied.body, copied.runs, self.store.parent_of(copied.id)),
                         ('Copy text', runs, parent.id))
        before_failure = self.store.list_notes()
        with self.store.connection:
            self.store.connection.execute('CREATE TRIGGER reject_child BEFORE INSERT ON note_hierarchy '
                                          "BEGIN SELECT RAISE(ABORT, 'parent write failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_note(folder_id=folder.id, parent_id=parent.id,
                                   title='No partial copy', body='Copy text', runs=runs, first=True)
        self.assertEqual(self.store.list_notes(), before_failure,
                         'A failed parent write must not leave an empty or partial copy')

    def test_open_in_write_copies_survive_and_never_overwrite_receiver_edits(self):
        from prairie_apps.notes_export import export_open_in_write
        note = self.store.update_note(self.note.id, title='Original', body='Library text')
        root = Path(self.temp.name) / 'handoffs'
        first = export_open_in_write(note, data_directory=root)
        self.assertIn('Library text', first.read_text())
        self.assertEqual(first.parent.stat().st_mode & 0o777, 0o700)
        first.write_text('Edited in Write')
        second = export_open_in_write(note, data_directory=root)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(), 'Edited in Write')
        self.assertIn('Library text', second.read_text())
        self.assertEqual(self.store.get_note(note.id), note)

    def test_snapshot_precedes_first_new_format_and_is_not_replaced(self):
        self.store.update_note(self.note.id, title='Original', body='before')
        self.store.update_note(self.note.id, title='Original', body='after',
                               runs=({'start': 0, 'end': 5, 'style': 'checklist'},))
        path = Path(self.store.connection.execute("SELECT value FROM metadata WHERE key='v70-write-backup'").fetchone()[0])
        with sqlite3.connect(path) as backup:
            self.assertEqual(backup.execute('SELECT body FROM notes WHERE id=?', (self.note.id,)).fetchone()[0], 'before')
        self.store.set_mark('note', self.note.id, {'kind': 'emoji', 'value': '🍞', 'hue': 45})
        self.assertEqual(len(list(path.parent.glob('notes.sqlite3.before-v70-*'))), 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_marks_preserve_unrelated_metadata_and_note_fields(self):
        with self.store.connection:
            self.store.connection.execute("INSERT INTO metadata VALUES('other-owner',?)", (json.dumps({'keep': 1}),))
        self.store.set_mark('note', self.note.id, {'kind': 'icon', 'value': 'coffee', 'hue': 150})
        self.assertEqual(self.store.get_note(self.note.id), self.note)
        self.assertEqual(self.store.get_mark('note', self.note.id)['value'], 'coffee')
        self.store.set_mark('note', self.note.id, {'kind': 'icon', 'hue': 'grey', 'value': 'file-text'})
        self.assertEqual(self.store.get_mark('note', self.note.id)['hue'], 'grey')
        self.store.set_mark('note', self.note.id, None)
        self.assertIsNone(self.store.get_mark('note', self.note.id))
        folder = self.store.create_folder('Colour retained')
        mark = {'kind': 'dot', 'hue': 'grey', 'folder_hue': 220, 'future_field': 'keep'}
        self.store.set_mark('folder', folder.id, mark)
        with sqlite3.connect(self.store.path) as reader:
            raw = reader.execute('SELECT value FROM metadata WHERE key=?',
                                 ('mark:folder:' + folder.id,)).fetchone()[0]
        self.assertEqual(json.loads(raw), mark, 'Retained colour and unrelated mark fields survive persistence')
        self.assertEqual(self.store.connection.execute("SELECT value FROM metadata WHERE key='other-owner'").fetchone()[0], '{"keep": 1}')

    def test_invalid_mark_does_not_write_or_create_backup(self):
        with self.assertRaises(ValueError):
            self.store.set_mark('note', self.note.id, {'kind': 'unknown'})
        self.assertFalse(list(self.store.path.parent.glob('*.before-v70-*')))

    def test_nonfinite_mark_hues_do_not_write_or_create_backup(self):
        folder = self.store.create_folder('No malformed hue')
        for hue in (float('nan'), float('inf'), float('-inf'), True):
            with self.subTest(hue=hue), self.assertRaises(ValueError):
                self.store.set_mark('note', self.note.id, {'kind': 'dot', 'hue': hue})
            with self.subTest(retained=hue), self.assertRaises(ValueError):
                self.store.set_mark('folder', folder.id, {'kind': 'dot', 'hue': 'grey', 'folder_hue': hue})
        self.assertIsNone(self.store.get_mark('note', self.note.id))
        self.assertIsNone(self.store.get_mark('folder', folder.id))
        self.assertFalse(list(self.store.path.parent.glob('*.before-v70-*')))

    def test_folder_cycles_are_rejected_and_deleting_parent_keeps_child(self):
        parent = self.store.create_folder('Parent')
        child = self.store.create_folder('Child')
        self.store.set_folder_parent(child.id, parent.id)
        with self.assertRaises(ValueError):
            self.store.set_folder_parent(parent.id, child.id)
        with self.assertRaises(ValueError):
            self.store.set_folder_parent(child.id, child.id)
        self.assertEqual(self.store.folder_parents(), {child.id: parent.id})
        self.store.delete_folder(parent.id)
        self.assertEqual(self.store.get_folder(child.id), child)
        self.assertEqual(self.store.folder_parents(), {})

    def test_marked_folder_creation_is_atomic_and_backup_precedes_it(self):
        parent = self.store.create_folder('Parent')
        before = self.store.list_folders()
        with self.assertRaises(ValueError):
            self.store.create_folder('Invalid', parent_id=parent.id,
                                     mark={'kind': 'dot', 'hue': float('nan')})
        self.assertEqual(self.store.list_folders(), before)
        self.assertFalse(list(self.store.path.parent.glob('*.before-v70-*')))
        with self.store.connection:
            self.store.connection.execute("CREATE TRIGGER reject_parent BEFORE INSERT ON metadata "
                                          "WHEN NEW.key LIKE 'folder-parent:%' "
                                          "BEGIN SELECT RAISE(ABORT, 'write failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_folder('Rolled back', parent_id=parent.id,
                                     mark={'kind': 'dot', 'hue': 150})
        self.assertEqual(self.store.list_folders(), before)
        self.assertFalse(self.store.folder_parents())
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM metadata WHERE key LIKE 'mark:folder:%'").fetchone()[0], 0)
        backup = Path(self.store.connection.execute(
            "SELECT value FROM metadata WHERE key='v70-write-backup'").fetchone()[0])
        with sqlite3.connect(backup) as snapshot:
            self.assertEqual(snapshot.execute('SELECT count(*) FROM folders').fetchone()[0], 1)

    def test_folder_move_rolls_back_parent_position_and_expansion_on_write_failure(self):
        parent = self.store.create_folder('Parent')
        child = self.store.create_folder('Child')
        self.store.set_folder_expanded(parent.id, False)
        self.store.place_root('folder', child.id, None)
        before = self.store.root_items()
        with self.store.connection:
            self.store.connection.execute("CREATE TRIGGER reject_position BEFORE INSERT ON metadata "
                "WHEN NEW.key='sidebar-root-order' BEGIN SELECT RAISE(ABORT, 'position write failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.move_folder(child.id, parent.id, root_before='folder:' + parent.id,
                                   expand_parent=True)
        self.assertEqual(self.store.folder_parents(), {})
        self.assertEqual(self.store.root_items(), before)
        self.assertFalse(self.store.get_folder(parent.id).expanded)
        with sqlite3.connect(self.store.path) as reader:
            self.assertIsNone(reader.execute('SELECT value FROM metadata WHERE key=?',
                                            ('folder-parent:' + child.id,)).fetchone())
        backup = Path(self.store.connection.execute(
            "SELECT value FROM metadata WHERE key='v70-write-backup'").fetchone()[0])
        with sqlite3.connect(backup) as snapshot:
            self.assertEqual(snapshot.execute('SELECT count(*) FROM folders').fetchone()[0], 2)
        with self.store.connection:
            self.store.connection.execute('DROP TRIGGER reject_position')
        self.store.move_folder(child.id, parent.id, root_before='folder:' + parent.id,
                               expand_parent=True)
        self.assertEqual(self.store.folder_parents(), {child.id: parent.id})
        self.assertTrue(self.store.get_folder(parent.id).expanded)
        self.assertLess([item.id for item in self.store.root_items()].index(child.id),
                        [item.id for item in self.store.root_items()].index(parent.id))
        self.assertEqual(self.store.get_note(self.note.id), self.note)

    def test_export_preserves_checks_strike_and_highlight(self):
        note = self.store.update_note(self.note.id, title='List', body='Done\nNext', runs=(
            {'start': 0, 'end': 5, 'style': 'checklist'}, {'start': 0, 'end': 5, 'style': 'checked'},
            {'start': 5, 'end': 9, 'style': 'checklist'}, {'start': 5, 'end': 9, 'style': 'strike'},
            {'start': 5, 'end': 9, 'style': 'highlight'},
        ))
        self.assertEqual(to_markdown(note)[0], '# List\n\n- [x] Done\n- [ ] <mark>~~Next~~</mark>\n')


if __name__ == '__main__':
    unittest.main()
