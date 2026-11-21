#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A picture widget with no live picture (its source failed) still draws its Look: the placeholder
card goes through its lumaframe, so a 20 px border shows on the program; while recording it is not drawn.

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_placeholder.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_streamy.engine import Engine  # noqa: E402
from luma_streamy.model import Effects, Project  # noqa: E402


def run(seconds: float) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def frame(engine: Engine):
    """One program picture as RGBA bytes (width, height, data)."""
    pipeline = engine.pipeline
    tap = Gst.parse_bin_from_description("queue ! gldownload ! videoconvert ! video/x-raw,format=RGBA ! "
                                         "appsink name=probe emit-signals=false max-buffers=1 drop=true sync=false",
                                         True)
    pipeline.add(tap)
    pipeline.get_by_name("program-landscape").link(tap)
    tap.sync_state_with_parent()
    sink = tap.get_by_name("probe")
    sample = None
    for _ in range(40):
        run(0.1)
        sample = sink.emit("try-pull-sample", 0)
        if sample is not None:
            break
    assert sample is not None, "no program picture"
    caps = sample.get_caps().get_structure(0)
    ok, info = sample.get_buffer().map(Gst.MapFlags.READ)
    data = bytes(info.data)
    sample.get_buffer().unmap(info)
    return caps.get_value("width"), caps.get_value("height"), data


def pixel(shot, x, y):
    width, _height, data = shot
    i = (y * width + x) * 4
    return tuple(data[i:i + 3])


def main() -> int:
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    camera = project.add_widget(scene.id, "camera", source={"fake-image": "/nonexistent/camera.png"})
    project.set_rect(camera.id, 200, 200, 640, 360)
    camera.effects = Effects(stroke_width=20, stroke_color="FFFFFF")
    engine = Engine(fake=True, preview=False)
    failed = []
    engine.connect("widget-failed", lambda _e, wid, reason: failed.append(wid))
    engine.start(project)
    run(1.5)
    assert camera.id in failed, "the source should have failed"
    engine.show_scene(scene, skip={camera.id})
    run(1.0)
    assert camera.id in engine.placeholders, "no placeholder for the frameless widget"
    shot = frame(engine)
    border, card, outside = pixel(shot, 205, 380), pixel(shot, 520, 300), pixel(shot, 100, 100)
    print("border", border, "card", card, "outside", outside)
    assert min(border) > 230, f"the border isn't drawn: {border}"
    assert 30 < card[0] < 120 and abs(card[0] - card[2]) < 30, f"the card isn't drawn: {card}"
    assert max(outside) < 20, f"the background changed: {outside}"
    # While sending, the card is the preview's only: transparent on the program.
    engine.outputs["fake"] = object()
    engine._sync_placeholder_alpha()
    run(0.5)
    shot = frame(engine)
    print("sending: border", pixel(shot, 205, 380))
    assert max(pixel(shot, 205, 380)) < 20, "the placeholder leaks into what is sent"
    engine.outputs.clear()
    engine.stop()
    run(0.5)  # the program's own threads finish before the interpreter goes
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main())
