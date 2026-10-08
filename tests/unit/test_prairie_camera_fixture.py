# SPDX-License-Identifier: Apache-2.0
"""Fixture state and the native-I/O boundaries, exercised without GTK."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps.camera_fixture import CameraFixture, fixture_from_environment, format_time

FIXTURE = ROOT / "tests/fixtures/camera-v70.json"


def method(name):
    tree = ast.parse((ROOT / "src/prairie-core/prairie_apps/camera.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CameraWindow")
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.returns = None
    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        arg.annotation = None
    scope = {"PhotoLibrary": Mock(side_effect=AssertionError("real photo store touched")),
             "new_photo_path": Mock(side_effect=AssertionError("real capture path touched")),
             "Gst": Mock(side_effect=AssertionError("real pipeline touched"))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "camera.py", "exec"), scope)
    return scope[name]


class CameraFixtureTests(unittest.TestCase):
    def test_v70_sources_and_initial_state(self):
        fixture = CameraFixture(FIXTURE)
        self.assertEqual([c.name for c in fixture.cameras], ["Studio Cam", "Nick’s phone", "Built-in camera"])
        self.assertEqual(fixture.camera.zooms, (1, 2, 4))
        self.assertEqual(fixture.values["fmt"], "raw")
        self.assertTrue(fixture.state.grid)
        self.assertEqual(fixture.state.roll, [])
        self.assertTrue(all(d.identifier.startswith("fixture:") for d in fixture.devices))

    def test_capture_is_in_memory_and_preserves_fixture_bytes(self):
        fixture = CameraFixture(FIXTURE)
        files = [FIXTURE, *(c.image for c in fixture.cameras)]
        before = {p: hashlib.sha256(p.read_bytes()).digest() for p in files}
        with patch("pathlib.Path.open", side_effect=AssertionError("capture opened a file")):
            first = fixture.capture()
            fixture.capture()
        self.assertEqual(first.path, fixture.camera.image)
        self.assertEqual(len(fixture.state.roll), 2)
        self.assertEqual(fixture.shots[0]["label"], "RAW + JPEG")
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).digest() for p in files})

    def test_video_start_stop_and_scan_do_not_encode(self):
        fixture = CameraFixture(FIXTURE)
        fixture.state.mode = "video"
        self.assertIsNone(fixture.capture())
        self.assertTrue(fixture.state.recording)
        fixture.state.elapsed = 12
        self.assertEqual(fixture.capture().duration, 12)
        self.assertEqual(fixture.shots[0]["label"], "Video, 00:12")
        fixture.state.mode = "scan"
        fixture.capture()
        self.assertEqual(fixture.shots[0]["label"], "Document, PDF")
        self.assertEqual(format_time(125), "02:05")

    def test_source_change_drops_unsupported_raw_and_manual(self):
        fixture = CameraFixture(FIXTURE)
        fixture.values["pro"] = True
        fixture.state.zoom = 4
        fixture.select_camera(2)
        self.assertEqual(fixture.camera.name, "Built-in camera")
        self.assertEqual(fixture.values["fmt"], "jpeg")
        self.assertFalse(fixture.values["pro"])
        self.assertTrue(fixture.state.mirror)
        self.assertEqual(fixture.state.zoom, 1)

    def test_invalid_environment_never_falls_back_to_real_devices(self):
        with patch.dict(os.environ, {"LUMA_CAMERA_FIXTURE": ""}):
            with self.assertRaises(ValueError):
                fixture_from_environment()
        with patch.dict(os.environ, {"LUMA_CAMERA_FIXTURE": "/does/not/exist"}):
            with self.assertRaises(FileNotFoundError):
                fixture_from_environment()
        for overrides in ({"cam": "unknown"}, {"mode": "unknown"}, {"timer": 5}, {"zoom": 5}, {"ev": 3}, {"iso": "10"}, {"bogus": True}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                CameraFixture(FIXTURE, overrides=overrides)

    def test_fixture_image_cannot_escape_directory(self):
        data = json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data["cameras"][0]["image"] = str(FIXTURE)
            path = root / "fixture.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                CameraFixture(path)

    def test_direct_native_write_entry_points_fail_closed(self):
        fake = SimpleNamespace(_fixture=CameraFixture(FIXTURE))
        for name, args in (("_photo_library", ()), ("_start_video", ()), ("_save_still_sample", (None, b"jpeg")),
                           ("_index_capture", (Path("/not-a-real-photo"),)),
                           ("_review_favorite_toggled", (Mock(),)),
                           ("_delete_review_response", (None, "trash", Path("/not-a-real-photo")))):
            with self.subTest(entry_point=name), self.assertRaisesRegex(RuntimeError, "fixture mode"):
                method(name)(fake, *args)


if __name__ == "__main__":
    unittest.main()
