# SPDX-License-Identifier: Apache-2.0
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/prairie-core"))
from prairie_apps.camera_state import CameraState, aspect_frame, crop_edges, output_dimensions


class CameraStateTests(unittest.TestCase):
    def test_source_switch_clears_sensor_specific_state(self):
        state = CameraState(zoom=3, manual={"ev": .7}, reticle=(.2, .3))
        state.select_source(2)
        self.assertEqual((state.source, state.zoom, state.manual, state.reticle), (2, 1, {}, None))

    def test_crop_retains_chroma_alignment_and_expected_zoom(self):
        self.assertEqual(crop_edges(1920, 1080, 1), (0, 0))
        self.assertEqual(crop_edges(1920, 1080, 2), (480, 270))
        x, y = crop_edges(1920, 1080, 4)
        self.assertEqual((x % 2, y % 2), (0, 0))
        self.assertGreater(1920 - 2 * x, 0)
        self.assertGreater(1080 - 2 * y, 0)

    def test_photo_shape_changes_preview_and_output_without_stretching(self):
        self.assertEqual(aspect_frame(1600, 900, "1:1"), (350, 0, 900, 900))
        self.assertEqual(aspect_frame(1600, 900, "16:9"), (0, 0, 1600, 900))
        self.assertEqual(output_dimensions(4000, 3000, "16:9"), (4000, 2250))
        for shape in ("4:3", "3:2", "16:9", "1:1"):
            x, y = crop_edges(4000, 3000, 2, shape)
            self.assertEqual((x % 2, y % 2), (0, 0))
            self.assertAlmostEqual((4000 - 2 * x) / (3000 - 2 * y),
                                   output_dimensions(4000, 3000, shape)[0] /
                                   output_dimensions(4000, 3000, shape)[1], delta=.01)

    def test_auto_and_session_data_are_not_shared_between_windows(self):
        first, second = CameraState(), CameraState()
        first.manual["ev"] = 0
        self.assertIn("ev", first.manual)
        self.assertNotIn("ev", second.manual)
        first.manual.clear()
        self.assertFalse(first.manual)
