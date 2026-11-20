#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Headless engine check: record 10 s to a file and stream 10 s to an RTMP server.

    STREAMY_FAKE_SOURCES=1 GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_smoke.py OUT_DIR RTMP_URL

RTMP_URL "-" checks the recording only.

Fake sources stand in for the portals (test patterns and test tones). Prints
one line per step; exits non-zero when something fails.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy.engine import Engine, choose_encoders  # noqa: E402
from luma_streamy.model import Project  # noqa: E402


def run_for(seconds: float) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def main() -> int:
    out_dir, url = sys.argv[1], sys.argv[2]
    project = Project.starter()
    scene = project.active_scene
    project.add_widget(scene.id, "display")
    camera = project.add_widget(scene.id, "camera")
    camera.effects.corner_radius = 32
    project.add_widget(scene.id, "microphone")
    second = project.add_scene("Just chatting")
    project.add_widget(second.id, "camera")

    engine = Engine(fake=True, preview=False)
    try:
        return checks(engine, project, scene, second, out_dir, url)
    finally:
        engine.stop()


def checks(engine, project, scene, second, out_dir, url) -> int:
    levels, states, problems = [], [], []
    engine.connect("levels", lambda _e, wid, l, r: levels.append((wid, l, r)))
    engine.connect("output-state", lambda _e, kind, state: states.append((kind, state)))
    engine.connect("problem", lambda _e, text: problems.append(text))
    t = time.process_time()
    engine.start(project)
    run_for(2)
    print("branches", sorted(engine.branches), "levels", len(levels))
    assert len(engine.branches) == 3, engine.branches
    assert levels, "no level messages"
    for codec in ("h264", "av1"):
        print("encoder", codec, choose_encoders(codec))

    record = os.path.join(out_dir, "smoke.mkv")
    engine.start_record(record, container="mkv")
    run_for(5)
    # Switch scenes while recording: the old scene's captures stop, the recording goes on.
    project.activate(second.id)
    engine.show_scene(project.active_scene)
    assert len(engine.branches) == 1, engine.branches
    run_for(5.2)
    stats = engine.stats("record")
    print("record kbps", round(stats.bitrate_kbps), "frames", stats.frames, "dropped", stats.dropped)
    engine.stop_record()
    for _ in range(100):  # the file is finished when its EOS has drained (slow on software GL)
        if ("record", "off") in states:
            break
        run_for(0.2)
    assert ("record", "off") in states, states

    if url == "-":
        return 0 if not problems else 1  # no RTMP server: the recording is the check
    engine.start_stream(url, video_kbps=4000)
    run_for(10)
    stats = engine.stats("stream")
    print("stream kbps", round(stats.bitrate_kbps) if stats else None, "states", states)
    assert stats is not None and stats.bitrate_kbps > 50, stats
    engine.stop_stream()
    run_for(2)
    engine.stop()
    print("cpu seconds", round(time.process_time() - t, 2), "problems", problems)
    # Idle: nothing captures once stopped.
    assert not engine.branches and not engine.outputs
    return 0 if not problems else 1


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main())
