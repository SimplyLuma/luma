# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luma_tide.model import (
    CopyAvailability,
    LibraryStore,
    MediaMetadata,
    RepeatMode,
    SourceState,
)


def metadata(uri: str, digest: str, title: str = "Low Tide") -> MediaMetadata:
    return MediaMetadata(
        uri=uri,
        content_digest=digest,
        title=title,
        artist="Fable Radio",
        album="Low Tide",
        album_artist="Fable Radio",
        duration_ns=214_000_000_000,
        track_number=1,
        format="FLAC",
        sample_rate=96_000,
    )


class LibraryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = LibraryStore(self.root / "library.db")
        self.local = self.store.add_source(
            "This device", "local-folder", self.root / "music", local=True
        )
        self.remote = self.store.add_source(
            "Navidrome", "subsonic", "https://music.example.test", local=False
        )

    def test_missing_title_decodes_filename_once_and_preserves_identity(self) -> None:
        uri = (self.root / "music/6 Money %20.mp3").as_uri()
        track, copy_id = self.store.upsert_copy(self.local.id, metadata(uri, "fallback", ""))
        self.assertEqual(track.title, "6 Money %20")
        repaired, repaired_copy = self.store.upsert_copy(self.local.id, metadata(uri, "fallback", "6 Money %20"))
        self.assertEqual((repaired.id, repaired_copy), (track.id, copy_id))
        self.assertEqual(self.store.tracks(query="Money")[0].title, "6 Money %20")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_exact_duplicates_become_distinct_copies_of_one_track(self) -> None:
        track, first = self.store.upsert_copy(
            self.local.id, metadata((self.root / "music/low-tide.flac").as_uri(), "a" * 64)
        )
        same, second = self.store.upsert_copy(
            self.remote.id,
            metadata("https://music.example.test/rest/stream?id=10", "a" * 64),
        )
        self.assertEqual(track.id, same.id)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.store.copies_for_track(track.id)), 2)
        self.assertTrue(self.store.select_copy(track.id).source_local)

    def test_matching_titles_do_not_merge_different_recordings(self) -> None:
        first, _ = self.store.upsert_copy(
            self.local.id, metadata((self.root / "music/a.flac").as_uri(), "a" * 64)
        )
        second, _ = self.store.upsert_copy(
            self.local.id, metadata((self.root / "music/b.flac").as_uri(), "b" * 64)
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(self.store.tracks()), 2)

    def test_unavailable_copy_is_retained_and_remote_fallback_is_selected(self) -> None:
        track, local_copy = self.store.upsert_copy(
            self.local.id, metadata((self.root / "music/a.flac").as_uri(), "a" * 64)
        )
        _track, remote_copy = self.store.upsert_copy(
            self.remote.id, metadata("https://music.example.test/stream/1", "a" * 64)
        )
        missing = self.store.finish_source_scan(self.local.id, set())
        self.assertEqual(missing, 1)
        selected = self.store.select_copy(track.id)
        self.assertEqual(selected.id, remote_copy)
        copies = self.store.copies_for_track(track.id)
        self.assertEqual(
            next(item for item in copies if item.id == local_copy).availability,
            CopyAvailability.UNAVAILABLE,
        )

    def test_explicit_offline_source_is_rejected_without_losing_copy(self) -> None:
        track, _local_copy = self.store.upsert_copy(
            self.local.id, metadata((self.root / "music/a.flac").as_uri(), "a" * 64)
        )
        _track, remote_copy = self.store.upsert_copy(
            self.remote.id, metadata("https://music.example.test/stream/1", "a" * 64)
        )
        self.store.set_source_state(self.remote.id, SourceState.OFFLINE)
        selected = self.store.select_copy(track.id, remote_copy)
        self.assertTrue(selected.source_local)
        self.assertEqual(len(self.store.copies_for_track(track.id)), 2)

    def test_playlist_order_and_queue_session_are_durable(self) -> None:
        tracks = []
        for index in range(3):
            track, _copy = self.store.upsert_copy(
                self.local.id,
                metadata(
                    (self.root / f"music/{index}.flac").as_uri(),
                    f"{index}" * 64,
                    title=f"Track {index}",
                ),
            )
            tracks.append(track)
        playlist = self.store.create_playlist("Late shift")
        self.store.set_playlist_tracks(playlist, [track.id for track in tracks])
        self.store.move_playlist_item(playlist, 2, 0)
        self.assertEqual(
            [track.title for track in self.store.playlist_tracks(playlist)],
            ["Track 2", "Track 0", "Track 1"],
        )
        self.store.replace_queue([track.id for track in tracks], current_position=1)
        self.store.update_session(
            position_ns=19_000_000_000,
            shuffle=True,
            repeat=RepeatMode.ALL,
            volume=0.4,
            playback_state="paused",
        )
        self.store.close()
        self.store = LibraryStore(self.root / "library.db")
        state = self.store.session_state()
        self.assertEqual(state.current_position, 1)
        self.assertEqual(state.position_ns, 19_000_000_000)
        self.assertTrue(state.shuffle)
        self.assertEqual(state.repeat, RepeatMode.ALL)
        self.assertEqual([entry.track.title for entry in self.store.queue()], ["Track 0", "Track 1", "Track 2"])

    def test_hidden_sources_filter_the_library_without_removing_anything(self) -> None:
        here, _ = self.store.upsert_copy(self.local.id, metadata((self.root / "music/a.flac").as_uri(), "a" * 64, "Here"))
        both, _ = self.store.upsert_copy(self.local.id, metadata((self.root / "music/b.flac").as_uri(), "b" * 64, "Both"))
        self.store.upsert_copy(self.remote.id, metadata("https://music.example.test/rest/stream?id=b", "b" * 64, "Both"))
        remote = MediaMetadata(uri="https://music.example.test/rest/stream?id=c", content_digest="",
                               source_item_id="c", title="Server only", album="Elsewhere", album_artist="Server Band")
        server, _ = self.store.upsert_copy(self.remote.id, remote)

        def titles() -> list[str]:
            return sorted(track.title for track in self.store.tracks())

        self.assertEqual(titles(), ["Both", "Here", "Server only"])
        self.store.show_only_source(self.remote.id)
        self.assertEqual(titles(), ["Both", "Server only"])
        self.assertEqual(self.store.library_counts(), {"songs": 2, "albums": 2, "artists": 2})
        self.assertEqual([artist for artist, _count in self.store.artists()], ["Fable Radio", "Server Band"])
        self.assertEqual({album for album, *_ in self.store.albums()}, {"Low Tide", "Elsewhere"})
        self.assertFalse(self.store.source(self.local.id).shown)
        self.store.set_source_shown(self.remote.id, False)
        self.assertEqual(titles(), [])
        self.assertEqual(self.store.library_counts(shown_only=False)["songs"], 3, "hiding removes nothing")
        self.assertIsNotNone(self.store.select_copy(here.id), "a hidden source's songs still play from the queue")
        self.store.set_source_shown(self.local.id, True)
        self.assertEqual(titles(), ["Both", "Here"])
        self.store.show_all_sources()
        self.assertEqual(titles(), ["Both", "Here", "Server only"])

    def test_search_groups_albums_artists_and_songs_best_match_first(self) -> None:
        def song(name: str, title: str, album: str, artist: str) -> MediaMetadata:
            return MediaMetadata(uri=(self.root / f"music/{name}.flac").as_uri(), content_digest=name * 64,
                                 title=title, album=album, artist=artist, album_artist=artist)

        self.store.upsert_copy(self.local.id, song("a", "Tide Pool", "Harbour Lights", "Moon Tide"))
        self.store.upsert_copy(self.local.id, song("b", "Low Tide", "Night Swim", "The Swells"))
        self.store.upsert_copy(self.local.id, song("c", "Undertow", "Tidewater", "Ocean Choir"))
        self.store.upsert_copy(self.local.id, song("d", "Morning", "Daybreak", "Sunrise"))
        results = self.store.search("tide")
        self.assertEqual([album for album, *_ in results.albums], ["Tidewater", "Harbour Lights"])
        self.assertEqual([artist for artist, _count in results.artists], ["Moon Tide"])
        self.assertEqual([track.title for track in results.songs][:2], ["Tide Pool", "Low Tide"])
        self.assertEqual({track.title for track in results.songs}, {"Tide Pool", "Low Tide", "Undertow"})
        self.assertEqual(self.store.search("  ").songs, [])

    def test_remove_source_never_deletes_media(self) -> None:
        media = self.root / "music/a.flac"
        media.parent.mkdir()
        media.write_bytes(b"not actually flac")
        self.store.upsert_copy(self.local.id, metadata(media.as_uri(), "a" * 64))
        self.store.remove_source(self.local.id)
        self.assertTrue(media.exists())

    def test_structured_artists_are_stored_in_order_and_read_back(self) -> None:
        track, _copy = self.store.upsert_copy(
            self.local.id,
            MediaMetadata(
                uri=(self.root / "music/a.flac").as_uri(),
                content_digest="a" * 64,
                title="Low Tide",
                artist="Artist A",
                album_artist="Various Artists",
                artists=("Artist A", "Artist B"),
                album_artists=("Various Artists", "Guest Curator"),
            ),
        )
        self.assertEqual(track.artists, ("Artist A", "Artist B"))
        self.assertEqual(track.album_artists, ("Various Artists", "Guest Curator"))
        self.assertEqual(self.store.artists_for_track(track.id), ["Artist A", "Artist B"])
        self.assertEqual(
            self.store.album_artists_for_track(track.id), ["Various Artists", "Guest Curator"]
        )
        # Re-fetched through every listing path, not just track().
        self.assertEqual(self.store.tracks()[0].artists, ("Artist A", "Artist B"))
        playlist = self.store.create_playlist("Compilation")
        self.store.set_playlist_tracks(playlist, [track.id])
        self.assertEqual(self.store.playlist_tracks(playlist)[0].artists, ("Artist A", "Artist B"))
        self.store.replace_queue([track.id])
        self.assertEqual(self.store.queue()[0].track.artists, ("Artist A", "Artist B"))

    def test_a_track_with_no_structural_artist_list_falls_back_to_the_single_display_artist(
        self,
    ) -> None:
        """A source that only ever had one artist (no `artists=` given) is
        not treated as a bug: the join tables still get exactly one row,
        matching the display column — never an empty list."""
        track, _copy = self.store.upsert_copy(
            self.local.id, metadata((self.root / "music/a.flac").as_uri(), "a" * 64)
        )
        self.assertEqual(track.artists, ("Fable Radio",))
        self.assertEqual(track.album_artists, ("Fable Radio",))
        self.assertEqual(self.store.artists_for_track(track.id), ["Fable Radio"])

    def test_re_upserting_a_track_replaces_its_structured_artist_list(self) -> None:
        """A re-scan (e.g. the schema v3 automatic re-index) must overwrite
        the join-table rows, not accumulate duplicates alongside them."""
        uri = (self.root / "music/a.flac").as_uri()
        first, _copy = self.store.upsert_copy(
            self.local.id,
            MediaMetadata(
                uri=uri, content_digest="a" * 64, title="Low Tide", artist="Artist A",
                artists=("Artist A",),
            ),
        )
        self.assertEqual(first.artists, ("Artist A",))
        second, _copy = self.store.upsert_copy(
            self.local.id,
            MediaMetadata(
                uri=uri, content_digest="a" * 64, title="Low Tide", artist="Artist A",
                artists=("Artist A", "Artist B"),
            ),
        )
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.artists, ("Artist A", "Artist B"))
        self.assertEqual(self.store.artists_for_track(second.id), ["Artist A", "Artist B"])


if __name__ == "__main__":
    unittest.main()

