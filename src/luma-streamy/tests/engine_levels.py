#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A microphone added to a running scene meters at once, without the program being rebuilt.

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_levels.py   (STREAMY_TEST_PREVIEW=1 under a display)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy.engine import Engine  # noqa: E402
from luma_streamy.model import Project  # noqa: E402


def run(seconds: float) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def main() -> int:
    preview = bool(int(os.environ.get("STREAMY_TEST_PREVIEW", "0")))
    if preview:
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        Gtk.init()
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    project.add_widget(scene.id, "display")
    engine = Engine(fake=True, preview=preview)
    first: dict[str, float] = {}
    engine.connect("levels", lambda _e, wid, _l, _r: first.setdefault(wid, time.monotonic()))
    engine.start(project)
    run(1.5)
    program = engine.pipeline
    mic = project.add_widget(scene.id, "microphone")
    added = time.monotonic()
    engine.show_scene(scene)
    run(1.0)
    assert engine.pipeline is program, "the program was rebuilt"
    assert mic.id in first, "no level from the microphone"
    delay = first[mic.id] - added
    print(f"first level {delay * 1000:.0f} ms after the microphone was added")
    assert delay <= 0.2, f"first level after {delay * 1000:.0f} ms"
    _ok, state, _pending = program.get_state(0)
    assert state == Gst.State.PLAYING, f"program is {state.value_nick}"
    engine.stop()
    run(0.5)

    # A source that fails as it starts (a camera that is gone) fails the program's own state change;
    # the program must still run on, so a microphone beside it meters.
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    project.add_widget(scene.id, "display")
    project.add_widget(scene.id, "camera", source={"fake-image": "/nonexistent/camera.png"})
    engine = Engine(fake=True, preview=preview)
    first.clear()
    failed: set[str] = set()
    engine.connect("levels", lambda _e, wid, _l, _r: first.setdefault(wid, time.monotonic()))
    engine.connect("widget-failed", lambda _e, wid, _reason: failed.add(wid))
    engine.start(project)
    run(1.5)
    assert failed, "the camera should have failed"
    engine.show_scene(scene, skip=failed)  # the window shows the failure (its card) at once
    run(1.0)
    mic = project.add_widget(scene.id, "microphone")
    added = time.monotonic()
    engine.show_scene(scene, skip=failed)  # as the window does: a failed source waits for Try again
    run(1.0)
    _ok, state, _pending = engine.pipeline.get_state(0)
    assert state == Gst.State.PLAYING, f"beside a failed source the program is {state.value_nick}"
    assert mic.id in first, "no level beside a failed source"
    assert first[mic.id] - added <= 0.2, f"first level beside a failed source after {(first[mic.id] - added) * 1000:.0f} ms"
    print(f"beside a failed source: first level {(first[mic.id] - added) * 1000:.0f} ms")
    engine.stop()
    run(0.5)  # the programs' own threads finish before the interpreter goes
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main())
