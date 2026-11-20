# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from pathlib import Path

from luma_streamy.model import Effects, ModelError, Project, ProjectStore


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.project = Project.starter()
        self.scene = self.project.active_scene

    def test_starter(self):
        self.assertEqual(len(self.project.scenes), 1)
        self.assertEqual(self.project.active, self.scene.id)
        self.assertEqual(self.project.canvas, (1920, 1080))

    def test_widgets_and_defaults(self):
        display = self.project.add_widget(self.scene.id, "display")
        self.assertEqual((display.x, display.y, display.width, display.height), (0, 0, 1920, 1080))
        camera = self.project.add_widget(self.scene.id, "camera", aspect=16 / 9)
        self.assertAlmostEqual(camera.width / camera.height, 16 / 9)
        self.assertGreater(camera.effects.corner_radius, 0)
        second = self.project.add_widget(self.scene.id, "camera")
        self.assertEqual(second.name, "Camera 2")
        mic = self.project.add_widget(self.scene.id, "microphone")
        self.assertFalse(mic.has_picture)
        self.assertTrue(mic.has_sound)
        with self.assertRaises(ModelError):
            self.project.add_widget(self.scene.id, "hologram")

    def test_stacking_and_rects(self):
        a = self.project.add_widget(self.scene.id, "display")
        b = self.project.add_widget(self.scene.id, "camera")
        self.project.move_widget(b.id, 0)
        self.assertEqual([w.id for w in self.scene.widgets], [b.id, a.id])
        self.project.set_rect(b.id, 10, 20, 300, 200)
        self.assertEqual((b.x, b.y, b.width, b.height), (10, 20, 300, 200))
        with self.assertRaises(ModelError):
            self.project.set_rect(b.id, 0, 0, 0, 10)
        self.project.align(b.id, "right")
        self.assertEqual(b.x, 1920 - 300)
        self.project.align(b.id, "middle")
        self.assertEqual(b.y, (1080 - 200) / 2)
        scene, index, removed = self.project.remove_widget(b.id)
        self.assertNotIn(removed, scene.widgets)
        self.project.restore_widget(scene.id, index, removed)
        self.assertEqual(scene.widgets[0], removed)

    def test_scenes(self):
        second = self.project.add_scene()
        self.assertEqual(second.name, "Scene 2")
        self.project.activate(second.id)
        index, removed = self.project.remove_scene(second.id)
        self.assertEqual(self.project.active, self.scene.id)
        self.project.restore_scene(index, removed)
        self.assertEqual(self.project.scenes[1], removed)
        copy = self.project.duplicate_scene(self.scene.id)
        self.assertEqual(copy.name, "Starting soon copy")
        self.project.remove_scene(copy.id)
        self.project.remove_scene(removed.id)
        with self.assertRaises(ModelError):
            self.project.remove_scene(self.scene.id)
        with self.assertRaises(ModelError):
            self.project.rename_scene(self.scene.id, "  ")

    def test_format_keeps_relative_place(self):
        camera = self.project.add_widget(self.scene.id, "camera")
        cx = (camera.x + camera.width / 2) / 1920
        aspect = camera.width / camera.height
        self.project.set_format("vertical")
        self.assertEqual(self.project.canvas, (1080, 1920))
        self.assertAlmostEqual((camera.x + camera.width / 2) / 1080, cx)
        self.assertAlmostEqual(camera.width / camera.height, aspect)

    def test_effects_clamp_and_margin(self):
        fx = Effects(opacity=3, crop_left=0.9, stroke_color="#12ab3", shadow_blur=20, shadow_y=8).clamped()
        self.assertEqual(fx.opacity, 1)
        self.assertEqual(fx.crop_left, 0.49)
        self.assertEqual(fx.stroke_color, "FFFFFF")
        self.assertEqual(fx.margin, 39.0)
        self.assertEqual(Effects().margin, 0)

    def test_round_trip_and_store(self):
        camera = self.project.add_widget(self.scene.id, "camera")
        camera.source["restore-token"] = "abc"
        data = json.loads(json.dumps(self.project.to_dict()))
        again = Project.from_dict(data)
        self.assertEqual(again.to_dict(), self.project.to_dict())
        # A widget kind from a newer version is skipped, not fatal.
        data["scenes"][0]["widgets"].append({"id": "x", "kind": "hologram", "name": "X"})
        self.assertEqual(len(Project.from_dict(data).scenes[0].widgets), 1)
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(Path(tmp) / "project.json")
            self.assertEqual(store.load().scenes[0].name, "Starting soon")
            store.save(self.project)
            self.assertEqual(store.load().to_dict(), self.project.to_dict())
            store.path.write_text("{not json")
            self.assertEqual(len(store.load().scenes), 1)
            self.assertTrue((Path(tmp) / "project.damaged.json").exists())


if __name__ == "__main__":
    unittest.main()
