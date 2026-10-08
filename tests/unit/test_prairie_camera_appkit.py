#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
from contextlib import closing
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))
SOURCE = ROOT / "src/prairie-core/prairie_apps/camera.py"
CSS = ROOT / "src/prairie-core/style/camera.css"


class CameraAppKitContractTests(unittest.TestCase):
    def test_preview_hook_controls_application_identity_without_changing_production(self) -> None:
        tree = ast.parse(SOURCE.read_text())
        declarations = [node for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id in ('APP_ID', 'APPLICATION_ID')
                                for target in node.targets)]
        application = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                           and node.name == 'CameraApplication')
        application.body = [node for node in application.body if isinstance(node, ast.FunctionDef)
                            and node.name == '__init__']
        class Application:
            def __init__(self, *, application_id):
                self.application_id = application_id
        scope = {'Adw': SimpleNamespace(Application=Application), 'os': os}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[*declarations, application],
                                                         type_ignores=[])), str(SOURCE), 'exec'), scope)
        production = 'org.projectluma.Camera'
        for preview in (False, True):
            scope['APP_ID'] = production + ('.LumaUIPreview' if preview else '')
            for fixture in (False, True):
                with self.subTest(preview=preview, fixture=fixture), patch.dict(os.environ):
                    os.environ.pop('LUMA_CAMERA_FIXTURE', None)
                    if fixture:
                        os.environ['LUMA_CAMERA_FIXTURE'] = 'fixture.json'
                    app = scope['CameraApplication']()
                    self.assertEqual(app.application_id, scope['APP_ID'] + ('.Fixture' if fixture else ''))
                    self.assertEqual(scope['APPLICATION_ID'], production)

    def test_existing_catalog_is_backed_up_before_library_initialization(self) -> None:
        tree = ast.parse(SOURCE.read_text())
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CameraWindow')
        method = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == '_photo_library')
        method.returns = None
        calls = []
        database = Path('/temporary-catalog/photos.sqlite3')
        library = object()
        scope = {'Path': Path, 'os': os, 'default_database_path': lambda: database,
                 'ensure_catalog_backup': lambda db, directory: calls.append(('backup', db, directory)),
                 'PhotoLibrary': lambda db: (calls.append(('library', db)), library)[1]}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(SOURCE), 'exec'), scope)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_STATE_HOME': directory}):
            self.assertIs(scope['_photo_library'](SimpleNamespace(_fixture=None)), library)
            self.assertEqual(calls, [('backup', database, Path(directory) / 'prairie-core/camera/backups'),
                                     ('library', database)])

    def test_real_catalog_initialization_preserves_a_prior_snapshot(self) -> None:
        from prairie_apps.camera_media import ensure_catalog_backup
        from prairie_apps.photos_backend import PhotoLibrary, SCHEMA_VERSION
        tree = ast.parse(SOURCE.read_text())
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CameraWindow')
        method = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == '_photo_library')
        method.returns = None
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_STATE_HOME': directory}):
            database = Path(directory) / 'photos.sqlite3'
            PhotoLibrary(database)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE metadata SET value='1' WHERE key='schema_version'")
                connection.execute("INSERT INTO metadata VALUES('unrelated-user-field','keep')")
                connection.commit()
            scope = {'Path': Path, 'os': os, 'default_database_path': lambda: database,
                     'ensure_catalog_backup': ensure_catalog_backup, 'PhotoLibrary': PhotoLibrary}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(SOURCE), 'exec'), scope)
            library = scope['_photo_library'](SimpleNamespace(_fixture=None))
            self.assertEqual(library.database, database)
            snapshots = list((Path(directory) / 'prairie-core/camera/backups').glob('*.sqlite3'))
            self.assertEqual(len(snapshots), 1)
            with closing(sqlite3.connect(snapshots[0])) as saved:
                self.assertEqual(saved.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone(), ('1',))
                self.assertEqual(saved.execute("SELECT value FROM metadata WHERE key='unrelated-user-field'").fetchone(), ('keep',))
            with closing(sqlite3.connect(database)) as current:
                self.assertEqual(current.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone(), (str(SCHEMA_VERSION),))
                self.assertEqual(current.execute("SELECT value FROM metadata WHERE key='unrelated-user-field'").fetchone(), ('keep',))

    def test_camera_is_one_appkit_application_with_explicit_capabilities(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("class CameraWindow(AppWindow):", source)
        self.assertIn("PresentationMode.FULLSCREEN", source)
        self.assertIn("InputMode.TOUCH", source)
        self.assertNotIn("class MobileCamera", source)

    def test_capture_is_atomic_and_hands_committed_media_to_photos(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('suffix=".partial"', source)
        self.assertIn("path = publish_capture(temporary, path)", source)
        self.assertIn("self._video_path = publish_capture(self._video_temporary, self._video_path)", source)
        self.assertIn("self._index_capture", source)
        self.assertIn("library.set_favorite", source)

    def test_compact_camera_uses_real_or_capability_gated_controls(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        css = CSS.read_text(encoding="utf-8")
        self.assertIn('device.still_width * device.still_height', source)
        self.assertIn("set_sensitive(False)", source)
        self.assertIn("camera-viewfinder", css)
        self.assertIn("camera-review", css)


if __name__ == "__main__":
    unittest.main()
