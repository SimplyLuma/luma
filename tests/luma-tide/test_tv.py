# SPDX-License-Identifier: Apache-2.0
"""Tide's TV presentation data: playlists, local history, playing a list (tv_runtime.py drives the screens)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from luma_tide.live import LiveLibrary
from luma_tide.model import LibraryStore, MediaMetadata
from luma_tide.playback import PlaybackController
from test_playback import FakeEngine


class TvDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LibraryStore(self.root / 'library.db')
        self.controller = PlaybackController(self.store, FakeEngine())
        self.source = LiveLibrary(SimpleNamespace(store=self.store, controller=self.controller))
        folder = self.store.add_source('Music', 'local-folder', self.root / 'music', local=True)
        self.tracks = []
        for album in ('A', 'B'):
            for number in (1, 2):
                track, _ = self.store.upsert_copy(folder.id, MediaMetadata(
                    uri=(self.root / f'{album}{number}.flac').as_uri(), title=f'{album}{number}', album=album,
                    artist='Artist', track_number=number, content_digest=f'{album}{number}'.lower() * 32))
                self.tracks.append(track)

    def tearDown(self):
        self.source.close()
        self.controller.close()
        self.store.close()
        self.temp.cleanup()

    def test_playlists_load_in_name_order_with_their_songs(self):
        late = self.store.create_playlist('Late')
        early = self.store.create_playlist('Early')
        self.store.set_playlist_tracks(late, [self.tracks[3].id, self.tracks[0].id])
        library = self.source.load()
        self.assertEqual([p.name for p in library.playlists], ['Early', 'Late'])
        self.assertEqual(library.playlists[1].song_ids, (self.tracks[3].id, self.tracks[0].id))
        self.assertEqual(library.playlists[0].song_ids, ())
        self.assertEqual(library.played, ())

    def test_played_follows_local_history_most_recent_first(self):
        library = self.source.load()
        first, second = library.albums[0].id, library.albums[1].id
        self.source._recency.record(first, 10)
        self.source._recency.record(second, 20)
        ordered = self.source.order_albums(library)
        self.assertEqual(ordered.played, (second, first))
        self.assertIs(self.source.order_albums(ordered), ordered)

    def test_play_ids_replaces_the_queue_from_a_song(self):
        ids = [self.tracks[2].id, self.tracks[0].id, self.tracks[1].id]
        self.source.play_ids(ids, 1)
        self.assertEqual([entry.track.id for entry in self.store.queue()], ids)
        self.assertEqual(self.controller.snapshot.queue_position, 1)
        self.source.play_ids([])                                     # nothing to play changes nothing
        self.assertEqual(len(self.store.queue()), 3)


if __name__ == '__main__':
    unittest.main()
