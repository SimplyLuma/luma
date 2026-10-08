# SPDX-License-Identifier: Apache-2.0
"""Check activity policy without a display, camera or microphone."""
import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(os.environ.get("CAMERA_ACTIVITY_TEST_SOURCE", Path(__file__).resolve().parents[2] / "src/prairie-core/prairie_apps/camera.py"))
window = next(node for node in ast.parse(SOURCE.read_text()).body if isinstance(node, ast.ClassDef) and node.name == "CameraWindow")

def method(name):
    node = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"Gst": SimpleNamespace(State=SimpleNamespace(NULL=0))}
    exec("from __future__ import annotations\n" + ast.unparse(node), namespace)
    return namespace[name]


def fixture(desktop=True, recording=False):
    return SimpleNamespace(
        _desktop=desktop, is_active=lambda: False,
        state=SimpleNamespace(recording=recording, screen="capture", source=0),
        _devices=(object(),), _picture=Mock(), _status=Mock(),
        _preview_suspended=False, _pipeline=Mock(), _capture_pipeline=None,
        _pending_camera_index=None, _stopping_pipeline=None,
        _stop_video=Mock(), _stop_camera=Mock(), _start_camera=Mock(),
        _cancel_background_capture=Mock(), _capture_button=Mock(), _switch_button=Mock(),
        _source_picker=Mock(), _settings_button=Mock(),
    )


class CameraActivityTests(unittest.TestCase):
    def test_desktop_focus_loss_keeps_preview_and_pending_capture(self):
        camera = fixture()
        method("_on_window_activity_changed")(camera, None, None)
        camera._stop_camera.assert_not_called()
        camera._cancel_background_capture.assert_not_called()
        self.assertFalse(camera._preview_suspended)

    def test_desktop_focus_loss_keeps_recording(self):
        camera = fixture(recording=True)
        method("_on_window_activity_changed")(camera, None, None)
        camera._stop_video.assert_not_called()

    def test_desktop_capture_resumes_preview_without_focus(self):
        camera = fixture()
        method("_resume_preview_after_capture")(camera)
        camera._start_camera.assert_called_once_with(0)

    def test_handheld_background_still_releases_camera(self):
        camera = fixture(desktop=False)
        method("_on_window_activity_changed")(camera, None, None)
        camera._stop_camera.assert_called_once()
        self.assertTrue(camera._preview_suspended)

    def test_handheld_background_still_keeps_recording(self):
        camera = fixture(desktop=False, recording=True)
        method("_on_window_activity_changed")(camera, None, None)
        camera._stop_video.assert_called_once()
