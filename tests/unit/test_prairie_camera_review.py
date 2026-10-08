# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/prairie-core'))
from prairie_apps.camera_media import ensure_catalog_backup
from prairie_apps.photos_backend import PhotoLibrary, default_database_path


class CameraCopyReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'XDG_DATA_HOME': str(root / 'data'), 'XDG_STATE_HOME': str(root / 'state'),
            'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_CACHE_HOME': str(root / 'cache'),
            'XDG_PICTURES_DIR': str(root / 'Pictures'),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root = root
        pictures = root / 'Pictures'
        pictures.mkdir()
        self.older = pictures / 'older.jpg'
        self.selected = pictures / 'capture.jpg'
        self.bytes = b'\xff\xd8\xffduplicate camera image\xff\xd9'
        self.older.write_bytes(self.bytes)
        self.selected.write_bytes(self.bytes)
        # A restored/synced copy can have a later timestamp than this capture.
        os.utime(self.older, (2000000000, 2000000000))
        self.library = PhotoLibrary(default_database_path())
        source = self.library.ensure_default_source()
        self.library.scan_source(source.id)
        self.assertEqual(self.library.reconcile_exact_copies(), 1)
        self.record = self.library.assets()[0]
        self.assertEqual(len(self.record.copies), 2)
        self.assertEqual(self.record.path, self.older)
        self.library.set_metadata(self.record.id, caption='retain caption')
        self.library.set_favorite(self.record.id, True)
        source_path = ROOT / 'src/prairie-core/prairie_apps/camera.py'
        tree = ast.parse(source_path.read_text())
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CameraWindow')
        names = {'_photo_library', '_index_capture', '_delete_review_response'}
        methods = [node for node in window.body if isinstance(node, ast.FunctionDef) and node.name in names]
        scope = {'Path': Path, 'os': os, 'default_database_path': default_database_path,
                 'ensure_catalog_backup': ensure_catalog_backup, 'PhotoLibrary': PhotoLibrary,
                 'GLib': SimpleNamespace(Error=RuntimeError),
                 'Gio': SimpleNamespace(File=SimpleNamespace(new_for_path=self.forbidden_fallback))}
        future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *methods], type_ignores=[])), str(source_path), 'exec'), scope)
        self.errors = []
        self.window = SimpleNamespace(_fixture=None, _last_capture_record=self.record,
            _last_capture_path=self.selected, state=SimpleNamespace(roll=[SimpleNamespace(path=self.selected, record=self.record), SimpleNamespace(path=self.older, record=self.record)]),
            _refresh_session_tray=lambda: None, _back_to_camera=lambda: None,
            _show_pipeline_error=self.errors.append,
            _last_shot_picture=SimpleNamespace(set_filename=lambda value: None),
            _last_shot_button=SimpleNamespace(set_sensitive=lambda value: None))
        for name in names:
            setattr(self.window, name, MethodType(scope[name], self.window))

    def forbidden_fallback(self, _path):
        self.fail('An indexed capture must use catalog-backed trash')

    def test_index_matches_selected_copy_even_when_it_is_not_preferred(self):
        indexed = self.window._index_capture(self.selected)
        self.assertIsNotNone(indexed)
        self.assertEqual(indexed.id, self.record.id)

    def test_trash_moves_only_selected_copy_and_preserves_asset_fields(self):
        self.window._delete_review_response(None, 'trash', self.selected)
        self.assertEqual(self.errors, [])
        self.assertFalse(self.selected.exists())
        self.assertEqual(self.older.read_bytes(), self.bytes)
        remaining = self.library.asset(self.record.id)
        self.assertEqual([copy.path for copy in remaining.copies], [self.older])
        self.assertTrue(remaining.favorite)
        self.assertEqual(remaining.caption, 'retain caption')
        deleted = self.library.assets(collection='deleted')[0].copies[0]
        self.assertEqual(deleted.original_path, self.selected)
        self.assertEqual(deleted.path.read_bytes(), self.bytes)
        snapshots = list((self.root / 'state/prairie-core/camera/backups').glob('*.sqlite3'))
        self.assertEqual(len(snapshots), 1)
        with closing(sqlite3.connect(snapshots[0])) as saved:
            self.assertEqual(saved.execute('SELECT COUNT(*) FROM copies WHERE trashed=0').fetchone(), (2,))
        self.assertEqual([shot.path for shot in self.window.state.roll], [self.older])

    def test_cancel_keeps_both_files_and_does_not_open_catalog(self):
        self.window._delete_review_response(None, 'cancel', self.selected)
        self.assertEqual(self.selected.read_bytes(), self.bytes)
        self.assertEqual(self.older.read_bytes(), self.bytes)
        self.assertFalse((self.root / 'state/prairie-core/camera/backups').exists())
        self.assertEqual(self.errors, [])

    def test_copy_removed_by_photos_does_not_trash_another_copy(self):
        selected = next(copy for copy in self.record.copies if copy.path == self.selected)
        deleted_path = self.library.trash_copy(selected.id)
        self.window._delete_review_response(None, 'trash', self.selected)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('no longer in Photos', self.errors[0])
        self.assertEqual(self.older.read_bytes(), self.bytes)
        self.assertEqual(deleted_path.read_bytes(), self.bytes)
        self.assertEqual(len(self.window.state.roll), 2)


if __name__ == '__main__':
    unittest.main()
