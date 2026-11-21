#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""After a start and after a restart, every picture widget is drawn on both canvases within 2 s: a still
image, live captures, and a widget whose source is missing (its placeholder card). A still that met a
compositor not yet running would stop for good and leave its place black.

    GST_PLUGIN_PATH=<lumaframe> python3 tests/engine_relaunch.py
"""
import os
import sys
import tempfile
import time

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


def still(path: str) -> None:
    """A 64 × 36 orange PNG."""
    pipeline = Gst.parse_launch(f"videotestsrc num-buffers=1 pattern=solid-color foreground-color=0xFFF0A030 ! "
                                f"video/x-raw,width=64,height=36 ! pngenc ! filesink location={path}")
    pipeline.set_state(Gst.State.PLAYING)
    pipeline.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipeline.set_state(Gst.State.NULL)


class Tap:
    """The latest program picture of one canvas, as RGBA bytes."""

    def __init__(self, engine: Engine, fmt: str) -> None:
        self.bin = Gst.parse_bin_from_description(
            "queue leaky=2 max-size-buffers=1 ! gldownload ! videoconvert ! video/x-raw,format=RGBA ! "
            "appsink name=probe emit-signals=false max-buffers=1 drop=true sync=false", True)
        engine.pipeline.add(self.bin)
        engine.pipeline.get_by_name(f"program-{fmt}").link(self.bin)
        self.bin.sync_state_with_parent()
        self.sink = self.bin.get_by_name("probe")

        self.sample = None

    def pixel(self, x: int, y: int):
        latest = self.sink.emit("try-pull-sample", 0)
        self.sample = latest or self.sample  # the newest picture; kept until a newer one comes
        sample = self.sample
        if sample is None:
            return None
        caps = sample.get_caps().get_structure(0)
        width = caps.get_value("width")
        ok, info = sample.get_buffer().map(Gst.MapFlags.READ)
        i = (y * width + x) * 4
        rgb = tuple(info.data[i:i + 3])
        sample.get_buffer().unmap(info)
        return rgb


def drawn(rgb) -> bool:
    return rgb is not None and max(rgb) > 40


def check(engine: Engine, project: Project, widgets, label: str) -> None:
    """Every widget's centre is drawn on both canvases within 2 s of the start."""
    started = time.monotonic()
    taps = {fmt: Tap(engine, fmt) for fmt in engine.shown_canvases}
    missing = []
    while time.monotonic() - started < float(os.environ.get("STREAMY_DRAWN_S", "2")):
        run(0.1)
        missing = []
        for fmt, tap in taps.items():
            for w in widgets:
                v = project.view_on(w, fmt)
                if not drawn(tap.pixel(int(v.x + 3), int(v.y + v.height / 2))):
                    missing.append((fmt, w.name))
        if not missing:
            break
    print(f"{label}: all drawn after {time.monotonic() - started:.2f} s" if not missing else f"{label}: {missing}")
    if missing:
        for fmt, program in engine.programs.items():
            print(fmt, "mixer pads", program.mixer.numsinkpads, program.mixer.get_state(0)[1].value_nick)
        for b in list(engine.branches.values()) + list(engine.placeholders.values()):
            print(b.widget_id, "layers", {f: (l.frame.get_state(0)[1].value_nick,
                                              l.frame.get_static_pad("src").get_current_caps() is not None)
                                          for f, l in b.layers.items()},
                  "head", b.elements[0].get_state(0)[1].value_nick, "shown", b.shown, "fade", b.fade)
        print("vertical grid", [taps["vertical"].pixel(x, y) for x in (30, 200, 540) for y in (700, 800, 960)],
              {w.name: tuple(round(c) for c in (project.view_on(w, "vertical").x, project.view_on(w, "vertical").y,
                                                project.view_on(w, "vertical").width)) for w in widgets})
        print("pixels", {(fmt, w.name): taps[fmt].pixel(int(project.view_on(w, fmt).x + 3),
                                                        int(project.view_on(w, fmt).y + project.view_on(w, fmt).height / 2))
                         for fmt, w_name in missing for w in widgets if w.name == w_name})
    assert not missing, f"{label}: not drawn in time: {missing}"
    for tap in taps.values():
        tap.bin.set_locked_state(True)
        tap.bin.set_state(Gst.State.NULL)
        engine.pipeline.remove(tap.bin)


def main() -> int:
    folder = tempfile.mkdtemp(prefix="streamy-relaunch-")
    image_path = os.path.join(folder, "still.png")
    still(image_path)
    project = Project.starter()
    scene = project.active_scene
    for w in list(scene.widgets):
        project.remove_widget(w.id)
    image = project.add_widget(scene.id, "image", source={"fake-image": image_path})
    window = project.add_widget(scene.id, "window")
    camera = project.add_widget(scene.id, "camera")
    gone = project.add_widget(scene.id, "display", source={"fake-image": os.path.join(folder, "gone.png")})
    project.add_widget(scene.id, "microphone")
    for w, rect in ((image, (40, 40, 400, 225)), (window, (480, 40, 400, 225)), (camera, (40, 400, 400, 225)),
                    (gone, (480, 400, 400, 225))):
        project.set_rect(w.id, *rect)
        w.effects = Effects(stroke_width=8, stroke_color="FFFFFF")  # a border shows wherever the layer is drawn
    engine = Engine(fake=True, preview=False)
    # Start order: a canvas's compositor is running before any layer links to it (a still answered
    # FLUSHING by a mixer not yet started stops for good).
    late = []
    add_layer = engine._add_layer

    def checked(branch, fmt, *args, **kwargs):
        program = engine.programs.get(fmt)
        if program is not None and program.mixer.get_state(0)[1] != Gst.State.PLAYING:
            late.append((branch.widget_id, fmt, program.mixer.get_state(0)[1].value_nick))
        return add_layer(branch, fmt, *args, **kwargs)

    engine._add_layer = checked
    engine.view_of = lambda widget, fmt: project.view_on(widget, fmt)  # what the window sets
    engine.set_canvases(tuple(os.environ.get("STREAMY_CANVASES", "landscape,vertical").split(",")))
    failed: set[str] = set()

    def on_failed(_engine, widget_id: str, _reason: str) -> None:
        failed.add(widget_id)
        engine.show_scene(scene, skip=failed)  # what the window does: the card takes its place

    engine.connect("widget-failed", on_failed)
    widgets = [image, window, camera, gone]
    engine.start(project)
    assert not late, f"layers linked to a compositor not yet running: {late}"
    check(engine, project, widgets, "start")
    engine.stop()
    run(0.5)
    engine.start(project)  # a relaunch: everything comes back, stills included
    check(engine, project, widgets, "restart")
    engine.stop()
    run(0.5)
    return 0


if __name__ == "__main__":
    Gst.init(None)
    sys.exit(main())
