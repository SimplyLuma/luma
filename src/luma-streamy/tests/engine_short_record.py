#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A recording stopped a second after it started is a whole file: its EOS drains, the muxer writes
its index, and ffprobe reads a duration and both streams.

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_short_record.py OUT_DIR
"""
import json
import os
import subprocess
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


def main(out_dir: str) -> int:
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    project.add_widget(scene.id, "display")
    project.add_widget(scene.id, "microphone")
    engine = Engine(fake=True, preview=False)
    states = []
    engine.connect("output-state", lambda _e, kind, state: states.append((kind, state)))
    engine.start(project)
    run(1.0)
    for container in ("mkv", "mp4"):
        path = os.path.join(out_dir, f"short.{container}")
        states.clear()
        engine.start_record(path, container=container)
        run(1.2)
        engine.stop_record()
        t = time.monotonic()
        while ("record", "off") not in states and time.monotonic() - t < 25:
            run(0.2)
        assert ("record", "off") in states, f"{container}: the recording never finished"
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                                "-of", "json", path], capture_output=True, text=True)
        info = json.loads(probe.stdout or "{}")
        kinds = sorted(s["codec_type"] for s in info.get("streams", []))
        duration = float(info.get("format", {}).get("duration", 0) or 0)
        print(container, "streams", kinds, "duration", round(duration, 2), probe.stderr.strip()[:200])
        assert kinds == ["audio", "video"], f"{container}: streams {kinds}"
        assert duration > 0.5, f"{container}: duration {duration}"
    engine.stop()
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp"))
