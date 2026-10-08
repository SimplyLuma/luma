# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_tide.fixture import FixtureLibrary
from luma_tide.presentation import duration_text, more_albums, next_queue, parse_duration, search_library, songs_matching
from luma_tide.presentation import Library

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/tide-v70.json"


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.source = FixtureLibrary(FIXTURE)
        self.library = self.source.load()

    def test_player_subject_can_disappear_during_source_refresh(self):
        self.assertEqual(self.library.find_song('pet:7'), self.library.song('pet:7'))
        self.assertIsNone(Library().find_song('pet:7'))
        self.assertIsNone(self.library.find_song(None))

    def test_v70_opening_data_and_missing_track_lists(self):
        self.assertEqual(len(self.library.albums), 12)
        self.assertEqual(len(self.library.artists), 11)
        self.assertEqual(len(songs_matching(self.library)), 42)
        album = self.library.album("pet")
        self.assertEqual((album.title, len(album.songs), album.minutes), ("Pet Sounds", 13, 37))
        self.assertEqual(self.library.album("blue").songs, ())
        self.assertEqual((self.source.player.song_id, self.source.player.position), ("pet:7", 64))

    def test_query_matches_artist_album_and_title_without_inventing_songs(self):
        self.assertEqual(len(songs_matching(self.library, "  BEACH BOYS  ")), 13)
        self.assertEqual(len(songs_matching(self.library, "Rumours")), 11)
        self.assertEqual([s.id for _, s in songs_matching(self.library, "God Only")], ["pet:7"])
        self.assertEqual(songs_matching(self.library, "does not exist"), [])

    def test_search_groups_artist_album_and_song_matches(self):
        artists, albums, songs = search_library(self.library, 'beach boys')
        self.assertEqual([name for name, _ in artists], ['The Beach Boys'])
        self.assertTrue(any(album.title == 'Pet Sounds' for album in albums))
        self.assertEqual(len(songs), 13)
        artists, albums, songs = search_library(self.library, 'god only')
        self.assertEqual((artists, albums), ([], []))
        self.assertEqual([song.id for _, song in songs], ['pet:7'])

    def test_sort_direction_and_numerical_duration(self):
        ascending = songs_matching(self.library, key="time")
        self.assertEqual(ascending[0][1].title, "I’ll Cry Instead")
        self.assertEqual(ascending[-1][1].title, "All Blues")
        descending = songs_matching(self.library, key="time", descending=True)
        self.assertEqual([song.duration for _, song in descending],
                         sorted((song.duration for _, song in ascending), reverse=True))
        self.assertEqual([s.id for _, s in descending if s.duration == 178], ["pet:3", "pet:6"])
        with self.assertRaises(ValueError):
            songs_matching(self.library, key="size")

    def test_recommendations_put_same_artist_first(self):
        more = more_albums(self.library, self.library.album("hdn"))
        self.assertEqual(more[0].id, "abbey")
        self.assertNotIn("hdn", {a.id for a in more})
        self.assertEqual(len(more), 7)

    def test_duration_formats_and_validates(self):
        self.assertEqual(duration_text(64), "1:04")
        self.assertEqual(duration_text(-5), "0:00")
        self.assertEqual(parse_duration("11:33"), 693)
        for text in ("1:60", "-1:00", "no"):
            with self.assertRaises(ValueError):
                parse_duration(text)

    def test_queue_begins_with_current_song_and_clear_keeps_it(self):
        self.assertEqual(len(next_queue(self.source.player)), 6)
        self.assertEqual(next_queue(self.source.player)[0], "pet:7")
        self.source.clear_queue()
        self.assertEqual(next_queue(self.source.player), ("pet:7",))

    def test_previous_restarts_then_moves_and_end_stops(self):
        self.source.step(-1)
        self.assertEqual((self.source.player.song_id, self.source.player.position), ("pet:7", 0))
        self.source.step(-1)
        self.assertEqual(self.source.player.song_id, "pet:6")
        for _ in range(7):
            self.source.step(1)
        self.assertFalse(self.source.player.playing)
        self.source.repeat()
        self.source.step(1)
        self.assertEqual(self.source.player.song_id, "pet:0")

    def test_fixture_mutations_write_nothing_and_never_open_the_real_store(self):
        before = FIXTURE.read_bytes()
        with patch("luma_tide.model.LibraryStore", side_effect=AssertionError("real store opened")), \
             patch("luma_tide.credentials.SecretServiceStore", side_effect=AssertionError("keyring opened")):
            source = FixtureLibrary(FIXTURE)
            source.love("pet:7")
            source.sign_out("nd")
            self.assertEqual(source.load().source("nd").subtitle, "Signed out")
            source.connect("music.example.com", "sample", "not-persisted", "nd")
            self.assertNotIn("not-persisted", repr(source.__dict__))
            source.remove_source("nd")
            self.assertEqual(len(source.load().sources), 2)
        self.assertEqual(FIXTURE.read_bytes(), before)

    def test_controls_are_bounded_and_output_must_exist(self):
        self.source.seek(9999)
        self.assertEqual(self.source.player.position, 171)
        self.source.set_volume(-50)
        self.assertEqual(self.source.player.volume, 0)
        self.source.set_volume(900)
        self.assertEqual(self.source.player.volume, 100)
        with self.assertRaises(ValueError):
            self.source.output("Made up device")

    def test_invalid_fixture_fails_instead_of_loading_real_data(self):
        raw = json.loads(FIXTURE.read_text())
        raw["albums"] = []
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty.json"
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                FixtureLibrary(path)


if __name__ == "__main__":
    unittest.main()
