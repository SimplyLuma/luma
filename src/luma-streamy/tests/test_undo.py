# SPDX-License-Identifier: Apache-2.0
import json
import unittest

from luma_streamy.model import Project
from luma_streamy.undo import COALESCE_S, History


def snap(project: Project) -> str:
    return json.dumps(project.to_dict(), sort_keys=True)


class UndoTest(unittest.TestCase):
    def setUp(self):
        self.project = Project.starter()
        self.scene = self.project.active_scene
        self.history = History()

    def edit(self, label, change, key=None, now=None):
        """Do ``change`` as the window does: a checkpoint first. Then undo, redo and undo again must
        give back exactly the project before and after it."""
        before = snap(self.project)
        self.history.checkpoint(label, self.project.to_dict(), key=key, now=now)
        change(self.project)
        after = snap(self.project)
        step = self.history.undo(self.project.to_dict())
        self.assertEqual(step.label, label)
        self.project = Project.from_dict(step.project)
        self.assertEqual(snap(self.project), before, f"undo {label}")
        step = self.history.redo(self.project.to_dict())
        self.project = Project.from_dict(step.project)
        self.assertEqual(snap(self.project), after, f"redo {label}")
        return before, after

    def widget(self, kind):
        return next(w for w in self.project.active_scene.widgets if w.kind == kind)

    def test_every_edit_round_trips(self):
        def add(p):
            p.add_widget(p.active_scene.id, "image", source={"size": [800, 600], "path": "/x.png"})

        self.edit("Add image", add)
        image = self.widget("image")
        self.edit("Move", lambda p: p.set_rect(image.id, 10, 20, 400, 300))
        self.edit("Fill canvas", lambda p: p.align(image.id, "fill"))
        self.edit("Look", lambda p: setattr(p.active_scene.widget(image.id).effects, "corner_radius", 40))
        self.edit("Crop", lambda p: setattr(p.active_scene.widget(image.id).effects, "crop_left", 0.2))
        self.edit("Order", lambda p: p.move_widget(image.id, 0))
        self.edit("Hide", lambda p: setattr(p.active_scene.widget(image.id), "visible", False))
        self.edit("Rename", lambda p: setattr(p.active_scene.widget(image.id), "name", "Logo"))
        self.edit("Source", lambda p: p.active_scene.widget(image.id).source.update(device="usb"))
        self.edit("Canvas", lambda p: p.set_format("vertical"))
        self.edit("Move on vertical", lambda p: p.set_rect(image.id, 1, 2, 300, 225))
        self.edit("New scene", lambda p: p.add_scene("Talk"))
        self.edit("Rename scene", lambda p: p.rename_scene(p.active_scene.id, "Main"))
        self.edit("Delete scene", lambda p: p.remove_scene(p.scenes[-1].id))

    def test_delete_comes_back_exactly(self):
        display = self.project.add_widget(self.scene.id, "display")
        display.source["restore-token"] = "token-1"
        display.effects.corner_radius = 12
        before, _after = self.edit("Delete Display", lambda p: p.remove_widget(display.id))
        step = self.history.undo(self.project.to_dict())
        restored = Project.from_dict(step.project)
        twin = restored.active_scene.widget(display.id)
        self.assertEqual(snap(restored), before)
        self.assertEqual(twin.source["restore-token"], "token-1")
        self.assertEqual(restored.active_scene.widgets.index(twin),
                         [w.id for w in self.project.active_scene.widgets].index(display.id)
                         if display.id in [w.id for w in self.project.active_scene.widgets] else
                         restored.active_scene.widgets.index(twin))

    def test_a_drag_is_one_step(self):
        w = self.project.add_widget(self.scene.id, "camera")
        start = snap(self.project)
        for i in range(10):  # ten moves, each within the coalescing time of the last
            self.history.checkpoint("Move Camera", self.project.to_dict(), key=(w.id, "rect"), now=i * 0.1)
            self.project.set_rect(w.id, i, i, 100, 100)
        self.assertEqual(len(self.history.undos), 1)
        self.project = Project.from_dict(self.history.undo(self.project.to_dict()).project)
        self.assertEqual(snap(self.project), start)
        # Later than the coalescing time, or another key: a new step.
        self.history.checkpoint("Move", self.project.to_dict(), key="a", now=100)
        self.history.checkpoint("Move", self.project.to_dict(), key="a", now=100 + COALESCE_S + 0.1)
        self.history.checkpoint("Look", self.project.to_dict(), key="b", now=100 + COALESCE_S + 0.2)
        self.assertEqual(len(self.history.undos), 3)
        self.assertEqual(self.history.redos, [])


if __name__ == "__main__":
    unittest.main()
