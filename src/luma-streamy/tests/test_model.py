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

    def test_format_switch_is_lossless(self):
        display = self.project.add_widget(self.scene.id, "display")
        camera = self.project.add_widget(self.scene.id, "camera", aspect=16 / 9)
        before = [(w.x, w.y, w.width, w.height) for w in (display, camera)]
        self.project.set_format("vertical")
        # Derived once: landscape across the vertical canvas, centred.
        self.assertEqual((display.x, display.width), (0, 1080))
        self.assertAlmostEqual(display.y, (1920 - 1080 * 1080 / 1920) / 2)
        self.project.set_rect(camera.id, 10, 20, 300, 169)  # an edit made on vertical
        self.project.set_format("landscape")
        self.assertEqual([(w.x, w.y, w.width, w.height) for w in (display, camera)], before)
        self.project.set_format("vertical")
        self.assertEqual((camera.x, camera.y, camera.width, camera.height), (10, 20, 300, 169))
        restored = Project.from_dict(json.loads(json.dumps(self.project.to_dict())))
        restored.set_format("landscape")
        self.assertEqual([(w.x, w.y, w.width, w.height) for w in restored.active_scene.widgets], before)

    def test_linked_placements(self):
        project = Project.starter()
        scene = project.active_scene
        for w in list(scene.widgets):
            project.remove_widget(w.id)
        wide = project.add_widget(scene.id, "display", source={"size": [5120, 1440]})
        project.place_natural(wide.id)
        old = project.add_widget(scene.id, "image", source={"size": [1024, 768]})
        project.place_natural(old.id)
        landscape = {w.id: (w.x, w.y, w.width, w.height) for w in scene.widgets}
        project.set_format("vertical")
        # Linked: landscape scaled to the vertical width and centred.
        s = 1080 / 1920
        for w in scene.widgets:
            lx, ly, lw, lh = landscape[w.id]
            self.assertAlmostEqual(w.x, lx * s)
            self.assertAlmostEqual(w.y, ly * s + (1920 - 1080 * s) / 2)
            self.assertAlmostEqual(w.width / w.height, lw / lh)
        # A primary edit moves every linked widget; a widget moved on vertical is its own there.
        project.set_rect(old.id, 10, 10, 200, 150)
        self.assertTrue(old.own.get("placement"))
        project.set_format("landscape")
        self.assertEqual((old.x, old.y, old.width, old.height), landscape[old.id])  # vertical edits never touch it
        project.set_rect(wide.id, 100, 100, 960, 270)
        project.set_format("vertical")
        self.assertAlmostEqual(wide.x, 100 * s)
        self.assertAlmostEqual(wide.width, 960 * s)
        self.assertEqual((old.x, old.y, old.width, old.height), (10, 10, 200, 150))
        # Match landscape: linked again, derived at once.
        project.match_primary(old.id)
        self.assertFalse(old.own.get("placement"))
        self.assertAlmostEqual(old.x, landscape[old.id][0] * s)
        # Round trip keeps link state and both canvases.
        project.set_rect(old.id, 5, 5, 100, 75)
        again = Project.from_dict(json.loads(json.dumps(project.to_dict())))
        twin = again.active_scene.widget(old.id)
        self.assertTrue(twin.own.get("placement"))
        again.set_format("landscape")
        self.assertEqual((twin.x, twin.y, twin.width, twin.height), landscape[old.id])
        # Added on vertical: its landscape placement is derived once from where it was put.
        project.set_format("vertical")
        late = project.add_widget(scene.id, "image", source={"size": [1080, 1920]})
        project.place_natural(late.id)
        project.set_format("landscape")
        self.assertTrue(0 <= late.x and late.x + late.width <= 1920 + 1e-6)
        project.reset_secondary(scene.id)
        self.assertFalse(any(w.own.get("placement") for w in scene.widgets))

    def test_sizes_keep_shape(self):
        for fmt in ("landscape", "vertical"):
            project = Project.starter(); project.set_format(fmt)
            scene = project.active_scene
            cw, ch = project.canvas
            for size in ((5120, 1440), (1024, 768), (1080, 1920)):
                w = project.add_widget(scene.id, "image", source={"size": list(size)})
                project.place_natural(w.id)
                aspect = size[0] / size[1]
                for how in ("fill", "fit", "fill-width", "fill-height"):
                    project.align(w.id, how)
                    self.assertAlmostEqual(w.width / w.height, aspect, places=6)
                project.align(w.id, "fit")
                self.assertTrue(abs(w.width - cw) < 1e-6 or abs(w.height - ch) < 1e-6)
                self.assertLessEqual(w.width, cw + 1e-6); self.assertLessEqual(w.height, ch + 1e-6)
                project.align(w.id, "fill")
                self.assertGreaterEqual(w.width, cw - 1e-6); self.assertGreaterEqual(w.height, ch - 1e-6)
                self.assertAlmostEqual(w.x + w.width / 2, cw / 2)
                project.align(w.id, "fill-width")
                self.assertAlmostEqual(w.width, cw)
                project.align(w.id, "original")
                self.assertEqual((w.width, w.height), (size[0], size[1]))
            # Order: front, back, one step.
            ids = [w.id for w in scene.widgets]
            project.move_widget(ids[0], len(ids))
            self.assertEqual(scene.widgets[-1].id, ids[0])
            project.move_widget(ids[0], 0)
            self.assertEqual(scene.widgets[0].id, ids[0])

    def test_natural_shapes(self):
        for size in ((5120, 1440), (3440, 1440), (1920, 1200), (1024, 768), (1080, 1920)):
            for fmt in ("landscape", "vertical"):
                project = Project.starter(); project.set_format(fmt)
                scene = project.active_scene
                cw, ch = project.canvas
                for kind in ("display", "image"):
                    w = project.add_widget(scene.id, kind, source={"size": list(size)})
                    project.place_natural(w.id)
                    self.assertAlmostEqual(w.width / w.height, size[0] / size[1], places=6)
                    share = 1.0 if kind == "display" else 0.5
                    self.assertLessEqual(w.width, cw * share + 1e-6)
                    self.assertLessEqual(w.height, ch * share + 1e-6)
                    self.assertTrue(abs(w.width - cw * share) < 1e-6 or abs(w.height - ch * share) < 1e-6)
                    self.assertAlmostEqual(w.x + w.width / 2, cw / 2)
                    self.assertAlmostEqual(w.y + w.height / 2, ch / 2)
                    # Squashed by an older Streamy: restored on load; stretched on purpose: kept.
                    project.set_rect(w.id, w.x, w.y, w.width, w.width / 2)
                    project.restore_shapes()
                    self.assertAlmostEqual(w.width / w.height, size[0] / size[1], places=6)
                    w.stretched = True
                    project.set_rect(w.id, w.x, w.y, w.width, w.width / 2)
                    project.restore_shapes()
                    self.assertAlmostEqual(w.width / w.height, 2.0)
                    cx = w.x + w.width / 2
                    project.reset_aspect(w.id)
                    self.assertFalse(w.stretched)
                    self.assertAlmostEqual(w.width / w.height, size[0] / size[1], places=6)
                    self.assertAlmostEqual(w.x + w.width / 2, cx)

    def test_human_names(self):
        self.assertEqual(self.project.human_name(self.scene.id, "image", "Logo"), "Logo")
        self.assertEqual(self.project.human_name(self.scene.id, "image", "image-1793375857294"), "Image")
        w = self.project.add_widget(self.scene.id, "image", name="image-1793375857294")
        self.project.tidy_names()
        self.assertEqual(w.name, "Image")

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
