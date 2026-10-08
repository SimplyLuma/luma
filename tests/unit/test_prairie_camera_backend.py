#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))

from prairie_apps.camera_backend import (  # noqa: E402
    _preview_source_size,
    _preview_saturation,
    _still_capture_profile,
    _viewfinder_profile,
    image_rotation_from_mount,
    inspect_camera_capability,
    list_camera_devices,
    new_photo_path,
    publish_capture,
    sensor_point_from_display,
    still_focus_position,
    video_direction_for_transform,
)


class CameraCapabilityTests(unittest.TestCase):
    def test_desktop_discovery_does_not_require_cam_command(self):
        expected = (object(),)
        with mock.patch("shutil.which", return_value=None), mock.patch(
            "prairie_apps.camera_backend._list_desktop_cameras", return_value=expected
        ):
            self.assertEqual(list_camera_devices(), expected)

    def test_desktop_discovery_handles_non_media_graph_camera_identifiers(self):
        expected = (object(),)
        with mock.patch("shutil.which", return_value="/usr/bin/cam"), mock.patch(
            "subprocess.run", return_value=mock.Mock(stdout="Available cameras:\n")
        ), mock.patch("prairie_apps.camera_backend._pipewire_camera_rotations", return_value={}), mock.patch(
            "prairie_apps.camera_backend._list_desktop_cameras", return_value=expected
        ):
            self.assertEqual(list_camera_devices(), expected)

    def test_fp6_labels_and_default_order_follow_sensor_identity(self) -> None:
        listing = """Available cameras:
1: Built-in Front Camera (/base/soc@0/cci@ac16000/i2c-bus@1/camera@3d)
2: Built-in Back Camera (/base/soc@0/cci@ac15000/i2c-bus@0/camera@1a)
3: Built-in Back Camera (/base/soc@0/cci@ac15000/i2c-bus@1/camera@36)
"""
        completed = mock.Mock(stdout=listing)
        with mock.patch("shutil.which", return_value="/usr/bin/cam"), mock.patch(
            "subprocess.run", return_value=completed
        ), mock.patch(
            "prairie_apps.camera_backend._pipewire_camera_rotations",
            return_value={},
        ):
            devices = list_camera_devices()
        self.assertEqual([device.label for device in devices], ["Main", "Wide", "Front"])
        self.assertEqual(
            [device.identifier.rsplit("/", 1)[-1] for device in devices],
            ["camera@1a", "camera@36", "camera@3d"],
        )

    def test_display_point_maps_through_sensor_mount_rotation(self) -> None:
        self.assertEqual(sensor_point_from_display(50, 25, 100, 100, 0), (500, 750))
        self.assertEqual(sensor_point_from_display(50, 25, 100, 100, 90), (750, 500))
        self.assertEqual(sensor_point_from_display(50, 25, 100, 100, 180), (500, 250))
        self.assertEqual(sensor_point_from_display(50, 25, 100, 100, 270), (250, 500))

    def test_mobile_rotation_matches_sensor_and_display_orientation(self) -> None:
        self.assertEqual(image_rotation_from_mount(270), 90)
        self.assertEqual(image_rotation_from_mount(90), 270)
        self.assertEqual(image_rotation_from_mount(270, 90), 0)
        self.assertEqual(image_rotation_from_mount(90, 90, True), 0)
        self.assertEqual(
            sensor_point_from_display(25, 20, 100, 100, 270),
            (200, 750),
        )
        self.assertEqual(
            sensor_point_from_display(25, 20, 100, 100, 90),
            (800, 250),
        )

    def test_front_preview_mirror_uses_one_transform_and_preserves_taps(self) -> None:
        self.assertEqual(video_direction_for_transform(90, True), 6)
        self.assertEqual(video_direction_for_transform(270, True), 7)
        self.assertEqual(video_direction_for_transform(90), 1)
        self.assertEqual(
            sensor_point_from_display(
                25,
                20,
                100,
                100,
                90,
                front_facing=True,
                mirror_horizontal=True,
            ),
            (800, 750),
        )

    def test_fp6_wide_uses_longer_exposure_sensor_mode(self) -> None:
        self.assertEqual(
            _preview_source_size(
                "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36"
            ),
            (2080, 1170),
        )
        self.assertEqual(_preview_source_size("/camera/external"), (1920, 1080))
        self.assertEqual(
            _preview_saturation(
                "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36"
            ),
            0.85,
        )
        self.assertIsNone(_preview_saturation("/camera/external"))

    def test_fp6_viewfinder_reduces_compositor_pressure(self) -> None:
        for identifier in (
            "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36",
            "/base/soc@0/cci@ac15000/i2c-bus@0/camera@1a",
            "/base/soc@0/cci@ac16000/i2c-bus@1/camera@3d",
        ):
            self.assertEqual(_viewfinder_profile(identifier), (1600, 900, 15))
        self.assertEqual(
            _viewfinder_profile("/camera/external", 1280, 720),
            (1280, 720, 30),
        )

    def test_fp6_still_profiles_use_native_four_by_three_modes(self) -> None:
        self.assertEqual(
            _still_capture_profile(
                "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36"
            ),
            (2560, 1920, 29.0),
        )
        self.assertEqual(
            _still_capture_profile(
                "/base/soc@0/cci@ac15000/i2c-bus@0/camera@1a"
            ),
            (2560, 1920, 35.0),
        )
        self.assertEqual(
            _still_capture_profile(
                "/base/soc@0/cci@ac16000/i2c-bus@1/camera@3d"
            ),
            (2560, 1920, None),
        )

    def test_still_capture_inherits_a_valid_preview_focus_position(self) -> None:
        self.assertEqual(still_focus_position(37.25, 29.0), 37.25)
        self.assertEqual(still_focus_position("44", 29.0), 44.0)
        self.assertEqual(still_focus_position(None, 29.0), 29.0)
        self.assertEqual(still_focus_position(float("nan"), 29.0), 29.0)
        self.assertEqual(still_focus_position(101.0, 29.0), 29.0)

    def test_new_photo_path_is_private_and_collision_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 18, 13, 4, 5, tzinfo=timezone.utc)
            environment = {"HOME": directory}
            first = new_photo_path(environment, now)
            self.assertEqual(first.name, "Luma-20260818-130405.jpg")
            self.assertEqual(first.parent.name, "Camera")
            first.touch()
            second = new_photo_path(environment, now)
            self.assertEqual(second.name, "Luma-20260818-130405-2.jpg")

    def test_publication_preserves_file_created_after_name_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requested = new_photo_path({"HOME": directory})
            temporary = requested.parent / ".complete-still.partial"
            temporary.write_bytes(b"new complete JPEG")
            requested.write_bytes(b"existing photo")
            published = publish_capture(temporary, requested)
            self.assertNotEqual(published, requested)
            self.assertEqual(requested.read_bytes(), b"existing photo")
            self.assertEqual(published.read_bytes(), b"new complete JPEG")
            self.assertFalse(temporary.exists())

    def test_publication_preserves_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requested = root / "take.webm"
            missing = root / "not-created.webm"
            requested.symlink_to(missing)
            temporary = root / ".complete-take.partial"
            temporary.write_bytes(b"complete WebM")
            published = publish_capture(temporary, requested)
            self.assertTrue(requested.is_symlink())
            self.assertEqual(requested.readlink(), missing)
            self.assertFalse(missing.exists())
            self.assertEqual(published.read_bytes(), b"complete WebM")

    def test_concurrent_publications_keep_every_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requested = root / "take.webm"
            temporaries = [root / f".take-{number}.partial" for number in range(4)]
            contents = [f"complete take {number}".encode() for number in range(4)]
            for temporary, content in zip(temporaries, contents):
                temporary.write_bytes(content)
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(publish_capture, temporary, requested)
                           for temporary in temporaries]
                published = [future.result() for future in futures]
            self.assertEqual(len(set(published)), 4)
            self.assertEqual([path.read_bytes() for path in published], contents)
            self.assertTrue(all(not temporary.exists() for temporary in temporaries))

    def test_failed_publication_retains_unfinished_take(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary = root / ".take.partial"
            temporary.write_bytes(b"complete take")
            with self.assertRaises(FileNotFoundError):
                publish_capture(temporary, root / "missing-directory" / "take.webm")
            self.assertEqual(temporary.read_bytes(), b"complete take")

    def test_video_codec_nodes_never_count_as_cameras(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "video0").touch()
            (root / "video1").touch()
            result = inspect_camera_capability(root)
        self.assertFalse(result.available)
        self.assertIn("media-controller", result.reason)

    def test_media_graph_without_libcamera_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media0").touch()
            with mock.patch("shutil.which", return_value=None):
                result = inspect_camera_capability(root)
        self.assertFalse(result.available)
        self.assertIn("libcamera", result.reason)

    def test_pipewire_camera_completes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media0").touch()
            with mock.patch("shutil.which", return_value="/usr/bin/tool"), mock.patch(
                "prairie_apps.camera_backend._pipewire_camera_count", return_value=2
            ):
                result = inspect_camera_capability(root)
        self.assertTrue(result.available)
        self.assertEqual(result.camera_count, 2)


if __name__ == "__main__":
    unittest.main()
