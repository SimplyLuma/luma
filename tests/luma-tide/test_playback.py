# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luma_tide.model import LibraryStore, MediaMetadata, RepeatMode, SourceState
from luma_tide.playback import PlaybackController, PlaybackState


class FakeEngine:
    """Models just enough of GStreamerEngine's gapless mechanics for tests:
    about_to_finish() asks the controller-supplied provider for the next
    URI (mirroring playbin's 'about-to-finish' signal) and, if one comes
    back, arms a pending gapless switch *without* touching `loaded` or
    `current_position` yet -- exactly like real playback, the old track
    keeps "playing" until stream_start() (mirroring the GStreamer
    STREAM_START bus message) actually commits the switch and emits
    "advanced". A real end-of-stream with nothing armed is eos()."""

    def __init__(self) -> None:
        self.handler = lambda *_: None
        self.next_track_provider = lambda: None
        self.loaded: list[tuple[str, int, bool]] = []
        self.current_position = 0
        self.volume = 1.0
        self.pending_uri: str | None = None

    def set_event_handler(self, handler): self.handler = handler
    def set_next_track_provider(self, provider): self.next_track_provider = provider
    def load(self, uri, *, position_ns=0, play=False):
        self.pending_uri = None
        self.loaded.append((uri, position_ns, play)); self.current_position = position_ns
        self.handler("state", "playing" if play else "paused")
    def play(self): self.handler("state", "playing")
    def pause(self): self.handler("state", "paused")
    def stop(self):
        self.pending_uri = None
        self.handler("state", "stopped")
    def seek(self, position_ns): self.current_position = position_ns
    def set_volume(self, volume): self.volume = volume
    def position(self): return self.current_position
    def close(self): pass

    # -- gapless test hooks, not part of the real PlaybackEngine protocol --

    def about_to_finish(self) -> str | None:
        """Simulates GStreamer's 'about-to-finish' signal: asks the
        controller what to preload, and if it answers, records the URI as
        armed (like setting playbin's 'uri' property) without changing
        `loaded`/`current_position` -- the currently-playing track is
        untouched until stream_start()."""
        uri = self.next_track_provider()
        self.pending_uri = uri
        return uri

    def stream_start(self) -> bool:
        """Simulates the STREAM_START bus message that follows a gapless
        switch actually taking effect. Returns False (a no-op) if nothing
        was armed, matching the real engine ignoring an ordinary load()'s
        own STREAM_START. Deliberately does *not* append to `loaded`: a
        real gapless hand-off never calls load()/resets pipeline state,
        which is the entire point, so `loaded` stays a record of hard
        reloads only."""
        if self.pending_uri is None:
            return False
        self.current_position = 0
        self.pending_uri = None
        self.handler("advanced", None)
        return True


class PlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = LibraryStore(self.root / "library.db")
        self.local = self.store.add_source("Here", "local-folder", self.root / "music", local=True)
        self.remote = self.store.add_source("Server", "subsonic", "https://music.example", local=False)
        self.tracks = []
        for index in range(3):
            track, _copy = self.store.upsert_copy(
                self.local.id,
                MediaMetadata(
                    uri=(self.root / f"music/{index}.flac").as_uri(),
                    content_digest=str(index) * 64,
                    title=f"Track {index}",
                    duration_ns=60_000_000_000,
                ),
            )
            self.tracks.append(track)
        self.store.replace_queue([track.id for track in self.tracks])
        self.engine = FakeEngine()
        self.controller = PlaybackController(self.store, self.engine)

    def tearDown(self) -> None:
        self.controller.close()
        self.store.close()
        self.temporary.cleanup()

    def test_play_next_previous_and_end_of_queue_are_real(self) -> None:
        self.assertTrue(self.controller.play())
        self.assertEqual(self.controller.snapshot.state, PlaybackState.PLAYING)
        self.assertTrue(self.controller.next())
        self.assertEqual(self.controller.snapshot.track.title, "Track 1")
        self.engine.current_position = 4_000_000_000
        self.controller._on_engine_event("position", self.engine.current_position)
        self.assertTrue(self.controller.previous())
        self.assertEqual(self.controller.snapshot.track.title, "Track 1")
        self.assertEqual(self.controller.snapshot.position_ns, 0)

    def test_selected_track_replaces_current_audio_in_both_directions(self) -> None:
        ids = [track.id for track in self.tracks]
        for start in (0, 1, 0, 2, 1):
            self.controller.replace_queue(ids, start=start)
            self.assertEqual(self.controller.snapshot.track.id, ids[start])
            self.assertEqual(self.controller.snapshot.queue_position, start)
            self.assertEqual(self.engine.loaded[-1][0], (self.root / f"music/{start}.flac").resolve().as_uri())
            self.assertEqual(self.store.session_state().current_position, start)
        self.controller.replace_queue([])
        self.assertIsNone(self.controller.snapshot.track)
        self.assertEqual(self.controller.snapshot.state, PlaybackState.STOPPED)

    def test_repeat_all_wraps_and_repeat_one_restarts(self) -> None:
        self.store.replace_queue([self.tracks[-1].id])
        self.controller._restore_queue()
        self.controller.set_repeat(RepeatMode.ALL)
        self.controller.play()
        self.assertTrue(self.controller.next())
        self.controller.set_repeat(RepeatMode.ONE)
        self.controller._on_engine_event("eos", None)
        self.assertEqual(self.engine.current_position, 0)
        self.assertEqual(self.controller.snapshot.state, PlaybackState.PLAYING)

    def test_switching_copy_keeps_position(self) -> None:
        track = self.tracks[0]
        _track, remote_copy = self.store.upsert_copy(
            self.remote.id,
            MediaMetadata(
                uri="https://music.example/stream/0",
                content_digest="0" * 64,
                title="Track 0",
                duration_ns=60_000_000_000,
            ),
        )
        self.controller.play()
        self.engine.current_position = 17_000_000_000
        self.assertTrue(self.controller.switch_copy(remote_copy))
        self.assertEqual(self.engine.loaded[-1], ("https://music.example/stream/0", 17_000_000_000, True))

    def test_metadata_refresh_keeps_playback_and_copy_identity(self) -> None:
        self.controller.play()
        self.controller.seek(12_000_000_000)
        before = self.controller.snapshot
        loaded = list(self.engine.loaded)
        self.store.upsert_copy(self.local.id, MediaMetadata(
            uri=before.copy.uri, content_digest="0" * 64,
            title="Refreshed title", duration_ns=60_000_000_000,
        ))
        self.controller.refresh_metadata()
        after = self.controller.snapshot
        self.assertEqual(after.track.title, "Refreshed title")
        self.assertEqual((after.track.id, after.copy.id), (before.track.id, before.copy.id))
        self.assertEqual((after.state, after.position_ns), (before.state, before.position_ns))
        self.assertEqual(self.engine.loaded, loaded)

    def test_engine_failure_stays_visible_after_pipeline_stops(self) -> None:
        self.store.replace_queue([self.tracks[0].id])
        self.controller._restore_queue()
        self.controller.play()
        self.engine.handler("error", "Audio output disconnected")
        self.assertEqual(self.controller.snapshot.state, PlaybackState.ERROR)
        self.assertEqual(self.controller.snapshot.error, "Audio output disconnected")
        self.assertEqual(self.store.session_state().playback_state, "error")
        self.controller.play()
        self.assertEqual(self.controller.snapshot.state, PlaybackState.PLAYING)
        self.assertIsNone(self.controller.snapshot.error)

    def test_unavailable_track_fails_honestly(self) -> None:
        self.store.set_source_state(self.local.id, SourceState.OFFLINE)
        self.store.replace_queue([self.tracks[0].id])
        self.controller._restore_queue()
        self.assertFalse(self.controller.play())
        self.assertEqual(self.controller.snapshot.state, PlaybackState.ERROR)
        self.assertIn("No reachable copy", self.controller.snapshot.error)


class GaplessBoundaryTests(unittest.TestCase):
    """Queue/skip/seek behavior around a gapless track boundary, driven
    through FakeEngine's about_to_finish()/stream_start() -- the same
    two-phase hand-off GStreamerEngine performs for real via playbin's
    'about-to-finish' signal and the STREAM_START bus message."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = LibraryStore(self.root / "library.db")
        self.local = self.store.add_source("Here", "local-folder", self.root / "music", local=True)
        self.tracks = []
        for index in range(3):
            track, _copy = self.store.upsert_copy(
                self.local.id,
                MediaMetadata(
                    uri=(self.root / f"music/{index}.flac").as_uri(),
                    content_digest=str(index) * 64,
                    title=f"Track {index}",
                    duration_ns=60_000_000_000,
                ),
            )
            self.tracks.append(track)
        self.store.replace_queue([track.id for track in self.tracks])
        self.engine = FakeEngine()
        self.controller = PlaybackController(self.store, self.engine)

    def tearDown(self) -> None:
        self.controller.close()
        self.store.close()
        self.temporary.cleanup()

    def test_about_to_finish_preloads_without_moving_the_snapshot_or_session(self) -> None:
        self.controller.play()
        loaded_before = list(self.engine.loaded)
        uri = self.engine.about_to_finish()
        self.assertEqual(uri, (self.root / "music/1.flac").resolve().as_uri())
        # The hand-off is armed, but nothing about "what's playing" has
        # moved yet -- the old track is still audibly playing.
        self.assertEqual(self.controller.snapshot.track.title, "Track 0")
        self.assertEqual(self.controller.snapshot.queue_position, 0)
        self.assertEqual(self.store.session_state().current_position, 0)
        self.assertEqual(self.engine.loaded, loaded_before)

    def test_stream_start_commits_the_advance_queue_position_and_metadata(self) -> None:
        self.controller.play()
        self.engine.about_to_finish()
        self.assertTrue(self.engine.stream_start())
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot.track.title, "Track 1")
        self.assertEqual(snapshot.queue_position, 1)
        self.assertEqual(snapshot.position_ns, 0)
        self.assertEqual(snapshot.state, PlaybackState.PLAYING)
        self.assertEqual(self.store.session_state().current_position, 1)
        # No hard reload happened for the gapless hand-off: the only load()
        # call on record is still the original one from play().
        self.assertEqual(len(self.engine.loaded), 1)

    def test_repeat_one_preloads_the_same_track_gaplessly(self) -> None:
        self.controller.set_repeat(RepeatMode.ONE)
        self.controller.play()
        uri = self.engine.about_to_finish()
        self.assertEqual(uri, (self.root / "music/0.flac").resolve().as_uri())
        self.assertTrue(self.engine.stream_start())
        self.assertEqual(self.controller.snapshot.track.title, "Track 0")
        self.assertEqual(self.controller.snapshot.queue_position, 0)
        self.assertEqual(self.controller.snapshot.position_ns, 0)

    def test_manual_skip_during_a_pending_gapless_hand_off_overrides_it(self) -> None:
        self.controller.play()
        self.engine.about_to_finish()
        self.assertIsNotNone(self.engine.pending_uri)
        self.assertTrue(self.controller.next())
        # next() moved the queue position immediately, the ordinary way, and
        # issued a real load() -- the armed gapless URI must not leak in
        # after the fact.
        self.assertEqual(self.controller.snapshot.track.title, "Track 1")
        self.assertEqual(self.controller.snapshot.queue_position, 1)
        self.assertEqual(self.engine.pending_uri, None)
        self.assertFalse(self.engine.stream_start())
        self.assertEqual(self.controller.snapshot.track.title, "Track 1")

    def test_seek_right_after_a_gapless_advance_seeks_the_new_track(self) -> None:
        self.controller.play()
        self.engine.about_to_finish()
        self.engine.stream_start()
        self.controller.seek(5_000_000_000)
        self.assertEqual(self.engine.current_position, 5_000_000_000)
        self.assertEqual(self.controller.snapshot.position_ns, 5_000_000_000)
        self.assertEqual(self.controller.snapshot.track.title, "Track 1")

    def test_end_of_queue_has_nothing_to_preload_and_falls_back_to_eos_stop(self) -> None:
        self.controller.replace_queue([self.tracks[-1].id])
        self.controller.play()
        self.assertIsNone(self.engine.about_to_finish())
        self.controller._on_engine_event("eos", None)
        self.assertEqual(self.controller.snapshot.state, PlaybackState.STOPPED)

    def test_unreachable_next_track_skips_gapless_preload_and_falls_back_on_eos(self) -> None:
        self.controller.play()
        self.assertEqual(self.controller.snapshot.track.title, "Track 0")
        # Track 0 is already loaded and playing; make the whole local
        # source unreachable so *only the next track's* preload fails --
        # the currently-playing track is untouched.
        self.store.set_source_state(self.local.id, SourceState.OFFLINE)
        self.assertIsNone(self.engine.about_to_finish())
        self.assertEqual(self.controller.snapshot.track.title, "Track 0")
        self.assertEqual(self.controller.snapshot.state, PlaybackState.PLAYING)
        self.assertIsNotNone(self.controller.snapshot.error)
        # The real EOS that eventually arrives falls back to the ordinary
        # (gappy) path, which keeps skipping every remaining track (all
        # unreachable, same offline source) until the queue runs out --
        # the pre-existing _move()/eos fallback behavior, unchanged here.
        self.controller._on_engine_event("eos", None)
        self.assertEqual(self.controller.snapshot.state, PlaybackState.STOPPED)

    def test_a_second_about_to_finish_before_stream_start_does_not_repeat_a_track(self) -> None:
        """The engine's about-to-finish runs on GStreamer's thread and can
        fire for the handed-over track before the main loop has processed
        the STREAM_START that confirms it. Asking twice must walk the queue
        forward, not hand the same track over again -- getting this wrong
        plays a track twice, which is exactly what a real pipeline run
        did before the hand-off cursor was tracked separately from the
        published queue position."""
        self.controller.play()
        first = self.engine.about_to_finish()
        self.assertEqual(first, (self.root / "music/1.flac").resolve().as_uri())
        # No stream_start() yet: the snapshot still says track 0.
        self.assertEqual(self.controller.snapshot.queue_position, 0)
        second = self.engine.about_to_finish()
        self.assertEqual(second, (self.root / "music/2.flac").resolve().as_uri())
        self.assertNotEqual(first, second)
        # Both hand-offs are in flight and commit in order.
        self.assertEqual([entry[0] for entry in self.controller._handed_over], [1, 2])
        self.engine.pending_uri = first
        self.assertTrue(self.engine.stream_start())
        self.assertEqual(self.controller.snapshot.queue_position, 1)
        self.assertEqual(self.controller.snapshot.track.title, "Track 1")
        self.engine.pending_uri = second
        self.assertTrue(self.engine.stream_start())
        self.assertEqual(self.controller.snapshot.queue_position, 2)
        self.assertEqual(self.controller.snapshot.track.title, "Track 2")

    def test_next_uri_during_advance_persistence_does_not_repeat_the_current_track(self) -> None:
        self.controller.play()
        self.engine.about_to_finish()
        original_update = self.store.update_session
        requests = []

        def update_with_streaming_request(**changes):
            if changes.get("current_position") == 1:
                # SQLite persistence releases the GIL: the streaming
                # thread can request its next URI before this returns.
                requests.append(self.controller._provide_next_uri())
            return original_update(**changes)

        self.store.update_session = update_with_streaming_request
        self.assertTrue(self.engine.stream_start())
        self.assertEqual(requests, [(self.root / "music/2.flac").resolve().as_uri()])
        self.assertEqual([entry[0] for entry in self.controller._handed_over], [2])
        self.assertEqual(self.controller.snapshot.queue_position, 1)
        self.assertEqual(self.store.session_state().current_position, 1)

    def test_about_to_finish_at_the_end_of_the_queue_hands_over_nothing(self) -> None:
        """Same race, at the end of the queue: the extra ask must answer
        None rather than looping back to a track that already played."""
        self.controller.play()
        self.assertIsNotNone(self.engine.about_to_finish())
        self.assertIsNotNone(self.engine.about_to_finish())
        self.assertIsNone(self.engine.about_to_finish())
        self.assertEqual([entry[0] for entry in self.controller._handed_over], [1, 2])

    def test_replace_queue_cancels_a_pending_gapless_hand_off(self) -> None:
        self.controller.play()
        self.engine.about_to_finish()
        self.assertIsNotNone(self.engine.pending_uri)
        self.controller.replace_queue([track.id for track in self.tracks], start=2, play=False)
        self.assertEqual(self.controller._handed_over, [])


if __name__ == "__main__":
    unittest.main()
