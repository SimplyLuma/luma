#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Sweeping a widget's shadow never moves or scales its picture: in every program frame the content's
left edge stays where it is (only the shadow grows outward).

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_shadow_sweep.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy.engine import Engine  # noqa: E402
from luma_streamy.model import Project  # noqa: E402

X, Y, W, H = 300, 200, 640, 360


def main() -> int:
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    box = project.add_widget(scene.id, "window")
    project.set_rect(box.id, X, Y, W, H)
    engine = Engine(fake=True, preview=False)
    engine.start(project)
    loop = GLib.MainLoop()
    GLib.timeout_add(1500, loop.quit)
    loop.run()
    tap = Gst.parse_bin_from_description("queue max-size-buffers=4 ! gldownload ! videoconvert ! "
                                         "video/x-raw,format=RGBA ! appsink name=probe emit-signals=true "
                                         "max-buffers=8 drop=false sync=false", True)
    engine.pipeline.add(tap)
    engine.pipeline.get_by_name("program").link(tap)
    tap.sync_state_with_parent()
    edges = []

    def sample(sink):
        s = sink.emit("pull-sample")
        width = s.get_caps().get_structure(0).get_value("width")
        ok, info = s.get_buffer().map(Gst.MapFlags.READ)
        row = Y + H // 2
        edge = None
        for x in range(X - 40, X + 40):  # the first bright pixel: the picture, never its dark shadow
            i = (row * width + x) * 4
            if sum(info.data[i:i + 3]) > 150:
                edge = x
                break
        s.get_buffer().unmap(info)
        edges.append(edge)
        return Gst.FlowReturn.OK

    tap.get_by_name("probe").connect("new-sample", sample)
    steps = list(range(0, 121, 8)) + list(range(120, -1, -8))

    def step() -> bool:
        if not steps:
            loop.quit()
            return False
        blur = steps.pop(0)
        box.effects.shadow_blur, box.effects.shadow_opacity, box.effects.shadow_y = blur, 0.6, blur / 4
        engine.update_widget(box)
        return True

    GLib.timeout_add(70, step)
    loop = GLib.MainLoop()
    loop.run()
    seen = [e for e in edges if e is not None]
    print(f"{len(edges)} frames; content left edge at {sorted(set(seen))}")
    engine.stop()
    assert len(seen) > 20 and max(seen) - min(seen) <= 1, f"the picture moved: {sorted(set(seen))}"
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main())
