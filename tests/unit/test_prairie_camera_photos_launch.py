# SPDX-License-Identifier: Apache-2.0
"""Camera hands real files to the asynchronous installed-application owner."""
from __future__ import annotations
import ast
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch
from gi.repository import Gio

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'src/prairie-core/prairie_apps/camera.py'

class Window:
    def __init__(self, path=None):
        self._fixture = None
        self._last_capture_path = path
        self.errors = []
    def _show_pipeline_error(self, message): self.errors.append(message)

def actual_handler(name, application, extra=None):
    tree = ast.parse(SOURCE.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CameraApplication')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    scope = dict(CameraWindow=Window, Gio=Gio)
    scope.update(extra or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, method], type_ignores=[])), str(SOURCE), 'exec'), scope)
    return MethodType(scope[name], application)

class PhotosLaunchTests(unittest.TestCase):
    def setUp(self):
        self.window = Window(Path('/tmp/camera-launch-test.jpg'))
        self.application = SimpleNamespace(props=SimpleNamespace(active_window=self.window))
        self.launch = actual_handler('_on_open_photos', self.application)
        self.owner_patch = patch('luma_appkit.application_directory.launch')
        self.owner = self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        self.environment_patch = patch.dict('os.environ', {'LUMA_CAMERA_PRIVATE_PREVIEW': '0'})
        self.environment_patch.start(); self.addCleanup(self.environment_patch.stop)
    def request(self):
        self.launch(None, None)
        self.owner.assert_called_once()
        args = self.owner.call_args.args
        self.assertEqual(args[0], 'org.projectluma.Photos.desktop')
        return args[1], self.owner.call_args.kwargs['callback']
    def test_missing_desktop_app_reports_failure(self):
        _, finish = self.request(); finish(False, 'Photos is unavailable.')
        self.assertEqual(self.window.errors, ['Photos did not open: Photos is unavailable.'])
    def test_false_launch_reports_failure(self):
        files, finish = self.request()
        self.assertEqual([f.get_uri() for f in files], [self.window._last_capture_path.as_uri()])
        finish(False, 'The launcher refused the request')
        self.assertEqual(self.window.errors, ['Photos did not open: The launcher refused the request'])
    def test_launch_error_reports_failure(self):
        _, finish = self.request(); finish(False, 'private launcher refused')
        self.assertEqual(self.window.errors, ['Photos did not open: private launcher refused'])
    def test_success_passes_selected_capture_uri(self):
        files, finish = self.request()
        self.assertEqual([f.get_uri() for f in files], [self.window._last_capture_path.as_uri()])
        self.assertEqual(self.window.errors, [])
        finish(True, None); self.assertEqual(self.window.errors, [])
    def test_without_capture_opens_photos_normally(self):
        self.window._last_capture_path = None
        files, finish = self.request(); self.assertEqual(files, [])
        finish(True, None); self.assertEqual(self.window.errors, [])
    def test_without_capture_false_launch_reports_failure(self):
        self.window._last_capture_path = None
        files, finish = self.request(); self.assertEqual(files, [])
        finish(False, 'The launcher refused the request')
        self.assertEqual(self.window.errors, ['Photos did not open: The launcher refused the request'])

class DarkroomLaunchTests(unittest.TestCase):
    def test_false_launch_reports_failure(self):
        dialogs = []
        class Dialog:
            def __init__(self, heading, body):
                self.heading, self.body = heading, body; dialogs.append(self)
            def add_response(self, _name, _label): pass
            def present(self, _window): pass
        window = Window(Path('/tmp/camera-launch-test.jpg'))
        application = SimpleNamespace(props=SimpleNamespace(active_window=window))
        method = actual_handler('_on_darkroom', application, {'Adw': SimpleNamespace(AlertDialog=Dialog)})
        with patch.dict('os.environ', {'LUMA_CAMERA_PRIVATE_PREVIEW': '0'}), patch('luma_appkit.application_directory.launch') as owner:
            method(None, None)
            self.assertEqual(owner.call_args.args[0], 'org.projectluma.Darkroom.desktop')
            self.assertEqual([f.get_uri() for f in owner.call_args.args[1]], [window._last_capture_path.as_uri()])
            self.assertEqual(dialogs, [], 'No error before the asynchronous owner replies')
            owner.call_args.kwargs['callback'](False, 'The launcher refused the request')
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0].heading, 'Darkroom did not open')
        self.assertIn('original photo remains unchanged', dialogs[0].body)

if __name__ == '__main__': unittest.main()
