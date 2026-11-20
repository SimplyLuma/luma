# SPDX-License-Identifier: Apache-2.0
"""Streamy's engine: builds and edits the GStreamer graph from the model.

Python only builds and edits the graph; every frame stays in GStreamer.

- One program pipeline: the active scene's widgets → lumaframe → glvideomixer
  → tee (preview, and a tap for the encoders), and its sound widgets →
  audiomixer → tee. Only the active scene's visible widgets capture; a widget
  that leaves the scene or is hidden is set to NULL and taken out, so its
  capture stops. Its portal session stays open (no picker when it returns).
- Outputs (a recording, a stream) are pipelines of their own, fed from the
  taps by appsrc. They exist only while recording or live, and a failure in
  one (a dropped connection) never stops the program or the other output.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst  # noqa: E402

from . import graph  # noqa: E402
from .graph import Chain, Element, EncoderChoice  # noqa: E402
from .model import Project, Scene, Widget  # noqa: E402

FADE_MS = 300


def _set_prop(element: Gst.Element, name: str, value) -> None:
    if name == "caps":
        element.set_property("caps", Gst.Caps.from_string(value))
        return
    if name in ("stream-properties", "client-properties"):
        element.set_property(name, Gst.Structure.new_from_string(value))
        return
    if isinstance(value, str):
        spec = element.find_property(name)
        if spec is not None and spec.value_type.is_a(GObject.TYPE_ENUM) or (
                spec is not None and spec.value_type.is_a(GObject.TYPE_FLAGS)):
            Gst.util_set_object_arg(element, name, value)
            return
    element.set_property(name, value)


#: Properties GStreamer takes only when an element is made.
CONSTRUCT_ONLY = {"force-live"}


def make(spec: Element) -> Gst.Element:
    early = {k: v for k, v in spec.props.items() if k in CONSTRUCT_ONLY}
    if early:
        element = Gst.ElementFactory.make_with_properties(spec.factory, ["name", *early], [spec.name, *early.values()])
    else:
        element = Gst.ElementFactory.make(spec.factory, spec.name)
    if element is None:
        raise EngineError(f"GStreamer has no {spec.factory} element")
    for name, value in spec.props.items():
        if name not in CONSTRUCT_ONLY:
            _set_prop(element, name, value)
    return element


class EngineError(RuntimeError):
    pass


def link_all(elements) -> None:
    elements = list(elements)
    for a, b in zip(elements, elements[1:]):
        if not a.link(b):
            raise EngineError(f"{a.get_name()} does not link to {b.get_name()}")


def available_factories() -> set[str]:
    Gst.init(None)
    wanted = graph.H264 + graph.AV1 + graph.AAC + ("eflvmux", "lumaframe", "gtk4paintablesink")
    return {name for name in wanted if Gst.ElementFactory.find(name) is not None}


_WORKING: dict[str, bool] = {}


def encoder_works(factory: str) -> bool:
    """Whether an encoder really encodes (a stub library or a missing driver fails here)."""
    if factory in _WORKING:
        return _WORKING[factory]
    Gst.init(None)
    pipeline = None
    try:
        if factory.startswith("va"):
            desc = f"videotestsrc num-buffers=2 ! video/x-raw,format=NV12,width=320,height=240 ! {factory} ! fakesink"
        else:
            desc = f"videotestsrc num-buffers=2 ! video/x-raw,format=I420,width=320,height=240 ! {factory} ! fakesink"
        pipeline = Gst.parse_launch(desc)
        pipeline.set_state(Gst.State.PLAYING)
        message = pipeline.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        ok = message is not None and message.type == Gst.MessageType.EOS
    except GLib.Error:
        ok = False
    finally:
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
    _WORKING[factory] = ok
    return ok


def choose_encoders(codec: str) -> EncoderChoice | None:
    """The first encoder for ``codec`` that is installed and works, hardware first."""
    available = available_factories()
    order = graph.H264 if codec == "h264" else graph.AV1
    working = {f for f in available if f not in order or encoder_works(f)}
    return graph.choose(working, codec)


FIRST_PICTURE_S = 10


def source_problem(message: str, debug: str) -> str:
    """A source's GStreamer error as a short plain reason."""
    text = f"{message} {debug}".lower()
    if "busy" in text or "ebusy" in text or "in use" in text:
        return "It's in use by another app"
    if "target not found" in text or "no such" in text or "not found" in text:
        return "It isn't available"
    if "permission" in text or "not allowed" in text:
        return "Streamy isn't allowed to use it"
    return "It stopped"


@dataclass
class Branch:
    widget_id: str
    elements: list[Gst.Element]
    pad: Gst.Pad | None = None
    picture: bool = True
    fade: int = 0
    shown: bool = False
    watchdog: int = 0


@dataclass
class OutputStats:
    started: float = field(default_factory=time.monotonic)
    bytes: int = 0
    bitrate_kbps: float = 0.0
    dropped: int = 0
    frames: int = 0
    _window_bytes: int = 0
    _window_start: float = field(default_factory=time.monotonic)


class Output:
    """One recording or stream: appsrc ! encoders ! mux ! sink, in its own pipeline."""

    def __init__(self, engine: "Engine", kind: str, choice: EncoderChoice, sink: list[Element],
                 video_kbps: int, audio_kbps: int, with_sound: bool) -> None:
        self.engine, self.kind, self.choice = engine, kind, choice
        self.stats = OutputStats()
        self.pipeline = Gst.Pipeline.new(f"streamy-{kind}")
        width, height = engine.canvas
        self.video_src = Gst.ElementFactory.make("appsrc", f"{kind}-video")
        self.video_src.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=NV12,width={width},height={height},framerate={graph.FPS}/1"))
        for name, value in (("is-live", True), ("format", Gst.Format.TIME), ("do-timestamp", False),
                            ("max-buffers", graph.FPS * 2), ("block", False)):
            self.video_src.set_property(name, value)
        self.pipeline.add(self.video_src)
        video = [make(e) for e in graph.video_encoder_chain(choice, video_kbps, kind)]
        tail = [make(e) for e in sink]
        for element in video + tail:
            self.pipeline.add(element)
        self._link([self.video_src] + video)
        mux = tail[0]
        video[-1].link(mux)
        link_all(tail)
        self.sound_src = None
        if with_sound and choice.audio is not None:
            self.sound_src = Gst.ElementFactory.make("appsrc", f"{kind}-sound")
            self.sound_src.set_property("caps", Gst.Caps.from_string(
                f"audio/x-raw,format=S16LE,layout=interleaved,rate={graph.AUDIO_RATE},channels=2"))
            for name, value in (("is-live", True), ("format", Gst.Format.TIME), ("do-timestamp", False),
                                ("max-buffers", 200), ("block", False)):
                self.sound_src.set_property(name, value)
            self.pipeline.add(self.sound_src)
            sound = [make(e) for e in graph.audio_encoder_chain(choice, audio_kbps, kind)]
            for element in sound:
                self.pipeline.add(element)
            self._link([self.sound_src] + sound)
            sound[-1].link(mux)
        mux.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._count)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_id = bus.connect("message", self._message)
        self.on_error: Callable[[str], None] | None = None
        self.on_finished: Callable[[], None] | None = None
        self.t0: int | None = None
        self.stopping = False

    @staticmethod
    def _link(elements: list[Gst.Element]) -> None:
        for a, b in zip(elements, elements[1:]):
            if not a.link(b):
                raise EngineError(f"{a.get_name()} does not link to {b.get_name()}")

    def start(self, clock: Gst.Clock, base_time: int, running: int) -> None:
        # Share the program's clock; time starts at zero for this output.
        self.pipeline.use_clock(clock)
        self.pipeline.set_start_time(Gst.CLOCK_TIME_NONE)
        self.pipeline.set_base_time(base_time + running)
        self.t0 = running
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise EngineError(f"the {self.kind} could not start")

    def push(self, src: Gst.Element, sample: Gst.Sample) -> None:
        if self.stopping or src is None or self.t0 is None:
            return
        buffer = sample.get_buffer()
        if buffer.pts == Gst.CLOCK_TIME_NONE or buffer.pts < self.t0:
            return
        if src is self.video_src:
            if src.get_property("current-level-buffers") >= src.get_property("max-buffers"):
                self.stats.dropped += 1  # the encoder or the network is far behind
                return
            self.stats.frames += 1
        out = buffer.copy()
        out.pts = buffer.pts - self.t0
        out.dts = Gst.CLOCK_TIME_NONE
        src.emit("push-buffer", out)

    def _count(self, _pad, info) -> Gst.PadProbeReturn:
        buffer = info.get_buffer()
        if buffer is not None:
            size = buffer.get_size()
            self.stats.bytes += size
            self.stats._window_bytes += size
            now = time.monotonic()
            span = now - self.stats._window_start
            if span >= 1.0:
                self.stats.bitrate_kbps = self.stats._window_bytes * 8 / 1000 / span
                self.stats._window_bytes = 0
                self.stats._window_start = now
        return Gst.PadProbeReturn.OK

    def stop(self) -> None:
        """Finish cleanly: EOS through the encoders and the muxer, then stop."""
        if self.stopping:
            return
        self.stopping = True
        for src in (self.video_src, self.sound_src):
            if src is not None:
                src.emit("end-of-stream")
        # A stream whose connection is gone never sees its EOS: give up after 5 s.
        GLib.timeout_add_seconds(5, self._finish)

    def _message(self, _bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.EOS:
            self._finish()
        elif message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            if self.on_error is not None and not self.stopping:
                self.on_error(error.message)
            self._finish()

    def _finish(self) -> bool:
        if self.pipeline is None:
            return False
        bus = self.pipeline.get_bus()
        bus.disconnect(self._bus_id)
        bus.remove_signal_watch()
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        if self.on_finished is not None:
            self.on_finished()
        return False


class Engine(GObject.Object):
    """The program: the active scene composited, its sound mixed, and the outputs."""

    __gsignals__ = {
        # widget id, left dB, right dB
        "levels": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float)),
        # a plain sentence for a toast
        "problem": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # "record" | "stream", "starting" | "on" | "reconnecting" | "off"
        "output-state": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # widget id: its first picture arrived
        "widget-ready": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # widget id, a plain reason: its source failed or never sent a picture; it is out of the program
        "widget-failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, *, fake: bool | None = None, preview: bool = True,
                 sources: Callable[[Widget], tuple[int | None, str | None]] | None = None) -> None:
        super().__init__()
        Gst.init(None)
        self.fake = bool(int(os.environ.get("STREAMY_FAKE_SOURCES", "0"))) if fake is None else fake
        self.want_preview = preview
        self.sources = sources  # widget -> (pipewire fd, node) from the portals
        self.pipeline: Gst.Pipeline | None = None
        self.paintable = None
        self.canvas = (1920, 1080)
        self.branches: dict[str, Branch] = {}
        self.outputs: dict[str, Output] = {}
        self.stream_retry: dict | None = None
        self._sound = False
        self._taps: dict[str, list[Gst.Element]] = {}
        self._zorder = 0
        self.preview_size: tuple[int, int] | None = None

    # ── The program pipeline ──

    def start(self, project: Project) -> None:
        if self.pipeline is not None:
            return
        self.canvas = project.canvas
        self.pipeline = Gst.Pipeline.new("streamy-program")
        for spec in graph.program(self.canvas):
            self.pipeline.add(make(spec))
        link_all([self.pipeline.get_by_name(e.name) for e in graph.program(self.canvas)])
        if self.want_preview:
            self._add_preview()
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._message)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise EngineError("the preview could not start")
        self.show_scene(project.active_scene)

    def stop(self) -> None:
        """Everything off: outputs finish, captures stop, the pipeline goes."""
        for output in list(self.outputs.values()):
            output.stop()
        if self.pipeline is None:
            return
        for widget_id in list(self.branches):
            self._remove_branch(widget_id)
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_bus().remove_signal_watch()
        self.pipeline = None
        self._sound = False
        self._taps.clear()

    @property
    def running(self) -> bool:
        return self.pipeline is not None

    def set_canvas(self, project: Project) -> None:
        """A new canvas size means a new program (refused while an output runs)."""
        if self.outputs:
            raise EngineError("the canvas can't change while recording or live")
        was = self.running
        self.stop()
        if was:
            self.start(project)

    def _add_preview(self) -> None:
        sink = Gst.ElementFactory.make("gtk4paintablesink", "preview-sink")
        if sink is None:
            raise EngineError("GStreamer has no gtk4paintablesink (gstreamer1-plugin-gtk4)")
        self.paintable = sink.get_property("paintable")
        sink.set_property("sync", False)
        bin_ = Gst.ElementFactory.make("glsinkbin", "preview")
        bin_.set_property("sink", sink)
        # The preview is scaled on the GPU to the size it is shown at: no full-size copy for the screen.
        queue = make(Element("queue", "preview-queue", {"max-size-buffers": 1, "leaky": 2}))
        scale = make(Element("glcolorscale", "preview-scale"))
        size = make(Element("capsfilter", "preview-size", {"caps": self._preview_caps()}))
        for element in (queue, scale, size, bin_):
            self.pipeline.add(element)
        link_all([queue, scale, size, bin_])
        self.pipeline.get_by_name("program").link(queue)

    def _preview_caps(self) -> str:
        width, height = self.preview_size or (self.canvas[0] // 2, self.canvas[1] // 2)
        return f"video/x-raw(memory:GLMemory),width={width},height={height}"

    def set_preview_size(self, width: int, height: int) -> None:
        """Show the program at this many pixels (the picture's size on screen, in device pixels)."""
        cw, ch = self.canvas
        width = max(2, min(cw, int(width) // 2 * 2))
        height = max(2, min(ch, int(round(width * ch / cw)) // 2 * 2))
        if (width, height) == self.preview_size:
            return
        self.preview_size = (width, height)
        size = self.pipeline.get_by_name("preview-size") if self.pipeline is not None else None
        if size is not None:
            size.set_property("caps", Gst.Caps.from_string(self._preview_caps()))

    # ── Scenes and widgets ──

    def show_scene(self, scene: Scene | None, *, skip: set[str] | frozenset = frozenset()) -> None:
        """Capture exactly the visible widgets of ``scene`` (less ``skip``: ones that failed); stop the rest."""
        if self.pipeline is None:
            return
        wanted = {w.id: w for w in (scene.widgets if scene else []) if w.visible and w.id not in skip}
        for widget_id in [i for i in self.branches if i not in wanted]:
            self._remove_branch(widget_id)
        for zorder, widget in enumerate(scene.widgets if scene else []):
            if widget.id not in wanted:
                continue
            if widget.id in self.branches:
                self.update_widget(widget, zorder)
            else:
                self._add_branch(widget, zorder)
        self._sync_sound()
        self._idle_check()

    def _idle_check(self) -> None:
        """Nothing to show and nothing sent: the program rests (PAUSED), so it costs nothing."""
        if self.pipeline is None:
            return
        resting = not self.branches and not self.outputs
        target = Gst.State.PAUSED if resting else Gst.State.PLAYING
        if self.pipeline.get_state(0)[2] != target or self.pipeline.get_state(0)[1] != target:
            self.pipeline.call_async(lambda pipeline, state: pipeline.set_state(state), target)

    def update_widget(self, widget: Widget, zorder: int | None = None) -> None:
        """Move, resize or restyle a widget live (no renegotiation unless its size changed)."""
        branch = self.branches.get(widget.id)
        if branch is None:
            return
        if branch.picture:
            frame = branch.elements[-1]
            props = graph.frame_props(widget)
            if branch.fade < FADE_MS:
                props["opacity"] = props["opacity"] * branch.fade / FADE_MS
            for name, value in props.items():
                if frame.get_property(name) != value:
                    frame.set_property(name, value)
            pad_props = graph.mixer_pad_props(widget, zorder if zorder is not None else branch.pad.get_property("zorder"))
            for name, value in pad_props.items():
                if branch.pad.get_property(name) != value:
                    branch.pad.set_property(name, value)
        else:
            volume = branch.elements[-3]
            for name, value in graph.volume_props(widget).items():
                volume.set_property(name, value)

    def restack(self, scene: Scene) -> None:
        for zorder, widget in enumerate(scene.widgets):
            branch = self.branches.get(widget.id)
            if branch is not None and branch.pad is not None and branch.picture:
                branch.pad.set_property("zorder", zorder)

    def _add_branch(self, widget: Widget, zorder: int) -> None:
        fd = node = None
        if not self.fake and self.sources is not None and widget.kind in ("display", "window", "camera", "app-audio"):
            fd, node = self.sources(widget)
            if fd is None:
                return  # not chosen yet: the window asks the portal first
        if widget.has_picture:
            chain = graph.picture_chain(widget, zorder, fake=self.fake, fd=fd, node=node)
        else:
            chain = graph.sound_chain(widget, fake=self.fake, fd=fd, node=node)
            self._ensure_sound()
        elements = [make(e) for e in chain.elements]
        if widget.has_picture:
            elements[-1].set_property("opacity", 0.0)  # fades in after its first picture
        for element in elements:
            self.pipeline.add(element)
        self._link_chain(elements)
        target = self.pipeline.get_by_name(chain.target)
        pad = target.request_pad_simple("sink_%u")
        for name, value in chain.pad.items():
            pad.set_property(name, value)
        if elements[-1].get_static_pad("src").link(pad) != Gst.PadLinkReturn.OK:
            raise EngineError(f"{widget.name} could not join the program")
        branch = Branch(widget.id, elements, pad, widget.has_picture)
        self.branches[widget.id] = branch
        if widget.has_picture:
            elements[-1].get_static_pad("src").add_probe(
                Gst.PadProbeType.BUFFER, self._first_picture, (branch, widget))
        # State changes run on GStreamer's thread pool: a source that takes its time
        # (a camera opening, a portal stream negotiating) never holds up the window.
        self.pipeline.call_async(lambda _pipeline, items: [e.sync_state_with_parent() for e in reversed(items)],
                                 elements)
        if widget.has_picture:
            branch.watchdog = GLib.timeout_add_seconds(FIRST_PICTURE_S, self._no_picture, branch)

    def _no_picture(self, branch: Branch) -> bool:
        branch.watchdog = 0
        if self.branches.get(branch.widget_id) is branch and not branch.shown:
            self._fail(branch.widget_id, "It didn't send a picture")
        return False

    def _fail(self, widget_id: str, reason: str) -> None:
        if widget_id in self.branches:
            self._remove_branch(widget_id)
            self.emit("widget-failed", widget_id, reason)
            self._idle_check()

    @staticmethod
    def _link_chain(elements: list[Gst.Element]) -> None:
        for a, b in zip(elements, elements[1:]):
            if a.get_factory().get_name() == "decodebin":
                def pad_added(_bin, pad, following=b):
                    sink = following.get_static_pad("sink")
                    if not sink.is_linked() and pad.query_caps(None).to_string().startswith(("video/", "audio/")):
                        pad.link(sink)
                a.connect("pad-added", pad_added)
            elif not a.link(b):
                raise EngineError(f"{a.get_name()} does not link to {b.get_name()}")

    def _first_picture(self, _pad, _info, data) -> Gst.PadProbeReturn:
        branch, widget = data
        branch.shown = True
        GLib.idle_add(self._fade_in, branch, widget)
        return Gst.PadProbeReturn.REMOVE

    def _fade_in(self, branch: Branch, widget: Widget) -> bool:
        if self.branches.get(branch.widget_id) is not branch:
            return False
        self.emit("widget-ready", widget.id)
        steps = 10

        def step() -> bool:
            if self.branches.get(branch.widget_id) is not branch:
                return False
            branch.fade = min(FADE_MS, branch.fade + FADE_MS // steps)
            target = widget.effects.clamped().opacity
            branch.elements[-1].set_property("opacity", target * branch.fade / FADE_MS)
            return branch.fade < FADE_MS

        GLib.timeout_add(FADE_MS // steps, step)
        return False

    def _remove_branch(self, widget_id: str) -> None:
        """Take a widget out without waiting: detach it from the mixer when its pad is idle, then
        stop it on GStreamer's thread pool (a stuck source can take its time) and drop it."""
        branch = self.branches.pop(widget_id, None)
        if branch is None or self.pipeline is None:
            return
        if branch.watchdog:
            GLib.source_remove(branch.watchdog)
            branch.watchdog = 0
        pipeline = self.pipeline
        last = branch.elements[-1].get_static_pad("src")
        mixer_pad = branch.pad
        if mixer_pad is not None:
            # An unlinked live source posts not-linked; keep its data out of the way first.
            last.add_probe(Gst.PadProbeType.BLOCK_DOWNSTREAM, lambda *_a: Gst.PadProbeReturn.DROP)
            last.unlink(mixer_pad)
            mixer_pad.get_parent_element().release_request_pad(mixer_pad)

        def stop(_pipeline, elements) -> None:
            for element in elements:
                element.set_state(Gst.State.NULL)
            GLib.idle_add(drop, elements)

        def drop(elements) -> bool:
            for element in elements:
                if element.get_parent() is pipeline:
                    pipeline.remove(element)
            return False

        for element in branch.elements:
            element.set_locked_state(True)  # the pipeline's own state changes leave it alone now
        pipeline.call_async(stop, branch.elements)
        if not branch.picture:
            self._sync_sound()

    # ── Sound ──

    def _needs_sound(self) -> bool:
        return any(not b.picture for b in self.branches.values()) or bool(self.outputs)

    def _ensure_sound(self) -> None:
        if self._sound or self.pipeline is None:
            return
        elements = [make(e) for e in graph.sound_program()]
        for element in elements:
            self.pipeline.add(element)
        link_all(elements)
        for element in reversed(elements):
            element.sync_state_with_parent()
        self._sound = True

    def _sync_sound(self) -> None:
        """The sound mixer runs only while something makes or takes sound."""
        if self._needs_sound():
            self._ensure_sound()
            return
        if not self._sound or self.pipeline is None:
            return
        for spec in graph.sound_program():
            element = self.pipeline.get_by_name(spec.name)
            if element is not None:
                element.set_state(Gst.State.NULL)
                self.pipeline.remove(element)
        self._sound = False

    # ── Outputs ──

    def _ensure_taps(self) -> None:
        if "video" not in self._taps:
            elements = [make(e) for e in graph.tap_video()]
            self._attach_tap("video", "program", elements)
        if "sound" not in self._taps and self._sound:
            elements = [make(e) for e in graph.tap_sound()]
            self._attach_tap("sound", "sound", elements)

    def _attach_tap(self, key: str, tee_name: str, elements: list[Gst.Element]) -> None:
        for element in elements:
            self.pipeline.add(element)
        link_all(elements)
        tee = self.pipeline.get_by_name(tee_name)
        tee.link(elements[0])
        elements[-1].connect("new-sample", self._sample, key)
        for element in reversed(elements):
            element.sync_state_with_parent()
        self._taps[key] = elements

    def _drop_taps(self) -> None:
        for key, elements in list(self._taps.items()):
            first = elements[0].get_static_pad("sink")
            peer = first.get_peer()
            for element in elements:
                element.set_state(Gst.State.NULL)
            if peer is not None:
                peer.unlink(first)
                peer.get_parent_element().release_request_pad(peer)
            for element in elements:
                self.pipeline.remove(element)
        self._taps.clear()

    def _sample(self, sink: Gst.Element, key: str) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        for output in list(self.outputs.values()):
            output.push(output.video_src if key == "video" else output.sound_src, sample)
        return Gst.FlowReturn.OK

    def start_output(self, kind: str, choice: EncoderChoice, sink: list[Element], *, video_kbps: int,
                     audio_kbps: int = 160) -> Output:
        if self.pipeline is None:
            raise EngineError("nothing to send: the program isn't running")
        if kind in self.outputs:
            raise EngineError(f"already {kind}ing")
        if self.pipeline.get_state(0)[1] != Gst.State.PLAYING:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.pipeline.get_state(Gst.SECOND)  # the base time is the playing one
        self._ensure_sound()
        self._ensure_taps()
        output = Output(self, kind, choice, sink, video_kbps, audio_kbps, with_sound=True)
        self.outputs[kind] = output

        def finished() -> None:
            if self.outputs.get(kind) is output:
                del self.outputs[kind]
            if not self.outputs and self.pipeline is not None:
                self._drop_taps()
                self._sync_sound()
                self._idle_check()
            if kind == "stream" and self.stream_retry is not None:
                return  # reconnecting, not off
            self.emit("output-state", kind, "off")

        output.on_finished = finished
        output.on_error = lambda message: self.emit("problem", message)
        clock = self.pipeline.get_clock()
        running = clock.get_time() - self.pipeline.get_base_time()
        output.start(clock, self.pipeline.get_base_time(), running)
        self.emit("output-state", kind, "on")
        return output

    def stop_output(self, kind: str) -> None:
        output = self.outputs.get(kind)
        if output is not None:
            output.stop()

    # ── Recording and streaming ──

    def start_record(self, path: str, *, container: str = "mkv", codec: str = "h264",
                     video_kbps: int = 12000) -> Output:
        choice = choose_encoders(codec) or (choose_encoders("av1") if codec == "h264" else None)
        if choice is None:
            raise EngineError("No video encoder works on this computer")
        return self.start_output("record", choice, graph.record_sink(container, path), video_kbps=video_kbps,
                                 audio_kbps=192)

    def stop_record(self) -> None:
        self.stop_output("record")

    def start_stream(self, url: str, *, codec: str = "h264", video_kbps: int = 6000, audio_kbps: int = 160) -> Output:
        """Go live; a dropped connection reconnects with backoff (2 s, ×1.5, up to 15 min)."""
        choice = choose_encoders(codec)
        if choice is None:
            raise EngineError("No H.264 encoder works on this computer" if codec == "h264"
                              else "No AV1 encoder works on this computer")
        if choice.audio is None:
            raise EngineError("No AAC encoder is installed")
        self.stream_retry = {"url": url, "choice": choice, "video": video_kbps, "audio": audio_kbps,
                             "attempt": 0, "timer": 0, "started": time.monotonic()}
        return self._open_stream()

    def _open_stream(self) -> Output:
        retry = self.stream_retry
        enhanced = retry["choice"].codec != "h264" and Gst.ElementFactory.find("eflvmux") is not None
        output = self.start_output("stream", retry["choice"], graph.stream_sink(retry["url"], enhanced=enhanced),
                                   video_kbps=retry["video"], audio_kbps=retry["audio"])
        output.on_error = self._stream_failed
        return output

    def _stream_failed(self, message: str) -> None:
        retry = self.stream_retry
        if retry is None:
            self.emit("problem", message)
            return
        retry["attempt"] += 1
        delay = graph.backoff(retry["attempt"])
        self.emit("output-state", "stream", "reconnecting")

        def again() -> bool:
            retry["timer"] = 0
            if self.stream_retry is retry and "stream" not in self.outputs and self.pipeline is not None:
                try:
                    self._open_stream()
                except EngineError as error:
                    self._stream_failed(str(error))
            return False

        retry["timer"] = GLib.timeout_add(int(delay * 1000), again)

    def stop_stream(self) -> None:
        retry, self.stream_retry = self.stream_retry, None
        if retry is not None and retry["timer"]:
            GLib.source_remove(retry["timer"])
        self.stop_output("stream")
        if "stream" not in self.outputs:
            self.emit("output-state", "stream", "off")

    def stats(self, kind: str) -> OutputStats | None:
        output = self.outputs.get(kind)
        return output.stats if output is not None else None

    def set_stream_bitrate(self, kbps: int) -> None:
        """Change a live stream's bitrate (the VA encoders take it while playing)."""
        output = self.outputs.get("stream")
        if output is None or output.pipeline is None:
            return
        encoder = output.pipeline.get_by_name("stream-encoder")
        if encoder is not None:
            factor = 1000 if encoder.get_factory().get_name() == "openh264enc" else 1
            encoder.set_property("bitrate", kbps * factor)

    # ── Bus ──

    def _message(self, _bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure is not None and structure.get_name() == "level":
                name = message.src.get_name()
                if name.startswith("w-") and name.endswith("-level"):
                    widget_id = name[2:-6]
                    peaks = structure.get_value("peak")
                    left = peaks[0] if peaks else -60.0
                    right = peaks[1] if len(peaks) > 1 else left
                    self.emit("levels", widget_id, max(-60.0, left), max(-60.0, right))
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            source = message.src.get_name() if message.src else ""
            widget_id = source[2:].rsplit("-", 1)[0] if source.startswith("w-") else None
            if widget_id is not None and widget_id in self.branches:
                # One widget failed (a window closed, a camera unplugged or busy): take it out, keep going.
                self._fail(widget_id, source_problem(error.message, debug or ""))
            else:
                self.emit("problem", error.message)
