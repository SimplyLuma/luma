# SPDX-License-Identifier: Apache-2.0
"""Fixtures and a boundary runner for Tide's gapless playback checks.

Everything here needs a real GStreamer, so only the runtime scripts import
it; the ordinary unit tests use FakeEngine instead.

`encode_fixture` writes short synthetic audio with GStreamer's own
audiotestsrc plus an encoder, so FLAC/Opus/Vorbis fixtures are generated in
the test run and never committed. MP3 is the exception: Fedora ships no MP3
*encoder* GStreamer plugin, so the two LAME-encoded MP3 fixtures (the ones
that actually carry the Xing/LAME encoder delay+padding this has to trim)
are committed under `fixtures/` -- see `regenerate_mp3_fixtures.sh` there.

`play_boundary` runs one two-track queue through the real
PlaybackController and GStreamerEngine and reports what happened at the
boundary: how many hard load()s there were (a gapless hand-off does none),
whether the pipeline ever dropped to NULL/READY before the advance was
confirmed, and exactly how many PCM samples came out the other end. That
sample count is the gapless proof: it must equal the two sources' own
sample counts, with no silence inserted and no encoder delay/padding left
in.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from luma_tide.model import LibraryStore, MediaMetadata  # noqa: E402
from luma_tide.playback import GStreamerEngine, PlaybackController, PlaybackState  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# audiotestsrc emits `samplesperbuffer` samples per buffer; an exact
# multiple keeps every fixture a whole number of samples, so the expected
# decoded sample count is exact rather than approximate.
SAMPLES_PER_BUFFER = 1024

ENCODERS = {
    # suffix: (encoder element(s), natural rate)
    "flac": ("flacenc", 44100),
    "opus": ("opusenc ! oggmux", 48000),
    "vorbis": ("vorbisenc ! oggmux", 44100),
}


def _bytes_per_frame(sample) -> int:
    """How many bytes one frame (one sample across all channels) takes, read
    from the sample's own caps rather than assumed -- so a run that
    negotiated something other than the requested format is caught by the
    sample maths instead of silently scaling it."""
    structure = sample.get_caps().get_structure(0)
    channels = structure.get_value("channels")
    width = {"S16LE": 2, "S16BE": 2, "S32LE": 4, "S32BE": 4, "F32LE": 4, "F32BE": 4,
             "F64LE": 8, "F64BE": 8, "S24_32LE": 4, "S24_32BE": 4}[structure.get_value("format")]
    return channels * width


def observed_caps(sample) -> str:
    return sample.get_caps().to_string()


def encode_fixture(path: Path, *, freq: int, samples: int, rate: int, suffix: str) -> int:
    """Write one fixture and return its exact sample count."""
    encoder, _natural = ENCODERS[suffix]
    buffers = samples // SAMPLES_PER_BUFFER
    exact = buffers * SAMPLES_PER_BUFFER
    pipeline = Gst.parse_launch(
        f"audiotestsrc num-buffers={buffers} samplesperbuffer={SAMPLES_PER_BUFFER} "
        f"wave=sine freq={freq} "
        f"! audio/x-raw,rate={rate},channels=1,format=S16LE ! audioconvert ! {encoder} "
        f"! filesink location={path}"
    )
    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)
    message = bus.timed_pop_filtered(
        30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
    )
    pipeline.set_state(Gst.State.NULL)
    if message is None:
        raise AssertionError(f"fixture encode for {path.name} timed out")
    if message.type == Gst.MessageType.ERROR:
        error, debug = message.parse_error()
        raise AssertionError(f"fixture encode for {path.name} failed: {error} ({debug})")
    return exact


def decode_sample_count(uri: str, rate: int) -> int:
    """Decode one file on its own and count the PCM samples that come out.

    Used as the per-format baseline: it is the same decoder chain playbin
    builds, so comparing it with a boundary run's total separates "the
    decoder emits N samples for this file" from "the gapless hand-off
    added or dropped samples".
    """
    pipeline = Gst.parse_launch(
        f"uridecodebin uri={uri} ! audioconvert ! audioresample "
        f"! audio/x-raw,format=S16LE,rate={rate},channels=1 ! appsink name=sink sync=false"
    )
    sink = pipeline.get_by_name("sink")
    sink.set_property("emit-signals", True)
    total = [0]

    def on_sample(appsink):
        sample = appsink.emit("pull-sample")
        total[0] += sample.get_buffer().get_size() // _bytes_per_frame(sample)
        return Gst.FlowReturn.OK

    sink.connect("new-sample", on_sample)
    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)
    message = bus.timed_pop_filtered(
        60 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
    )
    pipeline.set_state(Gst.State.NULL)
    if message is None:
        raise AssertionError(f"decoding {uri} timed out")
    if message.type == Gst.MessageType.ERROR:
        error, debug = message.parse_error()
        raise AssertionError(f"decoding {uri} failed: {error} ({debug})")
    return total[0]


@dataclass
class BoundaryRun:
    """What actually happened across one two-track boundary."""

    loads: list[str] = field(default_factory=list)
    positions: list[int] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    samples: int = 0
    null_or_ready_at_advance: int | None = None
    elapsed: float = 0.0
    states: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)
    initial_stream_position: int | None = None

    @property
    def gapless(self) -> bool:
        """True only when the boundary was a real hand-off: one load() for
        the first track and nothing else, no pipeline reset before the
        advance landed, and exactly one advance."""
        return (
            len(self.loads) == 1
            and self.null_or_ready_at_advance == 0
            and self.positions == [0, 1]
        )


def play_boundary(
    root: Path,
    tracks: list[tuple[str, str, int]],
    *,
    rate: int,
    sync: bool = False,
    resolve_uri=None,
    timeout: float = 90.0,
    initial_dispatch_delay: float = 0.0,
) -> BoundaryRun:
    """Play `tracks` -- (uri, title, duration_ns) -- as a two-track queue
    through the production controller/engine, with the audio captured from
    an appsink instead of PipeWire.

    `sync=False` runs the pipeline as fast as it decodes, which is what the
    per-format sample-continuity cases want; `sync=True` paces it on the
    real clock, so a long fixture exercises about-to-finish with the timing
    an actual listener gets.
    """
    store = LibraryStore(root / f"library-{abs(hash(tuple(tracks))) % 10**8}.db")
    source = store.add_source("Fixtures", "local-folder", root, local=True)
    ids = []
    for index, (uri, title, duration_ns) in enumerate(tracks):
        track, _copy = store.upsert_copy(
            source.id,
            MediaMetadata(
                uri=uri,
                content_digest=f"{index}{title}".ljust(64, "0")[:64],
                title=title,
                duration_ns=duration_ns,
            ),
        )
        ids.append(track.id)
    store.replace_queue(ids)

    engine = GStreamerEngine(audio_sink="appsink")
    sink = engine._audio_sink
    sink.set_property("caps", Gst.Caps.from_string(
        f"audio/x-raw,format=S16LE,rate={rate},channels=1"
    ))
    sink.set_property("sync", sync)
    sink.set_property("emit-signals", True)

    run = BoundaryRun()
    frames = [0]

    def on_sample(appsink):
        sample = appsink.emit("pull-sample")
        described = observed_caps(sample)
        if described not in run.caps:
            run.caps.append(described)
        frames[0] += sample.get_buffer().get_size() // _bytes_per_frame(sample)
        return Gst.FlowReturn.OK

    sink.connect("new-sample", on_sample)

    original_load = engine.load

    def counting_load(uri, **kwargs):
        run.loads.append(uri)
        original_load(uri, **kwargs)

    engine.load = counting_load

    saw_playing = [False]
    null_or_ready = [0]

    def watch_states(_bus, message):
        if message.type == Gst.MessageType.STREAM_START and run.initial_stream_position is None:
            run.initial_stream_position = controller.snapshot.queue_position
        if message.type == Gst.MessageType.STATE_CHANGED and message.src == engine._pipeline:
            _old, new, _pending = message.parse_state_changed()
            if new == Gst.State.PLAYING:
                saw_playing[0] = True
            elif saw_playing[0] and new in (Gst.State.NULL, Gst.State.READY):
                null_or_ready[0] += 1

    engine._bus.connect("message", watch_states)

    controller = PlaybackController(store, engine, resolve_uri=resolve_uri)

    def on_snapshot(snapshot):
        if not run.states or run.states[-1] != snapshot.state.value:
            run.states.append(snapshot.state.value)
        if not run.positions or run.positions[-1] != snapshot.queue_position:
            run.positions.append(snapshot.queue_position)
            if snapshot.track is not None:
                run.titles.append(snapshot.track.title)
            # The NULL/READY count at the moment the advance landed -- not
            # the final count, which legitimately includes the one reset
            # from stop() after the last track's real EOS.
            if snapshot.queue_position == 1 and run.null_or_ready_at_advance is None:
                run.null_or_ready_at_advance = null_or_ready[0]

    controller.subscribe(on_snapshot)

    started = time.monotonic()
    controller.play()
    if initial_dispatch_delay:
        # Hold only this observer's main loop. The actual GStreamer
        # streaming threads continue prerolling and prebuffering normally.
        time.sleep(initial_dispatch_delay)
    context = GLib.MainContext.default()
    deadline = started + timeout
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if controller.snapshot.state is PlaybackState.STOPPED and run.positions[-1:] == [len(tracks) - 1]:
            break
        time.sleep(0.005)
    else:
        run.elapsed = time.monotonic() - started
        run.samples = frames[0]
        controller.close()
        store.close()
        raise AssertionError(
            f"boundary run did not finish within {timeout}s: "
            f"states={run.states} positions={run.positions} samples={run.samples}"
        )
    run.elapsed = time.monotonic() - started
    run.samples = frames[0]
    controller.close()
    store.close()
    return run
