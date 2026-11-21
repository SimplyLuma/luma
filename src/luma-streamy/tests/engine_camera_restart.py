#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A camera reaches its first picture within 1 s on every start, restart, mode change and scene switch,
and a new stream never opens the device while the last one still holds it (a camera's PipeWire node
takes one format at a time: a stream asking another mode beside the old one gets no picture).

    STREAMY_FAKE_DEVICES=1 GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_camera_restart.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("STREAMY_FAKE_DEVICES", "1")

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy.engine import Engine  # noqa: E402
from luma_streamy.model import Project  # noqa: E402

RESTARTS = int(os.environ.get("N_RESTARTS", "20"))
MODES = int(os.environ.get("N_MODES", "50"))
FIRST_S = 1.0


def main() -> int:
    project = Project.starter()
    scene = project.active_scene
    camera = project.add_widget(scene.id, "camera", source={"camera": "v4l2_input.test", "mode": [1920, 1080, 30]})
    project.set_rect(camera.id, 40, 40, 640, 360)
    other = project.add_scene("Other")
    twin = project.add_widget(other.id, "camera", source={"camera": "v4l2_input.test", "mode": [1280, 720, 30]})
    engine = Engine(fake=True, preview=False)
    engine.view_of = lambda widget, fmt: project.view_on(widget, fmt)
    engine.set_canvases(("landscape",))
    heads: list = []  # every stream opened on the device, oldest first
    overlaps: list = []
    add_branch = engine._add_branch

    def checked(widget, zorder):
        before = set(engine.branches)
        add_branch(widget, zorder)
        branch = engine.branches.get(widget.id)
        if branch is not None and widget.id not in before:
            live = [h.get_name() for h in heads if h.get_state(0)[1] != Gst.State.NULL]
            if live:
                overlaps.append((branch.elements[0].get_name(), live))
            heads.append(branch.elements[0])

    engine._add_branch = checked
    ready: list[float] = []
    engine.connect("widget-ready", lambda _e, _wid: ready.append(time.monotonic()))

    def first_picture(label: str, action) -> None:
        count = len(ready)
        started = time.monotonic()
        action()
        context = GLib.MainContext.default()
        while len(ready) == count and time.monotonic() - started < FIRST_S + 2:
            context.iteration(False)
            time.sleep(0.002)
        took = (ready[-1] - started) if len(ready) > count else None
        assert took is not None and took <= FIRST_S, f"{label}: no first picture within {FIRST_S} s ({took})"
        times.append(took)

    times: list[float] = []
    first_picture("start", lambda: engine.start(project))
    for i in range(RESTARTS):
        engine.stop()
        first_picture(f"restart {i}", lambda: engine.start(project))
    modes = ([1920, 1080, 30], [1920, 1080, 25], [3840, 2160, 30], [1280, 720, 30])
    for i in range(MODES):
        camera.source["mode"] = list(modes[i % len(modes)])

        def restart():  # what the window does on a new mode: drop the stream, show the scene again
            engine._remove_branch(camera.id)
            engine.show_scene(scene)
        first_picture(f"mode {i}", restart)
    for i in range(10):
        target = other if i % 2 == 0 else scene
        first_picture(f"scene {i}", lambda t=target: (project.activate(t.id), engine.show_scene(t)))
    engine.stop()
    assert not overlaps, f"a stream opened while another held the device: {overlaps[:3]}"
    times.sort()
    print(f"camera first picture: {len(times)} starts, p50 {times[len(times) // 2] * 1000:.0f} ms, "
          f"max {times[-1] * 1000:.0f} ms; no overlapping streams")
    del twin
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main())
