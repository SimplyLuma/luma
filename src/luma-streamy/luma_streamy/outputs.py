# SPDX-License-Identifier: Apache-2.0
"""Outputs: every destination row live at once, one encode per canvas and preset.

The engine hands this module the program's raw pictures (NV12, per canvas) and sound (S16LE
48 kHz stereo) from its taps; ``Outputs`` encodes each (canvas, preset) once and tees the encoded
samples to every row that shares it. Each row is its own pipeline (output_graph.py has the shapes),
so a row that loses its connection reconnects alone (2 s, ×1.5, at most 15 min) and joins again on
the next key frame, which it asks the encoder for. A recording finishes cleanly: EOS drains its
encoder, then its muxer writes the file's index.

Samples arrive on GStreamer threads; every state change (build, teardown, reconnect) runs on the
GLib main loop.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GLib, GObject, Gst, GstVideo  # noqa: E402

from . import output_graph as og  # noqa: E402
from .graph import Element, EncoderChoice, backoff  # noqa: E402
from .output_graph import Preset, RowStats  # noqa: E402

#: A stream whose connection is gone never sees its EOS: it is torn down after this. A recording
#: waits longer, so a busy machine still drains its encoder and finishes the file.
STREAM_FINISH_S = 5
RECORD_FINISH_S = 30


def log(text: str) -> None:
    if os.environ.get("STREAMY_DEBUG"):
        print(f"streamy-outputs: {text}", flush=True)


def build(spec: Element) -> Gst.Element:
    element = Gst.ElementFactory.make(spec.factory, spec.name)
    if element is None:
        raise OutputError(f"{spec.factory} is not installed")
    for name, value in spec.props.items():
        if name == "caps":
            value = Gst.Caps.from_string(value)
        if "::" in name:  # a child's property (whipclientsink's signaller)
            element.set_property(name, value) if hasattr(element, "set_property") else None
            continue
        element.set_property(name, value)
    return element


def fresh(buffer: Gst.Buffer) -> Gst.Buffer:
    """A new buffer on the same memory whose times can be set. Buffer.copy() in Python is the boxed
    copy, a ref: setting its pts would change what every other row or encode receives."""
    return buffer.copy_region(Gst.BufferCopyFlags.FLAGS | Gst.BufferCopyFlags.TIMESTAMPS |
                              Gst.BufferCopyFlags.META | Gst.BufferCopyFlags.MEMORY, 0, buffer.get_size())


def link_all(elements: list[Gst.Element]) -> None:
    for a, b in zip(elements, elements[1:]):
        if not a.link(b):
            raise OutputError(f"{a.get_name()} does not link to {b.get_name()}")


class OutputError(RuntimeError):
    pass


def link_to_mux(element: Gst.Element, mux: Gst.Element, caps: Gst.Caps) -> bool:
    """Link ``element`` to the mux's request pad whose template takes ``caps`` (Element.link picks
    by the queue's still-open caps and can take the wrong one)."""
    for template in mux.get_pad_template_list():
        if template.direction == Gst.PadDirection.SINK and template.get_caps().can_intersect(caps):
            pad = mux.request_pad_simple(template.name_template) if template.presence == Gst.PadPresence.REQUEST \
                else mux.get_static_pad(template.name_template)
            if pad is not None and element.get_static_pad("src").link(pad) == Gst.PadLinkReturn.OK:
                return True
    return False


def working_encoders() -> set[str]:
    """The encoders installed here that really encode (a stub library or a missing driver fails)."""
    from .engine import available_factories, encoder_works
    from .graph import AV1, H264

    return {f for f in available_factories() if f not in H264 + AV1 or encoder_works(f)}


def _on_main(fn: Callable, *args) -> None:
    GLib.idle_add(lambda: fn(*args) and False)


class Row:
    """One destination's mux and sink: built on the first key frame, rebuilt after a dropped connection."""

    def __init__(self, outputs: "Outputs", group: "Encode", row_id: str, kind: str, target: str,
                 *, container: str = "mkv") -> None:
        self.outputs, self.group, self.id, self.kind, self.target = outputs, group, row_id, kind, target
        self.container = container
        self.stats = RowStats(state="connecting", started=time.monotonic())
        self.pipeline: Gst.Pipeline | None = None
        self.video_src = self.sound_src = None
        self.t0: int | None = None
        self.need_key = True
        self.stopping = False
        self.timer = 0
        self.attempt = 0
        self.finished = False
        self._last_bytes = 0
        self._last_tick = time.monotonic()
        self._lock = threading.Lock()
        self._bus_id = 0

    @property
    def is_stream(self) -> bool:
        return self.kind != "file"

    # ── Samples (GStreamer threads) ──

    def push_video(self, sample: Gst.Sample) -> None:
        if self.stopping:
            return
        buffer = sample.get_buffer()
        key = not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
        with self._lock:
            if self.pipeline is None:
                if self.group.sound_src is not None and self.group.sound_caps is None:
                    return  # the sound's first frame isn't out yet: start with both, on a later key frame
                if not key or self.timer:
                    if not key and self.need_key:
                        self.need_key = False
                        self.group.request_key()
                    return
                # A key frame: the row starts here. Built on this thread, so not one frame is lost.
                try:
                    self._build(sample.get_caps())
                except (OutputError, GLib.Error) as error:
                    _on_main(self._failed, str(error))
                    return
                self.t0 = buffer.pts
            src = self.video_src
            if src.get_property("current-level-buffers") >= src.get_property("max-buffers"):
                self.stats.dropped_network += 1
                self.need_key = True  # what follows a dropped picture is useless until the next key frame
                return
            if self.need_key and self.t0 is not None and buffer.pts != self.t0:
                if not key:
                    self.stats.dropped_network += 1
                    return
            self.need_key = False
            self.stats.frames += 1
            src.emit("push-buffer", self._shift(buffer))

    def push_sound(self, sample: Gst.Sample) -> None:
        with self._lock:
            if self.stopping or self.sound_src is None or self.t0 is None:
                return
            buffer = sample.get_buffer()
            if buffer.pts == Gst.CLOCK_TIME_NONE or buffer.pts < self.t0:
                return
            self.sound_src.emit("push-buffer", self._shift(buffer))

    def _shift(self, buffer: Gst.Buffer) -> Gst.Buffer:
        out = fresh(buffer)
        out.pts = buffer.pts - self.t0
        if buffer.dts != Gst.CLOCK_TIME_NONE:
            out.dts = max(0, buffer.dts - self.t0) if buffer.dts >= self.t0 else Gst.CLOCK_TIME_NONE
        return out

    # ── Pipeline ──

    def _build(self, video_caps: Gst.Caps) -> None:
        codec = self.group.choice.codec
        pipeline = Gst.Pipeline.new(f"streamy-row-{self.id}")
        tail = [build(e) for e in og.row_sink(self.id, self.kind, self.target, codec=codec,
                                              container=self.container)]
        head = [build(e) for e in og.row_head(self.id, "video")]
        adapter = [build(e) for e in og.video_adapter(self.id, self.kind, codec)]
        for element in head + adapter + tail:
            pipeline.add(element)
        head[0].set_property("caps", video_caps)
        link_all(head + adapter)
        mux = tail[0]
        if not link_to_mux((head + adapter)[-1], mux, video_caps if not adapter else
                           Gst.Caps.from_string(og.video_adapter(self.id, self.kind, codec)[-1].props["caps"])):
            raise OutputError(f"{codec} doesn't go to {mux.get_factory().get_name()}")
        link_all(tail)
        sound = None
        if self.group.sound_caps is not None:
            sound = [build(e) for e in og.row_head(self.id, "sound")]
            for element in sound:
                pipeline.add(element)
            sound[0].set_property("caps", self.group.sound_caps)
            link_all(sound)
            if not link_to_mux(sound[-1], mux, self.group.sound_caps):
                raise OutputError(f"AAC doesn't go to {mux.get_factory().get_name()}")
        counted = tail[-1].get_static_pad("sink")
        counted.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST, self._count)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_id = bus.connect("message", self._message)
        self.pipeline, self.video_src = pipeline, head[0]
        self.sound_src = sound[0] if sound else None
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise OutputError("the destination could not start")
        log(f"row {self.id}: built {self.kind} {self.target}")

    def _count(self, _pad, info) -> Gst.PadProbeReturn:
        if info.type & Gst.PadProbeType.BUFFER_LIST:
            blist = info.get_buffer_list()
            self.stats.bytes += sum(blist.get(i).get_size() for i in range(blist.length())) if blist else 0
        else:
            buffer = info.get_buffer()
            if buffer is not None:
                self.stats.bytes += buffer.get_size()
        return Gst.PadProbeReturn.OK

    def _message(self, _bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.EOS:
            self._teardown()
            self._done()
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            log(f"row {self.id}: {error.message} {debug}")
            self._failed(error.message)

    def _failed(self, message: str) -> None:
        self._teardown()
        if self.stopping:
            self._done()
            return
        words = og.error_words(message, self.kind)
        if not self.is_stream:
            self.stats.state, self.stats.words = "error", words
            self.outputs._row_gone(self)  # gone before anyone hears it ended
            self.outputs.emit("row-state", self.id, "error", words)
            return
        self.attempt += 1
        self.stats.reconnects += 1
        delay = backoff(self.attempt)
        self.stats.state, self.stats.words = "reconnecting", f"{words}"
        self.outputs.emit("row-state", self.id, "reconnecting", og.state_words(self.stats))

        def again() -> bool:
            with self._lock:
                self.timer = 0
                self.need_key = True
            if not self.stopping:
                self.stats.state = "connecting"
                self.group.request_key()
                self.outputs.emit("row-state", self.id, "connecting", og.state_words(self.stats))
            return False

        with self._lock:
            self.timer = GLib.timeout_add(int(delay * 1000), again)

    def _teardown(self) -> None:
        with self._lock:
            pipeline, self.pipeline = self.pipeline, None
            self.video_src = self.sound_src = None
            self.t0 = None
        if pipeline is None:
            return
        bus = pipeline.get_bus()
        if self._bus_id:
            bus.disconnect(self._bus_id)
            self._bus_id = 0
        bus.remove_signal_watch()
        pipeline.set_state(Gst.State.NULL)

    def stop(self) -> None:
        """Finish: EOS through the muxer (a file gets its index), then down."""
        if self.stopping:
            return
        self.stopping = True
        with self._lock:
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = 0
            srcs = [s for s in (self.video_src, self.sound_src) if s is not None]
        if not srcs:
            self._done()
            return
        self.stats.state = "finishing"
        self.outputs.emit("row-state", self.id, "finishing", og.state_words(self.stats))
        for src in srcs:
            src.emit("end-of-stream")
        GLib.timeout_add_seconds(STREAM_FINISH_S if self.is_stream else RECORD_FINISH_S,
                                 lambda: (self._teardown(), self._done()) and False)

    def _done(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.stats.state, self.stats.bitrate_kbps = "off", 0.0
        if self.kind == "file" and self.stats.frames == 0:
            try:
                os.unlink(self.target)  # a header alone is not a recording
            except OSError:
                pass
        self.outputs._row_gone(self)  # gone before anyone hears it ended: listeners see it off
        self.outputs.emit("row-state", self.id, "off", "")

    # ── Stats (main loop, ≤1 Hz) ──

    def tick(self) -> None:
        now = time.monotonic()
        span = max(1e-3, now - self._last_tick)
        sent = self.stats.bytes - self._last_bytes
        self._last_bytes, self._last_tick = self.stats.bytes, now
        self.stats.bitrate_kbps = sent * 8 / 1000 / span
        sink = self.pipeline.get_by_name(f"row-{self.id}-sink") if self.pipeline is not None else None
        network = None
        if sink is not None and self.kind in ("rtmp", "srt"):
            try:
                info = sink.get_property("stats")
            except TypeError:
                info = None
            if info is not None:
                for field in ("out-bytes-total", "bytes-sent-total", "bytes-sent"):
                    ok, value = info.get_uint64(field)
                    if ok:
                        network = value
                        break
                ok, rtt = info.get_double("rtt-ms")
                self.stats.rtt_ms = rtt if ok else None
        if self.stats.state == "connecting" and self.pipeline is not None and sent > 0 and \
                (network is None or network > 0):
            self.stats.state = "live"
            self.attempt = 0
            self.outputs.emit("row-state", self.id, "live", og.state_words(self.stats))
        if self.stats.state == "live":
            self.stats.history.append(round(self.stats.bitrate_kbps, 1))
            del self.stats.history[:-og.HISTORY]


class Encode:
    """One (canvas, preset) encode: raw program pictures and sound in, encoded samples to its rows."""

    def __init__(self, outputs: "Outputs", preset: Preset, choice: EncoderChoice) -> None:
        self.outputs, self.preset, self.choice = outputs, preset, choice
        self.rows: dict[str, Row] = {}
        self.pipeline: Gst.Pipeline | None = None
        self.video_src = self.sound_src = None
        self.sound_caps: Gst.Caps | None = None
        self.t0: int | None = None
        self.dropped = 0
        self.stopping = False
        self._eos: set[str] = set()
        self._lock = threading.Lock()
        self._encoder: Gst.Element | None = None
        self._vout: Gst.Element | None = None

    def _build(self, caps: Gst.Caps) -> None:
        pipeline = Gst.Pipeline.new(f"streamy-encode-{self.preset.key}")
        video_src = Gst.ElementFactory.make("appsrc", "encode-video")
        for name, value in (("is-live", True), ("format", Gst.Format.TIME), ("do-timestamp", False),
                            ("max-buffers", 8), ("block", False), ("caps", caps)):
            video_src.set_property(name, value)
        video = [build(e) for e in og.encode_video(self.choice, self.preset)]
        pipeline.add(video_src)
        for element in video:
            pipeline.add(element)
        link_all([video_src] + video)
        video[-1].connect("new-sample", self._encoded, "video")
        video[-1].connect("eos", self._encoded_eos, "video")
        self._encoder = next(e for e in video if e.get_name().endswith("-encoder"))
        self._vout = video[-1]
        sound = [build(e) for e in og.encode_sound(self.choice, self.preset)]
        sound_src = None
        if sound:
            sound_src = Gst.ElementFactory.make("appsrc", "encode-sound")
            for name, value in (("is-live", True), ("format", Gst.Format.TIME), ("do-timestamp", False),
                                ("max-buffers", 200), ("block", False),
                                ("caps", Gst.Caps.from_string(
                                    "audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=2"))):
                sound_src.set_property(name, value)
            pipeline.add(sound_src)
            for element in sound:
                pipeline.add(element)
            link_all([sound_src] + sound)
            sound[-1].connect("new-sample", self._encoded, "sound")
            sound[-1].connect("eos", self._encoded_eos, "sound")
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._error)
        self.pipeline, self.video_src, self.sound_src = pipeline, video_src, sound_src
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise OutputError("the encoder could not start")
        log(f"encode {self.preset.key}: {self.choice.video} + {self.choice.audio}")

    # Raw in (GStreamer threads: the engine's taps)

    def push_video(self, sample: Gst.Sample) -> None:
        buffer = sample.get_buffer()
        if self.stopping or buffer.pts == Gst.CLOCK_TIME_NONE:
            return
        with self._lock:
            if self.pipeline is None:
                try:
                    self._build(sample.get_caps())
                except (OutputError, GLib.Error) as error:
                    self.stopping = True
                    _on_main(self.outputs._encode_failed, self, str(error))
                    return
                self.t0 = buffer.pts
            if buffer.pts < self.t0:
                return
            src = self.video_src
            if src.get_property("current-level-buffers") >= src.get_property("max-buffers"):
                self.dropped += 1  # the encoder is behind
                return
            out = fresh(buffer)
            out.pts, out.dts = buffer.pts - self.t0, Gst.CLOCK_TIME_NONE
            src.emit("push-buffer", out)

    def push_sound(self, sample: Gst.Sample) -> None:
        buffer = sample.get_buffer()
        with self._lock:
            if self.stopping or self.sound_src is None or self.t0 is None or buffer.pts == Gst.CLOCK_TIME_NONE:
                return
            if buffer.pts < self.t0:
                return
            out = fresh(buffer)
            out.pts, out.dts = buffer.pts - self.t0, Gst.CLOCK_TIME_NONE
            self.sound_src.emit("push-buffer", out)

    # Encoded out (GStreamer threads)

    def _encoded(self, sink: Gst.Element, media: str) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        if media == "sound":
            if self.sound_caps is None:
                self.sound_caps = sample.get_caps()
            for row in list(self.rows.values()):
                row.push_sound(sample)
        else:
            for row in list(self.rows.values()):
                row.push_video(sample)
        return Gst.FlowReturn.OK

    def _encoded_eos(self, _sink, media: str) -> None:
        self._eos.add(media)
        if self._eos >= ({"video", "sound"} if self.sound_src is not None else {"video"}):
            _on_main(self._drained)

    def request_key(self) -> None:
        """Ask the encoder for a key frame now (a row joining or reconnecting starts on it)."""
        if self._vout is not None:
            self._vout.send_event(GstVideo.video_event_new_upstream_force_key_unit(Gst.CLOCK_TIME_NONE, True, 0))

    def set_bitrate(self, kbps: int) -> None:
        encoder = self._encoder
        if encoder is None:
            return
        name = encoder.get_factory().get_name()
        if name == "svtav1enc":
            encoder.set_property("target-bitrate", kbps)
        else:
            encoder.set_property("bitrate", kbps * (1000 if name == "openh264enc" else 1))

    def _error(self, _bus, message: Gst.Message) -> None:
        error, debug = message.parse_error()
        log(f"encode {self.preset.key}: {error.message} {debug}")
        self.outputs._encode_failed(self, error.message)

    def stop(self) -> None:
        """Drain the encoder (EOS), then finish every row behind it."""
        with self._lock:
            if self.stopping and self.pipeline is None:
                return
            self.stopping = True
            srcs = [s for s in (self.video_src, self.sound_src) if s is not None]
        if not srcs:
            self._drained()
            return
        for src in srcs:
            src.emit("end-of-stream")
        GLib.timeout_add_seconds(RECORD_FINISH_S, lambda: self._drained() and False)

    def _drained(self) -> None:
        for row in list(self.rows.values()):
            row.stop()
        self.close()

    def close(self) -> None:
        with self._lock:
            pipeline, self.pipeline = self.pipeline, None
            self.video_src = self.sound_src = None
        if pipeline is not None:
            pipeline.get_bus().remove_signal_watch()
            pipeline.set_state(Gst.State.NULL)


class Outputs(GObject.Object):
    """Every live destination row: start and stop rows, feed raw program media, read stats."""

    __gtype_name__ = "StreamyOutputs"
    __gsignals__ = {
        # row id, state (connecting | live | reconnecting | finishing | error | off), words
        "row-state": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        # Called once a second while anything is live (the Stats panel redraws on it).
        "tick": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "problem": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, *, working: set[str] | None = None) -> None:
        super().__init__()
        Gst.init(None)
        self._working = working
        self.encodes: dict[Preset, Encode] = {}
        self.rows: dict[str, Row] = {}
        self.stats_of: dict[str, RowStats] = {}
        self._tick = 0
        self._lock = threading.Lock()

    @property
    def working(self) -> set[str]:
        if self._working is None:
            self._working = working_encoders()
        return self._working

    # ── Rows ──

    def start(self, row_id: str, preset: Preset, target: str, *, record: bool = False,
              container: str = "mkv") -> Row:
        """Start one row: ``target`` is its address (stream) or its file's path (recording)."""
        if row_id in self.rows:
            raise OutputError("already live")
        kind = "file" if record else og.kind_of(target)
        if kind == "whip" and Gst.ElementFactory.find("whipclientsink") is None:
            raise OutputError("WHIP needs GStreamer's webrtc plugin, which isn't installed")
        if kind == "srt" and Gst.ElementFactory.find("srtsink") is None:
            raise OutputError("SRT needs GStreamer's srt plugin, which isn't installed")
        choice = og.choose_record(self.working, preset.codec) if record else og.choose(self.working, preset.codec)
        if choice is None:
            raise OutputError("No video encoder works on this computer" if record else
                              f"No {'H.264' if preset.codec == 'h264' else 'AV1'} encoder works on this computer")
        if choice.audio is None and not record:
            raise OutputError("No AAC encoder is installed")
        if record:
            preset = Preset(preset.canvas, choice.codec, preset.video_kbps, preset.audio_kbps)
        with self._lock:
            encode = self.encodes.get(preset)
            if encode is None or encode.stopping:
                encode = self.encodes[preset] = Encode(self, preset, choice)
            row = Row(self, encode, row_id, kind, target, container=container)
            encode.rows[row_id] = row
            self.rows[row_id] = row
        self.stats_of[row_id] = row.stats
        encode.request_key()
        self.emit("row-state", row_id, "connecting", og.state_words(row.stats))
        if not self._tick:
            self._tick = GLib.timeout_add_seconds(1, self._on_tick)
        return row

    def stop(self, row_id: str) -> None:
        row = self.rows.get(row_id)
        if row is None:
            return
        encode = row.group
        if not row.is_stream and len(encode.rows) == 1:
            encode.stop()  # a recording: drain its encoder first, so its last second is in the file
        else:
            row.stop()

    def stop_all(self) -> None:
        for encode in list(self.encodes.values()):
            encode.stop()

    def close_now(self) -> None:
        """Quitting: every row and encode to NULL at once (no drain), so no thread outlives the app."""
        with self._lock:
            rows, encodes = list(self.rows.values()), list(self.encodes.values())
        for row in rows:
            row.stopping = True
            row._teardown()
        for encode in encodes:
            encode.stopping = True
            encode.close()

    def _row_gone(self, row: Row) -> None:
        with self._lock:
            if self.rows.get(row.id) is row:
                del self.rows[row.id]
            encode = row.group
            encode.rows.pop(row.id, None)
            if not encode.rows and not encode.stopping:
                encode.stopping = True
                close = encode
            else:
                close = None
            if not encode.rows and self.encodes.get(encode.preset) is encode:
                del self.encodes[encode.preset]
        if close is not None:
            close.close()

    def _encode_failed(self, encode: Encode, message: str) -> None:
        self.emit("problem", f"The encoder stopped: {message}")
        for row in list(encode.rows.values()):
            row.stats.state, row.stats.words = "error", "The encoder stopped"
            row.stopping = True
            row._teardown()
            self._row_gone(row)
            self.emit("row-state", row.id, "error", row.stats.words)
        encode.close()

    # ── Media from the engine (GStreamer threads) ──

    def wants(self) -> set[str]:
        """The canvases whose pictures an encode needs now (the engine opens those taps only)."""
        return {p.canvas for p, e in list(self.encodes.items()) if not e.stopping}

    def push_video(self, canvas: str, sample: Gst.Sample) -> None:
        for preset, encode in list(self.encodes.items()):
            if preset.canvas == canvas:
                encode.push_video(sample)

    def push_sound(self, sample: Gst.Sample) -> None:
        for encode in list(self.encodes.values()):
            encode.push_sound(sample)

    # ── Stats ──

    def stats(self, row_id: str) -> RowStats | None:
        return self.stats_of.get(row_id)

    def encoder_drops(self, row_id: str) -> int:
        row = self.rows.get(row_id)
        return row.group.dropped if row is not None else 0

    def live(self) -> bool:
        return bool(self.rows)

    def _on_tick(self) -> bool:
        for row in list(self.rows.values()):
            row.stats.dropped_encoder = row.group.dropped
            row.tick()
        self.emit("tick")
        if not self.rows:
            self._tick = 0
            return False
        return True
