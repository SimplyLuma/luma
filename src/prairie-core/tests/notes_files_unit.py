# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from prairie_apps.notes_backend import NotesStore
from prairie_apps.notes_files import file_path, store_file, validate_file
from prairie_apps.notes_export import export_markdown


class FileTests(unittest.TestCase):
    def test_copy_is_private_additive_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            original = root / 'brief.md'
            original.write_bytes(b'# Original\n')
            metadata = store_file(original, root / 'files')
            kept = file_path(metadata['src'], root / 'files')
            self.assertEqual(original.read_bytes(), b'# Original\n')
            self.assertEqual(kept.read_bytes(), original.read_bytes())
            self.assertEqual(kept.stat().st_mode & 0o777, 0o600)
            self.assertEqual(store_file(original, root / 'files'), metadata)
            self.assertEqual(len(list((root / 'files').iterdir())), 1)
            self.assertTrue(metadata['src'].endswith('.md'))

    def test_invalid_or_oversized_file_never_creates_copy(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            original = root / 'test.txt'
            original.write_bytes(b'12345')
            with mock.patch('prairie_apps.notes_files.MAX_FILE_BYTES', 4), self.assertRaises(ValueError):
                store_file(original, root / 'files')
            self.assertFalse((root / 'files').exists())
            for reference in ('../../secret', '/etc/passwd', 'a' * 64 + '.../../txt'):
                with self.assertRaises(ValueError):
                    file_path(reference, root)
            with self.assertRaises(ValueError):
                validate_file({'src': 'a' * 64 + '.txt', 'name': '../secret', 'size': 1})

    def test_saved_reference_has_backup_and_exported_file_copy(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            original = root / 'brief.txt'
            original.write_bytes(b'brief')
            metadata = store_file(original, root / 'files')
            store = NotesStore(root / 'notes.sqlite3')
            try:
                note = store.create_note(title='Original')
                store.update_note(note.id, title=note.title, body='First',
                                  runs=({'start': 0, 'end': 5, 'style': 'strike'},))
                store.update_note(note.id, title=note.title, body='Latest')
                run = dict(metadata, start=0, end=1, style='file')
                note = store.update_note(note.id, title=note.title, body='\ufffc\n', runs=(run,))
                self.assertEqual(store.get_note(note.id).runs, (run,))
                self.assertEqual(note.preview, 'brief.txt')
                self.assertEqual(note.plain_text, 'brief.txt\n')
                self.assertTrue(list(root.glob('*.before-v70-*')))
                self.assertTrue(list(root.glob('*.before-files-*')))
                backup = store.connection.execute(
                    "SELECT value FROM metadata WHERE key='files-write-backup'").fetchone()[0]
                with sqlite3.connect(backup) as snapshot:
                    self.assertEqual(snapshot.execute('SELECT body FROM notes WHERE id=?',
                                                      (note.id,)).fetchone()[0], 'Latest')
                with mock.patch('prairie_apps.notes_files.files_directory', return_value=root / 'files'):
                    export_markdown(note, root / 'export.md')
                self.assertIn('[brief.txt](export%20attachments/', (root / 'export.md').read_text())
                self.assertEqual((root / 'export attachments' / metadata['src']).read_bytes(), b'brief')
                file_path(metadata['src'], root / 'files').unlink()
                with mock.patch('prairie_apps.notes_files.files_directory', return_value=root / 'files'):
                    with self.assertRaises(FileNotFoundError):
                        export_markdown(note, root / 'missing.md')
                self.assertFalse((root / 'missing.md').exists())
            finally:
                store.close()

    def test_sync_preserves_local_file_slot_without_uploading_file(self):
        from prairie_apps.connect_notes import NotesDeltaSync, note_attachments
        with tempfile.TemporaryDirectory() as scratch:
            store = NotesStore(Path(scratch) / 'notes.sqlite3')
            try:
                file = {'style': 'file', 'start': 0, 'end': 1, 'src': 'a' * 64 + '.txt',
                        'name': 'brief.txt', 'size': 5}
                image = {'style': 'image', 'start': 2, 'end': 3, 'src': 'b' * 64 + '.png'}
                note = store.create_note()
                note = store.update_note(note.id, title='', body='\ufffc\n\ufffc', runs=(file, image))
                sync = object.__new__(NotesDeltaSync)
                with mock.patch.object(sync, '_picture_name', return_value=image['src']):
                    runs = sync._runs_for(note.body, ['b' * 64], note)
                self.assertEqual(runs, (file, image))
                self.assertEqual(note_attachments(note), ['b' * 64])
            finally:
                store.close()
