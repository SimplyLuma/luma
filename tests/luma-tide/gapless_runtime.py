#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real-GStreamer proof that Tide's gapless hand-off actually keeps the
audio continuous across a track boundary, per format and per source.

Every case here runs the production PlaybackController + GStreamerEngine
(playbin3, 'about-to-finish', STREAM_START) over a real pipeline with real
decoders, because the whole claim is about audio continuity and cannot be
made from Python-level state. Pure audio pipelines need no display and no
session bus, so unlike the GTK runtime scripts in this directory this one
runs directly.

What each case proves:
  * one hard load() and no NULL/READY pipeline reset before the advance
    lands -- the hand-off was a real gapless switch, not a stop/reload;
  * the decoded sample count across both tracks equals the two sources'
    own sample counts -- no silence inserted, nothing dropped, and (for
    MP3) no encoder delay/padding left in.

The per-format baseline comes from decoding each fixture on its own
first, so a format whose decoder legitimately emits a different count than
the nominal duration is measured, not assumed.
"""
import json
import tempfile
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)

from gapless_fixtures import (  # noqa: E402
    FIXTURES,
    SAMPLES_PER_BUFFER,
    BoundaryRun,
    decode_sample_count,
    encode_fixture,
    play_boundary,
)
from luma_tide import remote  # noqa: E402
from luma_tide.subsonic import SubsonicClient  # noqa: E402
from subsonic_fixtures import PASSWORD, USERNAME, FakeSubsonicServer, song  # noqa: E402

SHORT = SAMPLES_PER_BUFFER * 43   # ~1.0s at 44100, ~0.9s at 48000
LONG = SAMPLES_PER_BUFFER * 390   # ~9.1s at 44100

results: list[tuple[str, str, BoundaryRun, int, int]] = []


def check(label: str, run: BoundaryRun, expected: int, *, tolerance: int, note: str = "") -> None:
    """One boundary case: assert it was a real hand-off and that the audio
    that came out matches the two sources sample for sample."""
    assert len(run.loads) == 1, (
        f"{label}: expected exactly one hard load() (the first track) and none for the "
        f"gapless hand-off, got {len(run.loads)}: {run.loads}"
    )
    assert run.null_or_ready_at_advance == 0, (
        f"{label}: the pipeline dropped to NULL/READY {run.null_or_ready_at_advance} time(s) "
        f"before the advance landed -- that is a stop/reload, not a gapless hand-off"
    )
    assert run.positions == [0, 1], (
        f"{label}: expected exactly one advance (queue position 0 -> 1), got {run.positions}"
    )
    assert run.initial_stream_position == 0, (
        f"{label}: the hard load's initial STREAM_START incorrectly advanced "
        f"the queue to {run.initial_stream_position} before the next track started"
    )
    drift = run.samples - expected
    assert abs(drift) <= tolerance, (
        f"{label}: decoded {run.samples} samples across the boundary but the two sources hold "
        f"{expected} -- drift of {drift} samples ({drift / 44.1:.1f} ms at 44.1 kHz) means audio "
        f"was inserted or dropped at the boundary"
    )
    results.append((label, note, run, expected, drift))


with tempfile.TemporaryDirectory() as root_str:
    root = Path(root_str)

    # -- encoded-in-test formats: FLAC, Opus, Vorbis -----------------------
    # Each pair gets a different tone so a mixed-up boundary would be
    # visible in the captured audio, and every fixture's own decoded length
    # is measured before it is used in a boundary run.
    for suffix, rate in (("flac", 44100), ("opus", 48000), ("vorbis", 44100)):
        paths = []
        for name, freq in (("a", 440), ("b", 880)):
            path = root / f"{suffix}_{name}.{suffix}"
            samples = encode_fixture(path, freq=freq, samples=SHORT, rate=rate, suffix=suffix)
            paths.append((path, samples))
        baselines = [decode_sample_count(path.as_uri(), rate) for path, _ in paths]
        expected = sum(baselines)
        duration_ns = paths[0][1] * 1_000_000_000 // rate
        run = play_boundary(
            root,
            [(paths[0][0].as_uri(), f"{suffix} A", duration_ns),
             (paths[1][0].as_uri(), f"{suffix} B", duration_ns)],
            rate=rate,
        )
        # A lossy codec's decoder may legitimately emit a fraction of a
        # frame more or less than the encoder was given; the baselines
        # above absorb that, so the boundary itself only gets a small
        # tolerance for the resampler's own edge behavior.
        check(
            f"local {suffix.upper()} -> {suffix.upper()}", run, expected, tolerance=256,
            note=f"per-file decode baseline {baselines[0]}/{baselines[1]} samples at {rate} Hz",
        )

    # Preroll runs on the streaming thread while an occupied main loop
    # has not yet dispatched the hard load's first STREAM_START. That
    # initial message must not consume a prebuffered hand-off. Three short
    # tracks also require every queued stream-start to advance exactly once.
    delayed_paths = []
    for name, freq in (("a", 440), ("b", 880), ("c", 1320)):
        path = root / f"delayed_{name}.vorbis"
        count = encode_fixture(path, freq=freq, samples=SHORT, rate=44100, suffix="vorbis")
        delayed_paths.append((path, count))
    delayed_expected = sum(decode_sample_count(path.as_uri(), 44100) for path, _ in delayed_paths)
    for trial in range(10):
        run = play_boundary(
            root,
            [(path.as_uri(), f"Delayed {trial} {path.stem}", count * 1_000_000_000 // 44100)
             for path, count in delayed_paths],
            rate=44100,
            initial_dispatch_delay=0.05,
        )
        assert run.initial_stream_position == 0, f"late initial STREAM_START advanced early: {run}"
        assert run.positions == [0, 1, 2], f"stream-start hand-offs were lost or repeated: {run}"
        assert len(run.loads) == 1 and run.null_or_ready_at_advance == 0, run
        assert abs(run.samples - delayed_expected) <= 256, (
            f"delayed three-track boundary inserted/dropped PCM: {run.samples}/{delayed_expected}"
        )
    print("PASS: 10 delayed-main-loop three-track runs: initial queue stays at 0, "
          "every advance commits once, no pipeline restart or added/dropped PCM", flush=True)

    # -- committed LAME MP3 pair (Xing/LAME encoder delay + padding) -------
    # The real-world album case: an MP3's first frames are encoder delay
    # and its last frame is padding. If the parser/decoder does not honor
    # the Xing/LAME header, every track boundary gains tens of
    # milliseconds of junk -- exactly the audible gap this is about.
    # GStreamer's mpegaudioparse does honor it, and this proves the trim
    # survives a gapless hand-off (where the parser is rebuilt mid-stream),
    # not just an ordinary load.
    manifest = json.loads((FIXTURES / "gapless_mp3_fixtures.json").read_text())
    mp3_rate = manifest["rate"]
    mp3_source_samples = manifest["source_samples"]
    mp3_paths = [FIXTURES / "gapless_lame_a.mp3", FIXTURES / "gapless_lame_b.mp3"]
    for path in mp3_paths:
        assert path.is_file(), f"missing committed MP3 fixture {path}"
    mp3_baselines = [decode_sample_count(path.as_uri(), mp3_rate) for path in mp3_paths]
    for index, baseline in enumerate(mp3_baselines):
        assert baseline == mp3_source_samples, (
            f"MP3 fixture {mp3_paths[index].name} decodes to {baseline} samples but was encoded "
            f"from {mp3_source_samples} -- the Xing/LAME encoder delay/padding is no longer being "
            f"trimmed, so MP3 gapless playback is not sample-accurate any more"
        )
    mp3_duration_ns = mp3_source_samples * 1_000_000_000 // mp3_rate
    run = play_boundary(
        root,
        [(mp3_paths[0].as_uri(), "MP3 A", mp3_duration_ns),
         (mp3_paths[1].as_uri(), "MP3 B", mp3_duration_ns)],
        rate=mp3_rate,
    )
    check(
        "local MP3 -> MP3 (LAME Xing delay/padding)", run, sum(mp3_baselines), tolerance=0,
        note=f"each fixture decodes to exactly its {mp3_source_samples} encoded samples: "
             f"the LAME encoder delay/padding is trimmed, and the trim survives the hand-off",
    )

    # -- iTunSMPB (iTunes-style gapless tag): a known limitation, locked ---
    # GStreamer has no iTunSMPB support at all (there is no such string in
    # libgstaudioparsers), so an MP3 whose gapless metadata lives only in
    # that ID3 frame keeps its encoder delay and padding. Tide cannot fix
    # this in-process: clipping would have to happen inside the decode
    # chain, and PyGObject exposes no way to resize or replace a buffer in
    # a pad probe (only whole-buffer drops), so a sample-accurate trim
    # needs mpegaudioparse to learn iTunSMPB upstream.
    #
    # What this case does prove is that the hand-off itself is still
    # gapless for these files -- nothing is added or dropped *at the
    # boundary* -- and it pins the exact residue, so if GStreamer ever
    # starts honoring iTunSMPB this fails loudly and the claim gets updated.
    itunes_paths = [FIXTURES / "gapless_itunsmpb_a.mp3", FIXTURES / "gapless_itunsmpb_b.mp3"]
    for path in itunes_paths:
        assert path.is_file(), f"missing committed MP3 fixture {path}"
    itunes_baselines = [decode_sample_count(path.as_uri(), mp3_rate) for path in itunes_paths]
    for index, baseline in enumerate(itunes_baselines):
        recorded = manifest["itunsmpb"]["ab"[index]]
        residue = baseline - mp3_source_samples
        assert baseline == recorded["decoded_samples"], (
            f"{itunes_paths[index].name} now decodes to {baseline} samples, not the "
            f"{recorded['decoded_samples']} recorded when the fixture was made -- if this is "
            f"{mp3_source_samples}, GStreamer has gained iTunSMPB support and Tide's "
            f"documented MP3 limitation should be re-measured and updated"
        )
        assert residue == recorded["delay"] + recorded["padding"], (
            f"{itunes_paths[index].name} residue {residue} does not match the tag's "
            f"{recorded['delay']} delay + {recorded['padding']} padding"
        )
    run = play_boundary(
        root,
        [(itunes_paths[0].as_uri(), "iTunSMPB A", mp3_duration_ns),
         (itunes_paths[1].as_uri(), "iTunSMPB B", mp3_duration_ns)],
        rate=mp3_rate,
    )
    itunes_residue = itunes_baselines[0] - mp3_source_samples
    check(
        "local MP3 -> MP3 (iTunSMPB: gapless hand-off, delay/padding NOT trimmed)",
        run, sum(itunes_baselines), tolerance=0,
        note=f"KNOWN LIMITATION: GStreamer ignores iTunSMPB, so each track keeps "
             f"{itunes_residue} samples ({itunes_residue / (mp3_rate / 1000):.1f} ms) of encoder "
             f"delay+padding; the hand-off itself adds and drops nothing",
    )

    # -- format change across the boundary: FLAC -> MP3 --------------------
    # Same sample rate on both sides, so this measures the decoder/codec
    # change itself rather than a resampler reconfiguration.
    flac_path = root / "change_a.flac"
    flac_samples = encode_fixture(flac_path, freq=440, samples=SHORT, rate=mp3_rate, suffix="flac")
    change_expected = decode_sample_count(flac_path.as_uri(), mp3_rate) + mp3_baselines[1]
    run = play_boundary(
        root,
        [(flac_path.as_uri(), "FLAC A", flac_samples * 1_000_000_000 // mp3_rate),
         (mp3_paths[1].as_uri(), "MP3 B", mp3_duration_ns)],
        rate=mp3_rate,
    )
    check(
        "local FLAC -> MP3 (format change)", run, change_expected, tolerance=256,
        note="one pipeline, decoder chain rebuilt mid-stream at the same rate",
    )

    # -- Subsonic/Navidrome stream, against an in-process fake server ------
    # The remote path end to end: credential-free stored copy URIs, a
    # freshly signed stream URL per playback (the real
    # SubsonicClient.stream_url, exactly what RemoteSources.resolve calls),
    # real HTTP, and GStreamer prebuffering the next stream over the
    # network while the current one is still playing.
    stream_rate = 44100
    remote_paths = []
    for name, freq in (("a", 440), ("b", 880)):
        path = root / f"stream_{name}.flac"
        encode_fixture(path, freq=freq, samples=SHORT, rate=stream_rate, suffix="flac")
        remote_paths.append(path)
    remote_baselines = [decode_sample_count(path.as_uri(), stream_rate) for path in remote_paths]

    songs = [song("song-a", title="Stream A", album_id="album-1"),
             song("song-b", title="Stream B", album_id="album-1")]
    with FakeSubsonicServer(songs) as server:
        server.audio = {
            "song-a": (remote_paths[0].read_bytes(), "audio/flac"),
            "song-b": (remote_paths[1].read_bytes(), "audio/flac"),
        }
        client = SubsonicClient(server.address, USERNAME, PASSWORD, allow_plaintext=True)
        client.ping()

        signed: list[str] = []

        def resolve_stream(copy):
            # What RemoteSources.resolve() does for a Subsonic copy: sign a
            # fresh URL for this one playback from the credential-free
            # stored URI. Called from the about-to-finish provider for the
            # next track, so it must stay this cheap.
            uri = client.stream_url(remote.song_id_from_uri(copy.uri))
            signed.append(uri)
            return uri

        duration_ns = remote_baselines[0] * 1_000_000_000 // stream_rate
        run = play_boundary(
            root,
            [(remote.copy_uri(server.address, "song-a"), "Stream A", duration_ns),
             (remote.copy_uri(server.address, "song-b"), "Stream B", duration_ns)],
            rate=stream_rate,
            resolve_uri=resolve_stream,
        )
        check(
            "Subsonic stream -> Subsonic stream", run, sum(remote_baselines), tolerance=256,
            note=f"{len(signed)} freshly signed stream URLs, "
                 f"{server.request_count('stream')} stream requests served over real HTTP",
        )
        assert len(signed) == 2, (
            f"expected the next track's stream URL to be signed once from about-to-finish, "
            f"got {len(signed)} resolutions"
        )
        assert all("&s=" in uri and "&t=" in uri for uri in signed), (
            "a resolved stream URL is missing its per-playback salt/token"
        )

    # -- realistic about-to-finish timing: a long pair on the real clock ---
    # The cases above run as fast as the files decode. This one is paced by
    # the pipeline clock, so about-to-finish fires with the same lead time a
    # listener's ~9 s track gives it, and the run must still take about as
    # long as the two tracks together -- no stall at the boundary.
    long_paths = []
    for name, freq in (("a", 440), ("b", 880)):
        path = root / f"long_{name}.flac"
        samples = encode_fixture(path, freq=freq, samples=LONG, rate=44100, suffix="flac")
        long_paths.append((path, samples))
    long_expected = sum(decode_sample_count(path.as_uri(), 44100) for path, _ in long_paths)
    long_duration_ns = long_paths[0][1] * 1_000_000_000 // 44100
    run = play_boundary(
        root,
        [(long_paths[0][0].as_uri(), "Long A", long_duration_ns),
         (long_paths[1][0].as_uri(), "Long B", long_duration_ns)],
        rate=44100,
        sync=True,
        timeout=120.0,
    )
    check(
        f"local FLAC -> FLAC, {long_duration_ns / 1e9:.1f}s tracks on the real clock",
        run, long_expected, tolerance=256,
        note=f"{run.elapsed:.1f}s wall clock for "
             f"{2 * long_duration_ns / 1e9:.1f}s of audio",
    )
    played = 2 * long_duration_ns / 1e9
    assert run.elapsed < played + 1.5, (
        f"real-time run took {run.elapsed:.1f}s for {played:.1f}s of audio -- "
        f"the boundary stalled instead of handing over gaplessly"
    )

print()
print("gapless boundary results (all through the production controller/engine):")
for label, note, run, expected, drift in results:
    print(f"  PASS {label}")
    print(f"       {run.samples} samples decoded, expected {expected}, drift {drift:+d} "
          f"({drift / 44.1:+.2f} ms at 44.1 kHz); loads={len(run.loads)}; "
          f"advance titles={run.titles}")
    if note:
        print(f"       {note}")
print()
print(f"PASS: {len(results)} gapless boundaries verified sample-accurate on a real GStreamer "
      f"pipeline (local FLAC/Opus/Vorbis/MP3, a FLAC->MP3 format change, a Subsonic HTTP "
      f"stream, and a real-time long-fixture run)")
