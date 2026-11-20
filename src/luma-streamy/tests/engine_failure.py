#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A widget whose source fails, or never answers, comes out of the program with a reason,
and the main loop never waits on it.

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_failure.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy import engine as engine_module  # noqa: E402
from luma_streamy.engine import Engine  # noqa: E402
from luma_streamy.model import Project  # noqa: E402


def main() -> int:
    engine_module.FIRST_PICTURE_S = 2
    folder = tempfile.mkdtemp(prefix="streamy-failure-")
    stuck = os.path.join(folder, "stuck")
    os.mkfifo(stuck)  # opening it blocks until someone writes: a source that never answers

    project = Project.starter()
    scene = project.active_scene
    good = project.add_widget(scene.id, "display")
    missing = project.add_widget(scene.id, "camera", source={"fake-image": os.path.join(folder, "gone.png")})
    hung = project.add_widget(scene.id, "window", source={"fake-image": stuck})

    engine = Engine(fake=True, preview=False)
    failed: dict[str, str] = {}
    engine.connect("widget-failed", lambda _e, wid, reason: failed.setdefault(wid, reason))
    gaps = []
    last = [time.monotonic()]

    def tick() -> bool:
        now = time.monotonic()
        gaps.append(now - last[0])
        last[0] = now
        return True

    GLib.timeout_add(50, tick)
    engine.start(project)
    loop = GLib.MainLoop()
    GLib.timeout_add(4500, loop.quit)
    loop.run()
    print("failed", failed, "branches", sorted(engine.branches), "longest stall", round(max(gaps), 3))
    assert missing.id in failed and failed[missing.id] == "missing", failed
    assert hung.id in failed and failed[hung.id] == "silent", failed
    assert good.id in engine.branches and good.id not in failed
    assert max(gaps) < 0.5, f"the main loop stalled for {max(gaps):.2f} s"
    # Let the stuck source go, then stop everything without waiting on it.
    fd = os.open(stuck, os.O_RDWR | os.O_NONBLOCK)
    t = time.monotonic()
    engine.stop()
    assert time.monotonic() - t < 1.0
    os.close(fd)

    # Nothing to show and nothing sent: the program rests.
    empty = Project.starter()
    idle = Engine(fake=True, preview=False)
    idle.start(empty)
    loop = GLib.MainLoop()
    GLib.timeout_add(800, loop.quit)
    loop.run()
    state = idle.pipeline.get_state(Gst.SECOND)[1]
    print("empty scene", state.value_nick)
    assert state == Gst.State.PAUSED, state
    idle.stop()
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main())
