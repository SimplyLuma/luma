# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/prairie-core'))
from prairie_apps.photos_adjustments import AdjustmentStore, apply_changes
from prairie_apps.photos_backend import PhotoLibrary


class AdjustmentTests(unittest.TestCase):
    def test_changes_preserve_unedited_and_future_fields(self):
        base = {'e': 12, 'w': 8, 'future': {'enabled': True}}
        self.assertEqual(apply_changes(base, {'e': 20, 'w': None}),
                         {'e': 20, 'future': {'enabled': True}})
        self.assertEqual(base['e'], 12)

    def test_invalid_changes_are_rejected(self):
        for changes in ({'e': 101}, {'sat': float('nan')}, {'rot': 45},
                        {'flip': 1}, {'pre': 'unknown'}, {'crop': '3:2'},
                        {'unknown': 2}, {'e': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                apply_changes({}, changes)

    def test_crop_ratio_is_reversible(self):
        self.assertEqual(apply_changes({}, {'crop': 'square'}), {'crop': 'square'})
        self.assertEqual(apply_changes({'crop': 'square'}, {'crop': 'original'}),
                         {'crop': 'original'})

    def test_manual_crop_persists_and_invalid_frames_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            picture = root / 'original.jpg'
            content = b'\xff\xd8\xfforiginal\xff\xd9'
            picture.write_bytes(content)
            library = PhotoLibrary(root / 'library.sqlite3')
            source = library.add_source(root, 'Pictures')
            library.scan_source(source.id)
            photo = library.assets()[0]
            store = AdjustmentStore(library.database)
            rect = [.1, .2, .7, .6]
            store.update(photo.id, {'crop_rect': rect})
            self.assertEqual(AdjustmentStore(library.database).load(photo.id)['crop_rect'], rect)
            for bad in ([.5, .5, .8, .8], [-.1, 0, .5, .5], [0, 0, 0, 1],
                        [0, 0, True, 1], [0, 0, float('nan'), 1], [0, 0, 1]):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    store.update(photo.id, {'crop_rect': bad})
                self.assertEqual(store.load(photo.id)['crop_rect'], rect)
            store.update(photo.id, {'crop_rect': None})
            self.assertNotIn('crop_rect', store.load(photo.id))
            self.assertEqual(picture.read_bytes(), content)

    def test_reads_create_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'missing.sqlite3'
            self.assertEqual(AdjustmentStore(db).load('absent'), {})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_save_backs_up_once_and_never_changes_original_or_other_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            picture = root / 'original.jpg'
            content = b'\xff\xd8\xfforiginal\xff\xd9'
            picture.write_bytes(content)
            db = root / 'library.sqlite3'
            library = PhotoLibrary(db)
            source = library.add_source(root, 'Pictures')
            library.scan_source(source.id)
            photo = library.assets()[0]
            library.set_favorite(photo.id, True)
            library.set_metadata(photo.id, caption='Keep this', place='Oakland')
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('INSERT INTO metadata VALUES (?,?)', ('unrelated', 'keep'))
            store = AdjustmentStore(db)
            store.update(photo.id, {'e': 12, 'w': 8})
            backup = store.backup_path.read_bytes()
            store.update(photo.id, {'e': 20})
            self.assertEqual(store.load(photo.id), {'e': 20, 'w': 8})
            self.assertEqual(store.backup_path.read_bytes(), backup)
            self.assertEqual(picture.read_bytes(), content)
            saved = library.asset(photo.id)
            self.assertEqual((saved.favorite, saved.caption, saved.place), (True, 'Keep this', 'Oakland'))
            with closing(sqlite3.connect(store.backup_path)) as connection, connection:
                self.assertIsNone(connection.execute('SELECT value FROM metadata WHERE key=?',
                                  (store.key(photo.id),)).fetchone())
                self.assertEqual(connection.execute('SELECT value FROM metadata WHERE key=?',
                                 ('unrelated',)).fetchone()[0], 'keep')
            store.update(photo.id, {'e': None, 'w': None})
            self.assertEqual(store.load(photo.id), {})

    def test_changes_read_latest_settings_and_refuse_missing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.jpg').write_bytes(b'\xff\xd8\xffa\xff\xd9')
            library = PhotoLibrary(root / 'library.sqlite3')
            source = library.add_source(root, 'Pictures')
            library.scan_source(source.id)
            photo = library.assets()[0]
            first = AdjustmentStore(library.database)
            second = AdjustmentStore(library.database)
            first.update(photo.id, {'e': 12})
            second.update(photo.id, {'w': 8})
            first.update(photo.id, {'e': 20})
            self.assertEqual(second.load(photo.id), {'e': 20, 'w': 8})
            with self.assertRaises(KeyError):
                first.update('not-an-asset', {'e': 10})

    def test_backup_failure_prevents_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.jpg').write_bytes(b'\xff\xd8\xffa\xff\xd9')
            library = PhotoLibrary(root / 'library.sqlite3')
            source = library.add_source(root, 'Pictures')
            library.scan_source(source.id)
            photo = library.assets()[0]
            store = AdjustmentStore(library.database)
            store.backup_path.mkdir()
            with self.assertRaises(OSError):
                store.update(photo.id, {'e': 10})
            self.assertEqual(store.load(photo.id), {})


if __name__ == '__main__':
    unittest.main()
