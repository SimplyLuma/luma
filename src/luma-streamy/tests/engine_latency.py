#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Drag latency per stage: a widget's move (or resize) to the preview on screen, p50/p95 in ms.

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_latency.py [move|resize] [MAX_P95_MS]

Needs a display (the preview is a gtk4paintablesink shown in a window). Each step is timed on
time.monotonic() and followed by its program frame (buffer pts) through the graph:

    set        the engine sets the mixer pad (a move) or commits a lumaframe revision (a resize)
    frame      lumaframe's first buffer with that revision (resize only)
    arrived    that buffer is queued on its mixer pad (resize only)
    selected   glvideomixer's samples-selected for the first frame drawn with the new geometry
    mixed      that frame leaves the mixer
    queued     it leaves the preview queue
    half       it leaves the first glcolorscale
    scaled     it leaves the second glcolorscale
    sink       it reaches gtk4paintablesink
    shown      the paintable's invalidate-contents after it
    painted    the window's next frame-clock after-paint
"""
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gst, Gtk  # noqa: E402

from luma_streamy.engine import Engine  # noqa: E402
from luma_streamy.model import Project  # noqa: E402

STAGES = ("frame", "arrived", "selected", "mixed", "queued", "half", "scaled", "sink", "shown", "painted")
PREVIEW_WIDTH = int(os.environ.get("STREAMY_LATENCY_PREVIEW", "640"))


def run_for(ms: int) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[max(0, min(len(values) - 1, round(q * (len(values) - 1))))]


def main(mode: str, limit_ms: float | None) -> int:
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    box = project.add_widget(scene.id, "display")
    project.set_rect(box.id, 0, 0, 640, 360)
    engine = Engine(fake=True, preview=True)
    window = Gtk.Window(default_width=PREVIEW_WIDTH, default_height=PREVIEW_WIDTH * 9 // 16)
    picture = Gtk.Picture()
    window.set_child(picture)
    engine.connect("preview-changed", lambda e: picture.set_paintable(e.paintable))
    window.present()
    run_for(300)  # mapped, with its GL context, before the sink asks for it
    engine.set_preview_size(PREVIEW_WIDTH, 0)
    engine.start(project)
    run_for(2500)  # the fade-in and the first renegotiations are over

    pipeline = engine.pipeline
    branch = engine.branches[box.id]
    frame, pad = branch.elements[-1], branch.pad
    mixer = pipeline.get_by_name("mixer")
    clock = pipeline.get_clock()
    step: dict = {}  # the step under way: {"set": t, "target": (xpos, width), "rev": n, "pts": P, stage: t}
    steps: list[dict] = []

    def now() -> float:
        return time.monotonic()

    def on_frame(_pad, info):
        buffer = info.get_buffer()
        if step.get("rev") and "frame" not in step and buffer is not None and buffer.offset >= step["rev"]:
            step["frame"] = now()
        return Gst.PadProbeReturn.OK

    def on_selected(_mixer, _segment, pts, *_rest):
        if "set" not in step or "selected" in step:
            return
        if "rev" in step:
            sample = mixer.peek_next_sample(pad)
            buffer = sample.get_buffer() if sample is not None else None
            if buffer is None or buffer.offset < step["rev"]:
                return
        elif (pad.get_property("xpos"), pad.get_property("width")) != step["target"]:
            return
        step["selected"] = now()
        step["pts"] = pts
        step["selected-late"] = (clock.get_time() - pipeline.get_base_time() - pts) / 1e6

    def at(stage: str):
        def probe(_pad, info):
            buffer = info.get_buffer()
            if "pts" in step and stage not in step and buffer is not None and buffer.pts >= step["pts"]:
                step[stage] = now()
                step[f"{stage}-late"] = (clock.get_time() - pipeline.get_base_time() - buffer.pts) / 1e6
            return Gst.PadProbeReturn.OK
        return probe

    def on_invalidate(_paintable):
        if "sink" in step and "shown" not in step:
            step["shown"] = now()

    def on_paint(_clock):
        if "shown" in step and "painted" not in step:
            step["painted"] = now()

    def on_arrived(_pad, info):
        buffer = info.get_buffer()
        if "frame" in step and "arrived" not in step and buffer is not None and buffer.offset >= step["rev"]:
            step["arrived"] = now()
        return Gst.PadProbeReturn.OK

    frame.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, on_frame)
    pad.add_probe(Gst.PadProbeType.BUFFER, on_arrived)
    mixer.connect("samples-selected", on_selected)  # after the engine's: the geometry is applied
    for stage, element in (("mixed", "mixer"), ("queued", "preview-queue"), ("half", "preview-scale-half"),
                           ("scaled", "preview-scale")):
        pipeline.get_by_name(element).get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, at(stage))
    sink = pipeline.get_by_name("preview").get_property("sink")
    sink.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, at("sink"))
    engine.paintable.connect("invalidate-contents", on_invalidate)
    window.get_frame_clock().connect("after-paint", on_paint)

    # Every buffer's lateness (running time when it passes minus its pts), per element.
    lateness: dict[str, list[float]] = {}

    def late(name: str):
        def probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is not None and buffer.pts != Gst.CLOCK_TIME_NONE:
                lateness.setdefault(name, []).append((clock.get_time() - pipeline.get_base_time() - buffer.pts) / 1e6)
            return Gst.PadProbeReturn.OK
        return probe

    for element in branch.elements[:1] + branch.elements[-4:] + [mixer]:
        element.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, late(element.get_name().split("-")[-1]))
    sink.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, late("preview"))

    moves = [(900, 640), (100, 480)] * 20
    loop = GLib.MainLoop()

    def move() -> bool:
        if step:
            steps.append(dict(step))
            step.clear()
        if not moves:
            loop.quit()
            return False
        x, width = moves.pop()
        if mode == "move":
            width = 640
        project.set_rect(box.id, x, 0, width, width * 9 // 16)
        m = int(round(box.effects.clamped().margin))
        step["target"] = (x - m, width + 2 * m)
        if mode == "resize":
            step["rev"] = engine._revision + 1  # the revision this resize commits
        step["set"] = now()
        engine.update_widget(box)
        return True

    rng = random.Random(1)  # steps at random phases of the program frame

    def tick() -> bool:
        if move():
            GLib.timeout_add(250 + rng.randrange(34), tick)
        return False

    GLib.timeout_add(300, tick)
    loop.run()
    engine.stop()

    complete = [s for s in steps if "painted" in s]
    print(f"{mode}: {len(complete)} of {len(steps)} steps reached the screen; preview {PREVIEW_WIDTH} px wide")
    print(f"  {'stage':<10}{'+p50':>8}{'+p95':>8}{'total p50':>11}{'total p95':>11}{'late p50':>10}")
    previous = "set"
    for stage in STAGES:
        have = [s for s in complete if stage in s]
        if not have:
            continue
        delta = [(s[stage] - s[previous]) * 1000 for s in have if previous in s]
        total = [(s[stage] - s["set"]) * 1000 for s in have]
        print(f"  {stage:<10}{percentile(delta, .5):8.1f}{percentile(delta, .95):8.1f}"
              f"{percentile(total, .5):11.1f}{percentile(total, .95):11.1f}", end="")
        late = [s[f"{stage}-late"] for s in have if f"{stage}-late" in s]
        print(f"{percentile(late, .5):10.1f}" if late else "")
        previous = stage
    print("  lateness (running time - pts) p50/p95: " + ", ".join(
        f"{name} {percentile(v, .5):.1f}/{percentile(v, .95):.1f}" for name, v in lateness.items()))
    if not complete:
        print("no step reached the screen")
        return 1
    total = [(s["painted"] - s["set"]) * 1000 for s in complete]
    print(f"  set to painted: p50 {statistics.median(total):.1f} ms, p95 {percentile(total, .95):.1f} ms")
    if limit_ms is not None and percentile(total, .95) > limit_ms:
        print(f"p95 over {limit_ms} ms")
        return 1
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "move",
                  float(sys.argv[2]) if len(sys.argv) > 2 else None))
