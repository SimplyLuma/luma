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
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstBase", "1.0")
from gi.repository import GLib, GObject, Gst, GstBase  # noqa: E402,F401 (GstBase: Aggregator.peek_next_sample)

from . import graph  # noqa: E402
from .graph import Chain, Element, EncoderChoice  # noqa: E402
from .model import FORMATS, Project, Scene, Widget  # noqa: E402

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
        if name in CONSTRUCT_ONLY:
            continue
        if element.find_property(name) is None:
            log(f"{spec.factory} has no {name}; left at its default")
            continue
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


#: Encoders that are what they say once installed: no driver behind them and no stub package
#: (openh264's noopenh264 is one) to stand in for them. Probing SVT-AV1 would also start its
#: thread pool in this process for nothing.
_SELF_CONTAINED = ("svtav1enc",)


def encoder_works(factory: str) -> bool:
    """Whether an encoder really encodes (a stub library or a missing driver fails here)."""
    if factory in _SELF_CONTAINED:
        return True
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


#: A capture that has sent no picture in this long says so ("waiting"). It is not a failure:
#: a window cast sends nothing until the window redraws, so the stream stays open.
FIRST_PICTURE_S = 8

#: Why a source failed: "busy", "missing" (the device or node is gone), "denied", "format"
#: (no picture format both ends take) or "stopped" (any other pipeline error).
REASONS = ("busy", "missing", "denied", "format", "stopped")


def source_problem(message: str, debug: str) -> str:
    """A source's GStreamer error as one of REASONS."""
    text = f"{message} {debug}".lower()
    if "busy" in text or "ebusy" in text or "in use" in text:
        return "busy"
    if "target not found" in text or "no such" in text or "not found" in text or "removed" in text:
        return "missing"
    if "permission" in text or "not allowed" in text or "denied" in text:
        return "denied"
    if "not-negotiated" in text or "negotiat" in text or "no common format" in text:
        return "format"
    return "stopped"


def crop_meta(buffer: Gst.Buffer) -> tuple[int, int, int, int] | None:
    """A buffer's GstVideoCropMeta (x, y, width, height), or None. PyGObject hands the meta out as a
    plain Gst.Meta, so its fields are read where they sit: after GstMeta (flags, info), four guints."""
    import ctypes
    import re

    meta = buffer.get_meta("GstVideoCropMetaAPI")
    if meta is None:
        return None
    found = re.search(r"at (0x[0-9a-f]+)\)>?$", repr(meta))
    if found is None:
        return None
    base = int(found.group(1), 16) + 2 * ctypes.sizeof(ctypes.c_void_p)  # flags (padded) and info
    values = [ctypes.c_uint.from_address(base + 4 * i).value for i in range(4)]
    return tuple(values) if values[2] and values[3] else None


#: Formats with alpha: SHM names and DRM fourccs (DMABuf drm-format, "AR24:<modifier>").
ALPHA_FORMATS = ("BGRA", "RGBA", "ARGB", "ABGR")
ALPHA_FOURCCS = ("AR24", "AB24", "RA24", "BA24")


def prefer_alpha(caps: str) -> str:
    """``caps`` with every format list ordered alpha first, so a window cast with transparent
    (rounded) corners is offered and taken with its alpha rather than as an opaque XR24/BGRx."""
    import re

    def order(found: re.Match) -> str:
        items = [v.strip() for v in found.group(2).split(",")]
        alpha = [v for v in items if v in ALPHA_FORMATS or v.split(":")[0] in ALPHA_FOURCCS]
        return f"{found.group(1)}{{ {', '.join(alpha + [v for v in items if v not in alpha])} }}"

    return re.sub(r"(\b(?:drm-)?format=\(string\))\{([^}]*)\}", order, caps)


def only_alpha(caps: str) -> str:
    """``caps`` cut to the formats with alpha (DMABuf AR24/AB24… with their modifiers, system-memory
    BGRA/RGBA…); "" when none is left. A PipeWire producer picks the first format of its own list that
    the consumer takes, so ordering alone never wins over a producer that lists XR24 first."""
    import re

    kept = []
    for part in caps.split(";"):
        part = part.strip()
        if not part:
            continue
        key = "drm-format" if "drm-format=" in part else "format"
        found = re.search(rf"\b{key}=\(string\)(\{{[^}}]*\}}|[^,;]+)", part)
        if found is None:
            continue
        value = found.group(1)
        items = [v.strip() for v in value.strip("{} ").split(",")] if value.startswith("{") else [value.strip()]
        alpha = [v for v in items if v in ALPHA_FORMATS or v.split(":")[0] in ALPHA_FOURCCS]
        if not alpha:
            continue
        text = alpha[0] if len(alpha) == 1 else "{ " + ", ".join(alpha) + " }"
        kept.append(part[:found.start(1)] + text + part[found.end(1):])
    return "; ".join(kept)


def log(text: str) -> None:
    """One line for the journal (stderr is the app unit's journal)."""
    import sys

    print(f"streamy: {text}", file=sys.stderr, flush=True)


@dataclass
class Branch:
    widget_id: str
    elements: list[Gst.Element]
    pad: Gst.Pad | None = None
    picture: bool = True
    fade: int = 0
    shown: bool = False
    watchdog: int = 0
    generation: int = 0
    monitor: list = field(default_factory=list)
    monitor_device: str | None = None
    #: A picture: its layer on each canvas composited (canvas -> Layer), the widget as last shown,
    #: its stacking place, and the source's last crop region.
    layers: dict = field(default_factory=dict)
    view: Widget | None = None
    zorder: int = 0
    crop: tuple | None = None

    def frame(self, fmt: str = "landscape") -> Gst.Element | None:
        layer = self.layers.get(fmt)
        return layer.frame if layer is not None else None


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
                 video_kbps: int, audio_kbps: int, with_sound: bool, fmt: str = "landscape") -> None:
        self.engine, self.kind, self.choice, self.fmt = engine, kind, choice, fmt
        self.stats = OutputStats()
        self.pipeline = Gst.Pipeline.new(f"streamy-{kind}")
        _label, width, height = FORMATS[fmt]
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
        self.started = False
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
        # Time starts at the first picture that arrives, not at the clock's "now": the program's
        # pictures reach the tap behind the clock, further behind on a busy machine, so a clock
        # start dropped every picture of a later output.
        self.t0 = None
        self.started = True
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise EngineError(f"the {self.kind} could not start")

    def push(self, src: Gst.Element, sample: Gst.Sample) -> None:
        if self.stopping or src is None or not self.started:
            return
        buffer = sample.get_buffer()
        if buffer.pts == Gst.CLOCK_TIME_NONE:
            return
        if self.t0 is None:
            if src is not self.video_src:
                return  # a recording starts on a picture; the sound before it is dropped
            self.t0 = buffer.pts
        if buffer.pts < self.t0:
            return
        if src is self.video_src:
            if src.get_property("current-level-buffers") >= src.get_property("max-buffers"):
                self.stats.dropped += 1  # the encoder or the network is far behind
                return
            self.stats.frames += 1
        # copy_region, not copy(): in Python copy() is the boxed copy, a ref, and setting its time
        # would change the buffer the other output receives too.
        out = buffer.copy_region(Gst.BufferCopyFlags.FLAGS | Gst.BufferCopyFlags.TIMESTAMPS |
                                 Gst.BufferCopyFlags.META | Gst.BufferCopyFlags.MEMORY, 0, buffer.get_size())
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
        # A stream whose connection is gone never sees its EOS: give up after 5 s. A recording
        # waits longer, so a busy machine still drains its encoder and finishes the file.
        GLib.timeout_add_seconds(5 if self.kind == "stream" else 20, self._finish)

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
        sink = self.pipeline.get_by_name("record-sink")
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        if self.kind == "record" and self.stats.frames == 0 and sink is not None:
            # No picture ever reached it: a header alone is not a recording; say so, leave no file.
            path = sink.get_property("location")
            try:
                os.unlink(path)
            except OSError:
                pass
            log(f"recording {path}: no picture reached it; removed")
            self.engine.emit("problem", "Nothing was recorded: no picture reached the recording.")
        if self.on_finished is not None:
            self.on_finished()
        return False


@dataclass
class Layer:
    """One picture widget drawn on one canvas: queue ! lumaframe ! mixer-<canvas> pad."""

    elements: list[Gst.Element]
    pad: Gst.Pad | None = None
    tee_pad: Gst.Pad | None = None

    @property
    def frame(self) -> Gst.Element:
        return self.elements[-1]


@dataclass
class Program:
    """One canvas's compositor and what hangs off it (its preview, its encoder tap)."""

    fmt: str
    size: tuple[int, int]
    elements: list[Gst.Element]
    preview: list[Gst.Element] = field(default_factory=list)
    paintable: object = None
    preview_size: tuple[int, int] | None = None
    handler: int = 0
    composited: list = field(default_factory=list)  # (program pts, {widget id: box}), newest last
    on_screen: dict = field(default_factory=dict)
    tap: list[Gst.Element] = field(default_factory=list)
    #: While something changes (a drag, a Look edit) the mixer reports each frame's layers and the preview
    #: sink each frame shown; idle, neither runs (no Python per frame).
    watching: bool = False
    sink_pad: Gst.Pad | None = None
    probe: int = 0

    @property
    def mixer(self) -> Gst.Element:
        return self.elements[0]

    @property
    def tee(self) -> Gst.Element:
        return self.elements[-1]


class Engine(GObject.Object):
    """The program: the active scene composited once per canvas that is on, its sound mixed, and the
    outputs. A picture is captured once and teed to a layer per canvas; a canvas's compositor exists
    only while it is shown or an output takes it (``set_canvases``)."""

    __gsignals__ = {
        # widget id, left dB, right dB
        "levels": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float)),
        # a plain sentence for a toast
        "problem": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # "record" | "stream", "starting" | "on" | "reconnecting" | "off"
        "output-state": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # canvas: its preview's paintable came or went (a compositor exists only while needed)
        "preview-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # widget id: its first picture arrived
        "widget-ready": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # widget id, a plain reason: its source failed; it is out of the program
        "widget-failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # widget id: its stream is open but no picture has come yet (it stays open and may start)
        "widget-waiting": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # widget id, width, height: the picture's own size changed (a window cast's crop region)
        "source-size": (GObject.SignalFlags.RUN_FIRST, None, (str, int, int)),
        # canvas, widget id, x, y, width, height: where that canvas's preview frame on screen now draws
        # the widget's box (canvas pixels), so an outline drawn over it never parts from the picture
        "frame-shown": (GObject.SignalFlags.RUN_FIRST, None, (str, str, float, float, float, float)),
    }

    def __init__(self, *, fake: bool | None = None, preview: bool = True,
                 sources: Callable[[Widget], tuple[int | None, str | None]] | None = None) -> None:
        super().__init__()
        Gst.init(None)
        self.fake = bool(int(os.environ.get("STREAMY_FAKE_SOURCES", "0"))) if fake is None else fake
        self.want_preview = preview
        self.sources = sources  # widget -> (pipewire fd, node) from the portals
        self.pipeline: Gst.Pipeline | None = None
        #: The canvases shown (the window's panels); outputs keep theirs running too.
        self.shown_canvases: tuple[str, ...] = ("landscape",)
        self.programs: dict[str, Program] = {}
        self.branches: dict[str, Branch] = {}
        #: Picture widgets without a live picture show a card (graph.placeholder_chain) in their place.
        self.placeholders: dict[str, Branch] = {}
        #: ``widget`` as canvas ``fmt`` has it (its placement and Look there); the window's project knows.
        self.view_of: Callable[[Widget, str], Widget] = lambda widget, _fmt: widget
        self._placeholder_words: dict[str, str] = {}
        self.outputs: dict[str, Output] = {}
        #: An outputs fan-out (outputs.Outputs) fed from the taps: push_video(fmt, sample), push_sound(sample),
        #: wants() -> the canvases it encodes.
        self.fanout = None
        self.stream_retry: dict | None = None
        self._sound = False
        self._sound_tap: list[Gst.Element] = []
        self._zorder = 0
        self._preview_sizes: dict[str, tuple[int, int]] = {}
        self.enabled = False
        self._generation = 0
        self._scene: Scene | None = None
        #: Atomic layer geometry: each mixer pad's geometry per lumaframe revision, applied by the
        #: mixer's samples-selected for the buffer it actually composites (see _samples_selected).
        self._geom_lock = threading.Lock()
        self._revision = 0
        self._revs: dict[Gst.Pad, dict[int, tuple]] = {}
        self._applied: dict[Gst.Pad, tuple] = {}
        self._applied_rev: dict[Gst.Pad, int] = {}
        self._pad_widget: dict[Gst.Pad, str] = {}
        #: Window casts that offered no alpha format: taken opaque (alpha first, not alpha only).
        self._no_alpha: set[str] = set()
        #: Enum properties last set by nick, per (element name, property): a move never re-sets them.
        self._strings: dict[tuple[str, str], str] = {}

    # ── Canvases ──

    @property
    def canvas(self) -> tuple[int, int]:
        """The first canvas shown (the size older callers and checks know)."""
        fmt = self.shown_canvases[0] if self.shown_canvases else "landscape"
        return FORMATS[fmt][1], FORMATS[fmt][2]

    @property
    def paintable(self):
        """The first shown canvas's preview (older callers and checks)."""
        return self.paintable_of(self.shown_canvases[0]) if self.shown_canvases else None

    def paintable_of(self, fmt: str):
        program = self.programs.get(fmt)
        return program.paintable if program is not None else None

    def wanted_canvases(self) -> list[str]:
        """The canvases composited: those shown, and those an output or the fan-out takes."""
        wanted = list(self.shown_canvases)
        sent = [o.fmt for o in self.outputs.values()]
        if self.fanout is not None:
            sent += list(self.fanout.wants())
        for fmt in sent:
            if fmt in FORMATS and fmt not in wanted:
                wanted.append(fmt)
        return wanted

    def set_canvases(self, shown) -> None:
        """Show these canvases (in order); a canvas neither shown nor sent stops compositing."""
        shown = tuple(f for f in shown if f in FORMATS)
        if shown == self.shown_canvases:
            return
        self.shown_canvases = shown
        if self.enabled:
            self.show_scene(self._scene, skip=self._skip, states=self._states)

    def _sync_programs(self) -> None:
        if self.pipeline is None:
            return
        wanted = self.wanted_canvases()
        for fmt in [f for f in self.programs if f not in wanted]:
            self._drop_program(fmt)
        for fmt in wanted:
            if fmt not in self.programs:
                self._add_program(fmt)

    def _add_program(self, fmt: str) -> None:
        size = (FORMATS[fmt][1], FORMATS[fmt][2])
        elements = [make(spec) for spec in graph.program(size, fmt)]
        for element in elements:
            self.pipeline.add(element)
        link_all(elements)
        program = Program(fmt, size, elements, preview_size=self._preview_sizes.get(fmt))
        mixer = program.mixer
        if mixer.find_property("emit-signals") is not None:
            program.handler = mixer.connect("samples-selected", self._samples_selected, program)
        self.programs[fmt] = program
        if self.want_preview:
            self._add_preview(program)
        for element in reversed(elements + program.preview):
            element.sync_state_with_parent()
        # Every widget already captured gets its layer here.
        for branch in list(self.branches.values()) + list(self.placeholders.values()):
            if branch.picture and branch.view is not None:
                self._add_layer(branch, fmt, branch.view, branch.zorder)
        self.emit("preview-changed", fmt)

    def _drop_program(self, fmt: str) -> None:
        program = self.programs.pop(fmt, None)
        if program is None:
            return
        for branch in list(self.branches.values()) + list(self.placeholders.values()):
            self._remove_layer(branch, fmt)
        if program.handler:
            program.mixer.set_property("emit-signals", False)
            program.mixer.disconnect(program.handler)
            program.handler = 0
        pipeline = self.pipeline
        items = program.elements + program.preview + program.tap
        sink = next((e for e in program.preview if e.get_name().startswith("preview-sink")), None)
        if sink is not None:
            # The preview's GTK sink stops here, on the thread its paintable belongs to.
            for element in program.preview:
                element.set_locked_state(True)
            for element in reversed(program.preview):
                element.set_state(Gst.State.NULL)
        for element in items:
            element.set_locked_state(True)

        def stop(_pipeline, elements) -> None:
            for element in elements:
                element.set_state(Gst.State.NULL)
            GLib.idle_add(lambda: [pipeline.remove(e) for e in elements if e.get_parent() is pipeline] and False)

        pipeline.call_async(stop, items)
        program.paintable = None
        self.emit("preview-changed", fmt)

    # ── The program pipeline ──

    def start(self, project: Project) -> None:
        """Show ``project``'s active scene. The pipeline itself exists only while something
        captures or is sent; an empty scene costs nothing."""
        if self.enabled:
            return
        self.enabled = True
        self.show_scene(project.active_scene)

    def _open(self) -> None:
        if self.pipeline is not None:
            return
        self.pipeline = Gst.Pipeline.new("streamy-program")
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._message)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise EngineError("the preview could not start")

    def _close(self) -> None:
        if self.pipeline is None:
            return
        for widget_id in list(self.placeholders):
            self._remove_placeholder(widget_id)
        for widget_id in list(self.branches):
            self._remove_branch(widget_id)
        pipeline = self.pipeline
        pipeline.get_bus().remove_signal_watch()
        programs, self.programs = self.programs, {}
        for program in programs.values():
            if program.handler:
                program.mixer.set_property("emit-signals", False)
                program.mixer.disconnect(program.handler)
            for element in program.preview:
                element.set_locked_state(True)
                element.set_state(Gst.State.NULL)
        self.pipeline = None
        # Off the window's thread: a source still stuck opening holds its state lock, and the
        # pipeline's own NULL waits on it. Nothing here needs the old pipeline any more.
        threading.Thread(target=pipeline.set_state, args=(Gst.State.NULL,), name="streamy-close",
                         daemon=True).start()
        self._sound = False
        self._sound_tap = []
        for fmt in programs:
            self.emit("preview-changed", fmt)

    def stop(self) -> None:
        """Everything off: outputs finish, captures stop, the pipeline goes."""
        for output in list(self.outputs.values()):
            output.stop()
        self.enabled = False
        self._close()

    @property
    def running(self) -> bool:
        return self.enabled

    def set_canvas(self, project: Project) -> None:
        """The canvas edited changed: every canvas keeps running; nothing to rebuild."""

    def _add_preview(self, program: Program) -> None:
        fmt = program.fmt
        sink = Gst.ElementFactory.make("gtk4paintablesink", f"preview-sink-{fmt}")
        if sink is None:
            raise EngineError("GStreamer has no gtk4paintablesink (gstreamer1-plugin-gtk4)")
        program.paintable = sink.get_property("paintable")
        sink.set_property("sync", False)
        bin_ = Gst.ElementFactory.make("glsinkbin", f"preview-{fmt}")
        bin_.set_property("sink", sink)
        # A live program never waits for the preview to preroll: with an async sink, a branch
        # added later (a microphone, a camera that never sends) can leave the pipeline PAUSED
        # waiting for it, and every live source in it paused with it (meters still, PipeWire
        # nodes suspended).
        sink.set_property("async", False)
        if bin_.find_property("async") is not None:
            bin_.set_property("async", False)
        # The preview is scaled on the GPU to the size it is shown at: no full-size copy for the screen.
        # Bilinear scaling aliases past 2:1, so a large reduction goes in two steps (at most 2:1 each,
        # a box filter in effect); a small one passes the first step through.
        queue = make(Element("queue", f"preview-queue-{fmt}", {"max-size-buffers": 1, "leaky": 2}))
        half = make(Element("glcolorscale", f"preview-scale-half-{fmt}"))
        half_size = make(Element("capsfilter", f"preview-size-half-{fmt}", {"caps": self._preview_caps(program, 2)}))
        scale = make(Element("glcolorscale", f"preview-scale-{fmt}"))
        size = make(Element("capsfilter", f"preview-size-{fmt}", {"caps": self._preview_caps(program)}))
        program.preview = [queue, half, half_size, scale, size, bin_]
        for element in program.preview:
            self.pipeline.add(element)
        link_all(program.preview)
        program.tee.link(queue)
        program.sink_pad = sink.get_static_pad("sink")

    @staticmethod
    def _preview_caps(program: Program, factor: int = 1) -> str:
        cw, ch = program.size
        width, height = program.preview_size or (cw // 2, ch // 2)
        width, height = min(cw, width * factor), min(ch, height * factor)
        return f"video/x-raw(memory:GLMemory),width={width},height={height}"

    def set_preview_size(self, width: int, height: int = 0, fmt: str | None = None) -> None:
        """Show canvas ``fmt``'s program at this many pixels wide (its size on screen, in device pixels)."""
        fmt = fmt or (self.shown_canvases[0] if self.shown_canvases else "landscape")
        _label, cw, ch = FORMATS[fmt]
        width = max(2, min(cw, int(width) // 2 * 2))
        height = max(2, min(ch, int(round(width * ch / cw)) // 2 * 2))
        if self._preview_sizes.get(fmt) == (width, height):
            return
        self._preview_sizes[fmt] = (width, height)
        program = self.programs.get(fmt)
        if program is None:
            return
        program.preview_size = (width, height)
        for element, factor in ((program.preview[2] if program.preview else None, 2),
                                (program.preview[4] if program.preview else None, 1)):
            if element is not None:
                element.set_property("caps", Gst.Caps.from_string(self._preview_caps(program, factor)))

    # ── Scenes and widgets ──

    _skip: set[str] | frozenset = frozenset()
    _states: dict[str, str] | None = None

    def show_scene(self, scene: Scene | None, *, skip: set[str] | frozenset = frozenset(),
                   states: dict[str, str] | None = None) -> None:
        """Capture exactly the visible widgets of ``scene`` (less ``skip``: ones that failed); stop the rest.

        A visible picture widget with no live picture (connecting, failed, nothing chosen: ``states``
        says which, "connecting" by default) shows its placeholder card through its own lumaframe, so its
        Look shows. The card is the preview's only: while recording or live it is transparent (its pad's
        alpha 0), so viewers never see it, and nothing moves (widgets are placed, never laid out)."""
        if not self.enabled:
            return
        self._scene, self._skip, self._states = scene, set(skip), dict(states or {})
        states = states or {}
        shown = [w for w in (scene.widgets if scene else []) if w.visible]
        composited = bool(self.wanted_canvases())
        # No canvas composited: pictures capture nothing (the sound still runs, for its meters).
        wanted = {w.id: w for w in shown if w.id not in skip and (composited or not w.has_picture)}
        if not wanted and not self.outputs and not (composited and shown):
            self._close()  # nothing to show and nothing sent: no pipeline at all
            return
        self._open()
        self._sync_programs()
        for widget_id in [i for i in self.branches if i not in wanted]:
            self._remove_branch(widget_id)
        for zorder, widget in enumerate(scene.widgets if scene else []):
            if widget.id not in wanted:
                continue
            if widget.id in self.branches:
                self.update_widget(widget, zorder)
            else:
                self._add_branch(widget, zorder)
        # Placeholders: every shown picture widget whose capture has no picture yet.
        cards = {}
        for zorder, widget in enumerate(scene.widgets if scene else []):
            if not composited or not widget.visible or not widget.has_picture:
                continue
            branch = self.branches.get(widget.id)
            if branch is not None and branch.shown:
                continue
            state = states.get(widget.id) or ("failed" if widget.id in skip else "connecting" if branch else "choose")
            cards[widget.id] = (widget, zorder, graph.placeholder_words(widget, state))
        for widget_id in [i for i in self.placeholders if i not in cards or
                          self._placeholder_words.get(i) != cards[i][2]]:
            self._remove_placeholder(widget_id)
        for widget_id, (widget, zorder, words) in cards.items():
            if widget_id in self.placeholders:
                self._update_placeholder(widget, zorder)
            else:
                self._add_placeholder(widget, zorder, words)
        self._sync_sound()
        self._keep_playing()
        self._idle_check()

    def _keep_playing(self) -> None:
        """The program runs whenever it exists: anything that left it short of PLAYING is undone.

        A source that fails while it starts (a camera that is gone) fails the pipeline's own state
        change, which then stays PAUSED with PLAYING still pending, and every live source in it
        pauses with it: a microphone's meter stops and its PipeWire node suspends. Asking again,
        once the failed branch is out, completes the change."""
        if self.pipeline is None:
            return
        _ok, state, _pending = self.pipeline.get_state(0)
        if state != Gst.State.PLAYING:
            # On GStreamer's pool: a source still opening (a stuck device) never holds the window.
            self.pipeline.call_async(lambda pipeline, _data: pipeline.set_state(Gst.State.PLAYING), None)

    def _idle_check(self) -> None:
        """Nothing captures, shows or is sent: the program goes, so it costs nothing."""
        def idle() -> bool:
            return not self.branches and not self.outputs and not self.placeholders and not self.programs

        if self.pipeline is not None and idle():
            GLib.idle_add(lambda: (self._close() if idle() else None) and False)

    # ── Layers: a picture on each canvas ──

    def _add_layer(self, branch: Branch, fmt: str, widget: Widget, zorder: int, *, card: bool = False) -> None:
        """Draw ``branch``'s picture on canvas ``fmt`` (placed and styled as that canvas has it)."""
        program = self.programs.get(fmt)
        if program is None or fmt in branch.layers or self.pipeline is None:
            return
        view = self.view_of(widget, fmt)
        prefix = branch.elements[0].get_name().rsplit("-", 1)[0]
        chain = graph.layer_chain(view, zorder, prefix, fmt)
        elements = [make(e) for e in chain.elements]
        if branch.fade < FADE_MS and not card:
            elements[-1].set_property("opacity", view.effects.clamped().opacity * branch.fade / FADE_MS)
        for element in elements:
            self.pipeline.add(element)
        link_all(elements)
        tee = branch.elements[-1]
        tee_pad = tee.request_pad_simple("src_%u")
        pad = program.mixer.request_pad_simple("sink_%u")
        for name, value in chain.pad.items():
            pad.set_property(name, value)
        if card:
            pad.set_property("alpha", 0.0 if self.outputs else 1.0)
        layer = Layer(elements, pad, tee_pad)
        if elements[-1].get_static_pad("src").link(pad) != Gst.PadLinkReturn.OK:
            log(f"widget {branch.widget_id}: its layer does not link to {fmt}'s mixer")
        self._place(elements[-1], pad, view, chain.pad, True)
        tee_pad.link(elements[0].get_static_pad("sink"))
        branch.layers[fmt] = layer
        if branch.crop is not None and branch.crop[2] is not None:
            width, height, (x, y, w, h) = branch.crop
            x, y = min(x, width - 1), min(y, height - 1)
            self._crop_layer(branch, layer, (x, y, min(w, width - x), min(h, height - y)))
        self.pipeline.call_async(lambda _p, items: [e.sync_state_with_parent() for e in reversed(items)], elements)

    def _remove_layer(self, branch: Branch, fmt: str) -> None:
        layer = branch.layers.pop(fmt, None)
        if layer is None or self.pipeline is None:
            return
        pipeline = self.pipeline
        self._forget_pad(layer.pad)
        tee = branch.elements[-1]
        if layer.tee_pad is not None:
            layer.tee_pad.unlink(layer.elements[0].get_static_pad("sink"))
            tee.release_request_pad(layer.tee_pad)
        src = layer.frame.get_static_pad("src")
        if layer.pad is not None:
            src.add_probe(Gst.PadProbeType.BLOCK_DOWNSTREAM, lambda *_a: Gst.PadProbeReturn.DROP)
            src.unlink(layer.pad)
            layer.pad.get_parent_element().release_request_pad(layer.pad)
        for element in layer.elements:
            element.set_locked_state(True)

        def stop(_pipeline, elements) -> None:
            for element in elements:
                element.set_state(Gst.State.NULL)
            GLib.idle_add(lambda: [pipeline.remove(e) for e in elements if e.get_parent() is pipeline] and False)

        pipeline.call_async(stop, layer.elements)

    def _style_layer(self, layer: Layer, view: Widget, zorder: int, *, fade: int = FADE_MS,
                     alpha: float | None = None) -> None:
        """Move, resize or restyle one layer live (no renegotiation unless its size changed)."""
        frame = layer.frame
        props = graph.frame_props(view)
        if fade < FADE_MS:
            props["opacity"] = props["opacity"] * fade / FADE_MS
        changed = False
        for name, value in props.items():
            if frame.find_property(name) is None:
                continue  # an older lumaframe (no stroke-position): its default
            if isinstance(value, str):
                if self._strings.get((frame.get_name(), name)) != value:
                    self._strings[(frame.get_name(), name)] = value
                    _set_prop(frame, name, value)
                    changed = True
            elif frame.get_property(name) != value:
                frame.set_property(name, value)
                changed = True
        pad_props = graph.mixer_pad_props(view, zorder)
        if alpha is not None:
            pad_props["alpha"] = alpha
        self._place(frame, layer.pad, view, pad_props, changed)

    # ── Placeholders ──

    def _add_placeholder(self, widget: Widget, zorder: int, words: str) -> None:
        self._generation += 1
        chain = graph.placeholder_chain(widget, zorder, words, self._generation)
        try:
            elements = [make(e) for e in chain.elements]
        except EngineError as error:
            log(f"no placeholder for {widget.id}: {error}")
            return
        for element in elements:
            self.pipeline.add(element)
        link_all(elements)
        card = Branch(widget.id, elements, None, True, shown=True, generation=self._generation, view=widget,
                      zorder=zorder, fade=FADE_MS)
        self.placeholders[widget.id] = card
        self._placeholder_words[widget.id] = words
        for fmt in self.programs:
            self._add_layer(card, fmt, widget, zorder, card=True)
        self.pipeline.call_async(lambda _p, items: [e.sync_state_with_parent() for e in reversed(items)], elements)

    def _update_placeholder(self, widget: Widget, zorder: int | None = None) -> None:
        card = self.placeholders.get(widget.id)
        if card is None:
            return
        card.view = widget
        if zorder is not None:
            card.zorder = zorder
        for fmt, layer in card.layers.items():
            self._style_layer(layer, self.view_of(widget, fmt), card.zorder, alpha=0.0 if self.outputs else 1.0)

    def _remove_placeholder(self, widget_id: str) -> None:
        card = self.placeholders.pop(widget_id, None)
        self._placeholder_words.pop(widget_id, None)
        if card is None:
            return
        self.branches[f"placeholder:{widget_id}"] = card
        self._remove_branch(f"placeholder:{widget_id}")

    def _sync_placeholder_alpha(self) -> None:
        """Sending or not: the cards are the preview's only."""
        for card in self.placeholders.values():
            for layer in card.layers.values():
                if layer.pad is not None:
                    layer.pad.set_property("alpha", 0.0 if self.outputs else 1.0)

    def update_widget(self, widget: Widget, zorder: int | None = None) -> None:
        """Move, resize or restyle a widget live on every canvas it is drawn on."""
        self._update_placeholder(widget, zorder)
        branch = self.branches.get(widget.id)
        if branch is None:
            return
        branch.view = widget
        if zorder is not None:
            branch.zorder = zorder
        if branch.picture:
            for fmt, layer in branch.layers.items():
                self._style_layer(layer, self.view_of(widget, fmt), branch.zorder, fade=branch.fade)
            flip = next((e for e in branch.elements if e.get_factory().get_name() == "glvideoflip"), None)
            if flip is not None:
                Gst.util_set_object_arg(flip, "method", graph.flip_method(widget))
        else:
            volume = next(e for e in branch.elements if e.get_factory().get_name() == "volume")
            for name, value in graph.volume_props(widget).items():
                volume.set_property(name, value)
            if branch.pad is not None:
                branch.pad.set_property("mute", widget.monitor == "only")
            self._sync_monitor(branch, widget)

    def restack(self, scene: Scene) -> None:
        for zorder, widget in enumerate(scene.widgets):
            for branch in (self.placeholders.get(widget.id), self.branches.get(widget.id)):
                if branch is None or not branch.picture:
                    continue
                branch.zorder = zorder
                for layer in branch.layers.values():
                    if layer.pad is not None:
                        layer.pad.set_property("zorder", zorder)

    def _add_branch(self, widget: Widget, zorder: int) -> None:
        fd = node = None
        if not self.fake and self.sources is not None and widget.kind in ("display", "window", "camera", "app-audio"):
            fd, node = self.sources(widget)
            if fd is None:
                return  # not chosen yet: the window asks the portal first
        self._generation += 1
        if widget.has_picture:
            chain = graph.picture_chain(widget, zorder, fake=self.fake, fd=fd, node=node, generation=self._generation)
        else:
            chain = graph.sound_chain(widget, fake=self.fake, fd=fd, node=node, generation=self._generation)
            self._ensure_sound()
        elements = [make(e) for e in chain.elements]
        for element in elements:
            self.pipeline.add(element)
        self._link_chain(elements)
        branch = Branch(widget.id, elements, None, widget.has_picture, generation=self._generation, view=widget,
                        zorder=zorder)
        if not widget.has_picture:
            target = self.pipeline.get_by_name(chain.target)
            pad = target.request_pad_simple("sink_%u")
            for name, value in chain.pad.items():
                pad.set_property(name, value)
            if elements[-1].get_static_pad("src").link(pad) != Gst.PadLinkReturn.OK:
                target.release_request_pad(pad)
                for element in elements:
                    self.pipeline.remove(element)
                log(f"widget {widget.id} failed: its branch does not link to the mixer")
                self.emit("widget-failed", widget.id, "stopped")
                return
            branch.pad = pad
        self.branches[widget.id] = branch
        if widget.has_picture:
            for fmt in self.programs:
                self._add_layer(branch, fmt, widget, zorder)
            elements[-1].get_static_pad("sink").add_probe(
                Gst.PadProbeType.BUFFER, self._first_picture, (branch, widget))
        else:
            self._sync_monitor(branch, widget)
        # State changes run on GStreamer's thread pool: a source that takes its time
        # (a camera opening, a portal stream negotiating) never holds up the window.
        self.pipeline.call_async(lambda _pipeline, items: [e.sync_state_with_parent() for e in reversed(items)],
                                 elements)
        if widget.has_picture:
            branch.watchdog = GLib.timeout_add_seconds(FIRST_PICTURE_S, self._no_picture, branch)
        if not self.fake and widget.kind in ("display", "window", "camera"):
            elements[0].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._source_buffer,
                                                        {"branch": branch, "seen": None})
        alpha = next((e for e in elements if e.get_name().endswith("-alpha")), None)
        if alpha is not None:
            strict = widget.id not in self._no_alpha
            elements[0].get_static_pad("src").add_probe(Gst.PadProbeType.QUERY_DOWNSTREAM, self._alpha_first,
                                                        (alpha, strict, widget.id))
        if os.environ.get("STREAMY_TRACE"):
            self._trace(branch, elements)

    def _source_buffer(self, pad: Gst.Pad, info: Gst.PadProbeInfo, state: dict) -> Gst.PadProbeReturn:
        """What a capture actually sends: its caps and, for a window cast, the crop region the window
        is in. The first is logged; a crop that changes (the window resized) is applied and reported."""
        buffer = info.get_buffer()
        caps = pad.get_current_caps()
        if buffer is None or caps is None:
            return Gst.PadProbeReturn.OK
        st = caps.get_structure(0)
        width, height = st.get_value("width"), st.get_value("height")
        crop = crop_meta(buffer)
        seen = (width, height, crop)
        if seen != state["seen"]:
            state["seen"] = seen
            branch = state["branch"]
            if state.get("logged") is None:
                state["logged"] = True
                log(f"widget {branch.widget_id}: {caps.to_string()[:160]}; crop meta {crop or 'none'}")
                if branch.widget_id.startswith("window"):
                    offered = pad.peer_query_caps(None)
                    log(f"widget {branch.widget_id}: downstream took {offered.to_string()[:600] if offered else '?'}")
            GLib.idle_add(self._apply_crop, branch, width, height, crop)
        return Gst.PadProbeReturn.OK

    @staticmethod
    def _alpha_first(_pad: Gst.Pad, info: Gst.PadProbeInfo, data) -> Gst.PadProbeReturn:
        """A window cast asks what it may send: before the query passes, the capsfilter after the
        source takes what downstream accepts cut to the alpha formats (``strict``; a window's rounded
        corners and shadow room stay transparent), or ordered alpha first once a cast without any
        alpha format failed that way."""
        capsfilter, strict, widget_id = data
        query = info.get_query()
        if query is None or query.type != Gst.QueryType.CAPS or not capsfilter.get_property("caps").is_any():
            return Gst.PadProbeReturn.OK
        ask = Gst.Query.new_caps(query.parse_caps())
        if not capsfilter.get_static_pad("src").peer_query(ask):
            return Gst.PadProbeReturn.OK
        caps = ask.parse_caps_result()
        if caps is None or caps.is_any() or caps.is_empty():
            return Gst.PadProbeReturn.OK
        text = caps.to_string()
        chosen = only_alpha(text) if strict else ""
        if strict and not chosen:
            log(f"widget {widget_id}: downstream takes no alpha format; the window is opaque")
        chosen = chosen or prefer_alpha(text)
        if chosen != text:
            capsfilter.set_property("caps", Gst.Caps.from_string(chosen))
        return Gst.PadProbeReturn.OK

    def _apply_crop(self, branch: Branch, width: int, height: int, crop) -> bool:
        """A window cast's buffers are the size of the cast and the window a crop region inside them
        (GstVideoCropMeta, often on DMABuf the GL upload takes as it is). The region is the picture:
        lumaframe draws only it (content-x/y/width/height), on the GPU, and it is the widget's shape."""
        if self.branches.get(branch.widget_id) is not branch:
            return False
        branch.crop = (width, height, crop)
        if crop is not None:
            x, y, w, h = crop
            x, y = min(x, width - 1), min(y, height - 1)
            w, h = min(w, width - x), min(h, height - y)
            for layer in branch.layers.values():
                self._crop_layer(branch, layer, (x, y, w, h))
            self.emit("source-size", branch.widget_id, w, h)
        else:
            self.emit("source-size", branch.widget_id, width, height)
        return False

    def _crop_layer(self, branch: Branch, layer: Layer, rect) -> None:
        frame = layer.frame
        if frame.find_property("content-width") is None:
            log(f"widget {branch.widget_id}: this lumaframe has no content rect; the crop region is not applied")
            return
        changed = False
        for name, value in zip(("content-x", "content-y", "content-width", "content-height"), rect):
            if frame.get_property(name) != value:
                frame.set_property(name, type(frame.get_property(name))(value))
                changed = True
        if changed:
            self._commit(frame, layer.pad)

    # ── Atomic layer geometry ──
    #
    # A lumaframe layer's size includes its shadow margin, so a Look change resizes the buffer and moves
    # its mixer pad (xpos = x - margin). Set from here, the two would land on different frames and the
    # picture would jump. Instead every lumaframe change is committed as one revision, stamped on the
    # buffers drawn with it, and the mixer pad takes the geometry of the revision of the buffer the
    # mixer is about to composite. A plain move (same revision) applies on the next program frame.

    _GEOMETRY = ("xpos", "ypos", "width", "height")

    def _place(self, frame: Gst.Element, pad: Gst.Pad | None, widget: Widget, pad_props: dict,
               changed: bool) -> None:
        """Commit ``frame``'s pending changes (``changed``) and where its pad draws it."""
        if pad is None:
            return
        self._watch()
        for name, value in pad_props.items():
            if name not in self._GEOMETRY and pad.get_property(name) != value:
                pad.set_property(name, value)
        geometry = tuple(pad_props[k] for k in self._GEOMETRY)
        box = (float(widget.x), float(widget.y), float(widget.width), float(widget.height))
        if frame.find_property("revision") is None:  # an older lumaframe: as it was, not atomic
            for name, value in zip(self._GEOMETRY, geometry):
                if pad.get_property(name) != value:
                    pad.set_property(name, value)
            return
        with self._geom_lock:
            self._pad_widget[pad] = widget.id
            revs = self._revs.setdefault(pad, {})
            if not changed and revs:
                latest = max(revs)
                revs[latest] = (geometry, box)  # same layer, moved: the next frame shows it
                if self._applied_rev.get(pad) != latest:
                    return  # a change is on its way: the mixer applies the move with it
                self._applied[pad] = geometry
            else:
                geometry = None
        if geometry is not None:
            # The mixer already composites this revision: move the pad now, as a plain move.
            for name, value in zip(self._GEOMETRY, revs[latest][0]):
                pad.set_property(name, value)
            return
        with self._geom_lock:
            self._revision += 1
            revision = self._revision
            revs[revision] = (tuple(pad_props[k] for k in self._GEOMETRY), box)
        frame.set_property("revision", revision)

    _last_change = 0.0
    _watch_timer = 0

    def _watch(self) -> None:
        """Something changes: every canvas reports which revision each frame shows, until it settles."""
        self._last_change = time.monotonic()
        for program in self.programs.values():
            if program.watching:
                continue
            program.watching = True
            if program.handler:
                program.mixer.set_property("emit-signals", True)
            if program.sink_pad is not None and not program.probe:
                program.probe = program.sink_pad.add_probe(Gst.PadProbeType.BUFFER, self._preview_frame, program)
        if not self._watch_timer:
            self._watch_timer = GLib.timeout_add(500, self._settle)

    def _settle(self) -> bool:
        """Nothing changed for a second and every pad shows its latest revision: stop watching."""
        with self._geom_lock:
            pending = any(revs and self._applied_rev.get(pad) != max(revs) for pad, revs in self._revs.items())
        if pending or time.monotonic() - self._last_change < 1.0:
            return True
        self._watch_timer = 0
        for program in self.programs.values():
            program.watching = False
            if program.handler:
                program.mixer.set_property("emit-signals", False)
            if program.probe and program.sink_pad is not None:
                program.sink_pad.remove_probe(program.probe)
            program.probe = 0
        return False

    def _commit(self, frame: Gst.Element, pad: Gst.Pad | None) -> None:
        """A change to ``frame`` that keeps its geometry (opacity, content rect)."""
        if pad is None or frame.find_property("revision") is None:
            return
        self._watch()
        with self._geom_lock:
            revs = self._revs.get(pad)
            if not revs:
                return
            self._revision += 1
            revision = self._revision
            revs[revision] = revs[max(revs)]
        frame.set_property("revision", revision)

    def _forget_pad(self, pad: Gst.Pad | None) -> None:
        with self._geom_lock:
            self._revs.pop(pad, None)
            self._applied.pop(pad, None)
            self._applied_rev.pop(pad, None)
            self._pad_widget.pop(pad, None)

    def _samples_selected(self, mixer, _segment, pts, _dts, _duration, _info, program: Program) -> None:
        """The mixer's thread, before it composites a frame: each layer's pad takes the geometry of
        the revision its selected buffer was drawn with."""
        boxes = {}
        # The mixer's own pads, each held while it is looked at (one being released stays valid).
        pads = []
        iterator = mixer.iterate_sink_pads()
        while True:
            result, pad = iterator.next()
            if result == Gst.IteratorResult.RESYNC:
                pads, _ = [], iterator.resync()
                continue
            if result != Gst.IteratorResult.OK:
                break
            pads.append(pad)
        for pad in pads:
            with self._geom_lock:
                revs = self._revs.get(pad)
            if revs is None:
                continue
            sample = mixer.peek_next_sample(pad)
            buffer = sample.get_buffer() if sample is not None else None
            if buffer is None:
                continue
            with self._geom_lock:
                entry = revs.get(buffer.offset)
                if entry is None:
                    continue
                for old in [r for r in revs if r < buffer.offset]:
                    del revs[old]
                applied = self._applied.get(pad)
                self._applied[pad] = entry[0]
                self._applied_rev[pad] = buffer.offset
                widget_id = self._pad_widget.get(pad)
            if applied != entry[0]:
                for name, value in zip(self._GEOMETRY, entry[0]):
                    pad.set_property(name, value)
            if widget_id is not None:
                boxes[widget_id] = entry[1]
        with self._geom_lock:
            program.composited.append((pts, boxes))
            del program.composited[:-16]

    def _preview_frame(self, _pad, info, program: Program) -> Gst.PadProbeReturn:
        """A program frame reaches the preview: what it shows, for the outline (frame-shown)."""
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        with self._geom_lock:
            boxes = next((b for t, b in reversed(program.composited) if t <= buffer.pts), None)
        if boxes is not None:
            changed = {k: v for k, v in boxes.items() if program.on_screen.get(k) != v}
            if changed:
                program.on_screen.update(changed)
                GLib.idle_add(self._emit_shown, program.fmt, changed, priority=GLib.PRIORITY_DEFAULT)
        return Gst.PadProbeReturn.OK

    def _emit_shown(self, fmt: str, boxes: dict) -> bool:
        for widget_id, box in boxes.items():
            self.emit("frame-shown", fmt, widget_id, *box)
        return False

    def _trace(self, branch: Branch, elements: list) -> None:
        """STREAMY_TRACE=1: count pictures at the source and at the tee, every 2 s, on stderr."""
        import sys

        counts = {}
        for element in (elements[0], elements[-2] if len(elements) > 1 else elements[0]):
            pad = element.get_static_pad("src")
            if pad is None:
                continue
            counts[element.get_name()] = [0, None]

            def seen(p, info, name=element.get_name()):
                counts[name][0] += 1
                buffer = info.get_buffer()
                counts[name][1] = (buffer.pts, p.get_current_caps().to_string()[:120] if p.get_current_caps() else None)
                return Gst.PadProbeReturn.OK
            pad.add_probe(Gst.PadProbeType.BUFFER, seen)

        def report() -> bool:
            print("streamy-trace", branch.widget_id, counts, file=sys.stderr, flush=True)
            return self.branches.get(branch.widget_id) is branch
        GLib.timeout_add_seconds(2, report)

    def _no_picture(self, branch: Branch) -> bool:
        branch.watchdog = 0
        if self.branches.get(branch.widget_id) is branch and not branch.shown and \
                branch.widget_id not in self._no_alpha and any(e.get_name().endswith("-alpha") for e in branch.elements):
            self._fail(branch.widget_id, "format", "no picture with an alpha format")  # opaque, at once
            return False
        if self.branches.get(branch.widget_id) is branch and not branch.shown:
            log(f"widget {branch.widget_id}: no picture after {FIRST_PICTURE_S} s; the stream stays open")
            self.emit("widget-waiting", branch.widget_id)
        return False

    def _fail(self, widget_id: str, reason: str, detail: str = "") -> None:
        branch = self.branches.get(widget_id)
        if branch is not None and reason in ("format", "stopped") and widget_id not in self._no_alpha and \
                any(e.get_name().endswith("-alpha") for e in branch.elements):
            # The cast offers no format with alpha: take it opaque, at once, rather than fail it.
            log(f"widget {widget_id}: the window cast offers no alpha format ({detail[:160]}); taken opaque")
            self._no_alpha.add(widget_id)
            self._remove_branch(widget_id)
            GLib.idle_add(lambda: self.show_scene(self._scene, skip=self._skip, states=self._states) and False)
            return
        if widget_id in self.branches:
            log(f"widget {widget_id} failed: {reason}" + (f": {detail}" if detail else ""))
            self._remove_branch(widget_id)
            self.emit("widget-failed", widget_id, reason)
            self._idle_check()
            # Once its elements are out, the rest of the program runs on (see _keep_playing).
            GLib.timeout_add(100, lambda: self._keep_playing() and False)

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
            for fmt, layer in branch.layers.items():
                target = self.view_of(branch.view or widget, fmt).effects.clamped().opacity
                layer.frame.set_property("opacity", target * branch.fade / FADE_MS)
                self._commit(layer.frame, layer.pad)
            if branch.fade >= FADE_MS:
                self._remove_placeholder(widget.id)  # the real picture is in: the card goes
            return branch.fade < FADE_MS

        GLib.timeout_add(FADE_MS // steps, step)
        return False

    def _sync_monitor(self, branch: Branch, widget: Widget) -> None:
        """A sound widget's monitor exists exactly while it is on (and follows its device)."""
        want = widget.monitor in ("only", "both")
        device = widget.source.get("monitor-device")
        if branch.monitor and (not want or device != branch.monitor_device):
            self._drop_monitor(branch)
        if not want or branch.monitor or self.pipeline is None:
            return
        tee = next((e for e in branch.elements if e.get_factory().get_name() == "tee"), None)
        if tee is None:
            return
        elements = [make(e) for e in graph.monitor_chain(widget, branch.generation)]
        for element in elements:
            self.pipeline.add(element)
        link_all(elements)
        tee.link(elements[0])
        branch.monitor, branch.monitor_device = elements, device
        self.pipeline.call_async(lambda _p, items: [e.sync_state_with_parent() for e in reversed(items)], elements)

    def _drop_monitor(self, branch: Branch) -> None:
        elements, branch.monitor = branch.monitor, []
        if not elements or self.pipeline is None:
            return
        pipeline = self.pipeline
        sink_pad = elements[0].get_static_pad("sink")
        peer = sink_pad.get_peer()
        if peer is not None:
            peer.unlink(sink_pad)
            peer.get_parent_element().release_request_pad(peer)
        for element in elements:
            element.set_locked_state(True)

        def stop(_pipeline, items) -> None:
            for element in items:
                element.set_state(Gst.State.NULL)
            GLib.idle_add(lambda: [pipeline.remove(e) for e in items if e.get_parent() is pipeline] and False)

        pipeline.call_async(stop, elements)

    def _remove_branch(self, widget_id: str) -> None:
        """Take a widget out without waiting: detach it from the mixers when its pads are idle, then
        stop it on GStreamer's thread pool (a stuck source can take its time) and drop it."""
        branch = self.branches.pop(widget_id, None)
        if branch is None or self.pipeline is None:
            return
        self._drop_monitor(branch)
        if branch.watchdog:
            GLib.source_remove(branch.watchdog)
            branch.watchdog = 0
        for fmt in list(branch.layers):
            self._remove_layer(branch, fmt)
        pipeline = self.pipeline
        if branch.pad is not None:
            last = branch.elements[-1].get_static_pad("src")
            # An unlinked live source posts not-linked; keep its data out of the way first.
            last.add_probe(Gst.PadProbeType.BLOCK_DOWNSTREAM, lambda *_a: Gst.PadProbeReturn.DROP)
            last.unlink(branch.pad)
            branch.pad.get_parent_element().release_request_pad(branch.pad)

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
        return any(not b.picture for b in self.branches.values()) or bool(self.outputs) or \
            bool(self.fanout is not None and self.fanout.wants())

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
        # The sound tap goes with the mixer it hangs from (nothing flows there once it stops).
        for element in self._sound_tap:
            element.set_state(Gst.State.NULL)
            self.pipeline.remove(element)
        self._sound_tap = []
        for spec in graph.sound_program():
            element = self.pipeline.get_by_name(spec.name)
            if element is not None:
                element.set_state(Gst.State.NULL)
                self.pipeline.remove(element)
        self._sound = False

    # ── Outputs ──

    def _sent_canvases(self) -> set[str]:
        sent = {o.fmt for o in self.outputs.values()}
        if self.fanout is not None:
            sent |= set(self.fanout.wants())
        return sent

    def sync_taps(self) -> None:
        """Open the taps the outputs read (made on first use, then only their valves open and close);
        a canvas nothing sends has its valve closed, so it converts and reads back nothing.

        The taps stay linked. Taking a tee branch down while the program runs raced the next
        output's GL elements, and a branch shut down before it is unlinked answers the tee
        FLUSHING and stops the program; a closed valve costs nothing either way."""
        if self.pipeline is None:
            return
        self._sync_programs()
        sent = self._sent_canvases()
        if sent:
            self._ensure_sound()
        for fmt, program in self.programs.items():
            if fmt in sent and not program.tap:
                program.tap = self._attach_tap(("video", fmt), program.tee, [make(e) for e in graph.tap_video(fmt)])
            if program.tap:
                program.tap[0].set_property("drop", fmt not in sent)
        if sent and not self._sound_tap and self._sound:
            self._sound_tap = self._attach_tap("sound", self.pipeline.get_by_name("sound"),
                                               [make(e) for e in graph.tap_sound()])
        if self._sound_tap:
            self._sound_tap[0].set_property("drop", not sent)
        if not sent:
            self._sync_sound()
        if self.enabled:
            # A canvas an output starts taking captures its pictures; one kept only for an output that
            # ended goes now.
            self.show_scene(self._scene, skip=self._skip, states=self._states)

    # Older names.
    def _ensure_taps(self) -> None:
        self.sync_taps()

    def _drop_taps(self) -> None:
        self.sync_taps()

    def _attach_tap(self, key, tee: Gst.Element, elements: list[Gst.Element]) -> list[Gst.Element]:
        for element in elements:
            self.pipeline.add(element)
        link_all(elements)
        tee.link(elements[0])
        elements[-1].connect("new-sample", self._sample, key)
        for element in reversed(elements):
            element.sync_state_with_parent()
        return elements

    def _sample(self, sink: Gst.Element, key) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        if key == "sound":
            for output in list(self.outputs.values()):
                output.push(output.sound_src, sample)
            if self.fanout is not None:
                self.fanout.push_sound(sample)
        else:
            fmt = key[1]
            for output in list(self.outputs.values()):
                if output.fmt == fmt:
                    output.push(output.video_src, sample)
            if self.fanout is not None:
                self.fanout.push_video(fmt, sample)
        return Gst.FlowReturn.OK

    def start_output(self, kind: str, choice: EncoderChoice, sink: list[Element], *, video_kbps: int,
                     audio_kbps: int = 160, fmt: str | None = None) -> Output:
        if not self.enabled:
            raise EngineError("nothing to send: the program isn't running")
        fmt = fmt or (self.shown_canvases[0] if self.shown_canvases else "landscape")
        self._open()
        if kind in self.outputs:
            raise EngineError(f"already {kind}ing")
        output = Output(self, kind, choice, sink, video_kbps, audio_kbps, with_sound=True, fmt=fmt)
        self.outputs[kind] = output
        self._ensure_sound()
        self.sync_taps()
        self._sync_placeholder_alpha()

        def finished() -> None:
            if self.outputs.get(kind) is output:
                del self.outputs[kind]
            self._sync_placeholder_alpha()
            if self.pipeline is not None:
                self.sync_taps()
                if not self.outputs:
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

    def clock_now(self) -> tuple[Gst.Clock, int, int] | None:
        """(clock, base time, running time) of the program, for an output pipeline of its own to share."""
        if self.pipeline is None:
            return None
        clock = self.pipeline.get_clock()
        if clock is None:
            return None
        return clock, self.pipeline.get_base_time(), clock.get_time() - self.pipeline.get_base_time()

    # ── Recording and streaming ──

    def start_record(self, path: str, *, container: str = "mkv", codec: str = "h264",
                     video_kbps: int = 12000, fmt: str | None = None) -> Output:
        choice = choose_encoders(codec) or (choose_encoders("av1") if codec == "h264" else None)
        if choice is None:
            raise EngineError("No video encoder works on this computer")
        return self.start_output("record", choice, graph.record_sink(container, path), video_kbps=video_kbps,
                                 audio_kbps=192, fmt=fmt)

    def stop_record(self) -> None:
        self.stop_output("record")

    def start_stream(self, url: str, *, codec: str = "h264", video_kbps: int = 6000, audio_kbps: int = 160,
                     fmt: str | None = None) -> Output:
        """Go live; a dropped connection reconnects with backoff (2 s, ×1.5, up to 15 min)."""
        choice = choose_encoders(codec)
        if choice is None:
            raise EngineError("No H.264 encoder works on this computer" if codec == "h264"
                              else "No AV1 encoder works on this computer")
        if choice.audio is None:
            raise EngineError("No AAC encoder is installed")
        self.stream_retry = {"url": url, "choice": choice, "video": video_kbps, "audio": audio_kbps,
                             "attempt": 0, "timer": 0, "started": time.monotonic(), "fmt": fmt}
        return self._open_stream()

    def _open_stream(self) -> Output:
        retry = self.stream_retry
        enhanced = retry["choice"].codec != "h264" and Gst.ElementFactory.find("eflvmux") is not None
        output = self.start_output("stream", retry["choice"], graph.stream_sink(retry["url"], enhanced=enhanced),
                                   video_kbps=retry["video"], audio_kbps=retry["audio"], fmt=retry.get("fmt"))
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
            name = encoder.get_factory().get_name()
            if name == "svtav1enc":
                encoder.set_property("target-bitrate", kbps)  # read at the next key frame
            else:
                encoder.set_property("bitrate", kbps * (1000 if name == "openh264enc" else 1))

    # ── Bus ──

    def _message(self, _bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.STATE_CHANGED and message.src is self.pipeline:
            old, new, _pending = message.parse_state_changed()
            if old == Gst.State.PLAYING and new == Gst.State.PAUSED and self.enabled:
                GLib.idle_add(lambda: self._keep_playing() and False)
        elif message.type == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure is not None and structure.get_name() == "level":
                name = message.src.get_name()
                widget_id = graph.widget_of(name) if name.endswith("-level") else None
                if widget_id is not None:
                    peaks = structure.get_value("peak")
                    left = peaks[0] if peaks else -60.0
                    right = peaks[1] if len(peaks) > 1 else left
                    self.emit("levels", widget_id, max(-60.0, left), max(-60.0, right))
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            source = message.src.get_name() if message.src else ""
            widget_id = graph.widget_of(source)
            if widget_id is not None and widget_id in self.branches:
                # One widget failed (a window closed, a camera unplugged or busy): take it out, keep going.
                self._fail(widget_id, source_problem(error.message, debug or ""),
                           f"{error.message} ({(debug or '').splitlines()[0] if debug else ''})")
            else:
                self.emit("problem", error.message)
