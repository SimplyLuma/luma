# SPDX-License-Identifier: Apache-2.0
"""An artist's releases, newest first, their top songs, and names a source
left as a placeholder."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luma_tide.model import LibraryStore, MediaMetadata


class ArtistPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = LibraryStore(root / "library.db")
        self.source = self.store.add_source("Music", "local-folder", root, local=True)
        self.root = root
        self.n = 0

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def add(self, **fields) -> str:
        self.n += 1
        fields.setdefault("title", f"Song {self.n}")
        track, _copy = self.store.upsert_copy(self.source.id, MediaMetadata(
            uri=(self.root / f"{self.n}.flac").as_uri(), content_digest=f"{self.n:064d}", **fields))
        return track.id

    def test_an_artist_s_releases_are_newest_first_including_features(self) -> None:
        for year, album in ((2015, "Early"), (2021, "Late")):
            self.add(artist="Harbor Lights", album=album, album_artist="Harbor Lights", year=year)
        self.add(artist="North Choir", artists=("North Choir", "Harbor Lights"), album="Guests",
                 album_artist="North Choir", year=2018)
        self.add(artist="North Choir", album="Unrelated", album_artist="North Choir", year=2024)
        albums = [album for album, *_rest in self.store.albums(artist="Harbor Lights")]
        self.assertEqual(albums, ["Late", "Guests", "Early"])

    def test_top_songs_are_the_most_played(self) -> None:
        quiet = self.add(artist="Harbor Lights", album="A", year=2020)
        loud = self.add(artist="Harbor Lights", album="A", year=2019)
        for _ in range(3):
            self.store.increment_play_count(loud)
        self.assertEqual([track.id for track in self.store.top_tracks("Harbor Lights")], [loud, quiet])

    def test_placeholder_names_read_as_unknown_in_sentence_case(self) -> None:
        track_id = self.add(artist="[Unknown Artist]", album="[Unknown Album]", album_artist="[Unknown Artist]")
        track = self.store.track(track_id)
        self.assertEqual((track.artist, track.album, track.album_artist),
                         ("Unknown artist", "Unknown album", "Unknown artist"))
        self.assertEqual(track.artists, ("Unknown artist",))


if __name__ == "__main__":
    unittest.main()
