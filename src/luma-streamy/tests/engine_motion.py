#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Motion to picture: how long a widget's move (its mixer pad) takes to show on the program, p50/p95.

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_motion.py [MAX_P95_MS]
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy.engine import Engine  # noqa: E402
from luma_streamy.model import Project  # noqa: E402


def main(limit_ms: float | None) -> int:
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    box = project.add_widget(scene.id, "display")
    project.set_rect(box.id, 0, 0, 200, 200)
    engine = Engine(fake=True, preview=False)
    engine.start(project)
    loop = GLib.MainLoop()
    GLib.timeout_add(1500, loop.quit)
    loop.run()
    tap = Gst.parse_bin_from_description("queue max-size-buffers=1 leaky=downstream ! gldownload ! videoconvert ! "
                                         "video/x-raw,format=RGBA ! appsink name=probe emit-signals=true "
                                         "max-buffers=1 drop=true sync=false", True)
    engine.pipeline.add(tap)
    engine.pipeline.get_by_name("program").link(tap)
    tap.sync_state_with_parent()
    state = {"target": None, "moved": 0.0, "delays": []}

    def sample(sink):
        s = sink.emit("pull-sample")
        if state["target"] is None:
            return Gst.FlowReturn.OK
        caps = s.get_caps().get_structure(0)
        width = caps.get_value("width")
        ok, info = s.get_buffer().map(Gst.MapFlags.READ)
        x = state["target"] + 100
        i = (100 * width + x) * 4
        lit = sum(info.data[i:i + 3]) > 60
        s.get_buffer().unmap(info)
        if lit:
            state["delays"].append((time.monotonic() - state["moved"]) * 1000)
            state["target"] = None
        return Gst.FlowReturn.OK

    tap.get_by_name("probe").connect("new-sample", sample)
    positions = [900, 100] * 15

    def move() -> bool:
        if not positions:
            loop.quit()
            return False
        x = positions.pop()
        project.set_rect(box.id, x, 0, 200, 200)
        state["moved"] = time.monotonic()
        state["target"] = x
        engine.update_widget(box)
        return True

    GLib.timeout_add(250, move)
    loop = GLib.MainLoop()
    loop.run()
    delays = sorted(state["delays"])
    p50 = statistics.median(delays)
    p95 = delays[int(len(delays) * 0.95) - 1]
    print(f"motion to program picture: p50 {p50:.0f} ms, p95 {p95:.0f} ms over {len(delays)} moves")
    engine.stop()
    if limit_ms is not None:
        assert p95 <= limit_ms, f"p95 {p95:.0f} ms over {limit_ms} ms"
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else None))
