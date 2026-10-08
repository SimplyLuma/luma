# SPDX-License-Identifier: Apache-2.0
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from luma_tide.live import LiveLibrary
from luma_tide.model import LibraryStore, MediaMetadata, SourceState
from luma_tide.playback import PlaybackController
from test_playback import FakeEngine

class LivePresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LibraryStore(self.root / 'library.db')
        self.controller = PlaybackController(self.store, FakeEngine())
        self.source = LiveLibrary(SimpleNamespace(store=self.store, controller=self.controller))
    def tearDown(self):
        self.source.close()
        self.controller.close()
        self.store.close()
        self.temp.cleanup()
    def test_sign_out_hides_remote_songs_but_keeps_a_local_copy(self):
        remote = self.store.add_source('Server', 'subsonic', 'https://music.example', local=False)
        local = self.store.add_source('Music', 'local-folder', self.root / 'music', local=True)
        both, _ = self.store.upsert_copy(remote.id, MediaMetadata(uri='https://music.example/rest/stream?id=1',
            content_digest='a' * 64, title='Available here', album='A', artist='Artist', recording_id='shared'))
        hidden, _ = self.store.upsert_copy(remote.id, MediaMetadata(uri='https://music.example/rest/stream?id=2',
            content_digest='b' * 64, title='Server only', album='A', artist='Artist', recording_id='remote-only'))
        self.store.upsert_copy(local.id, MediaMetadata(uri=(self.root / 'one.flac').as_uri(),
            content_digest='a' * 64, title='Available here', album='A', artist='Artist', recording_id='shared'))
        library = self.source.load()
        self.assertEqual({s.id for a in library.albums for s in a.songs}, {both.id, hidden.id})
        self.assertEqual(set(library.song(both.id)[1].library_sources), {'Server', 'Music'})
        self.assertEqual(library.song(hidden.id)[1].library_sources, ('Server',))
        self.store.set_source_state(remote.id, SourceState.AUTH_REQUIRED)
        library = self.source.load()
        self.assertEqual({s.id for a in library.albums for s in a.songs}, {both.id})
        self.assertEqual(library.source(remote.id).subtitle, 'Signed out')
    def test_only_the_real_system_output_is_advertised(self):
        self.assertEqual(self.source.player.outputs, (('This computer', 'monitor', 'System audio output'),))
        with self.assertRaises(ValueError):
            self.source.output('Kitchen')

    def test_edit_uses_the_saved_server_scheme_and_path_without_changing_it(self):
        address = 'http://music.example.test:4533/subsonic'
        remote = self.store.add_source('Server', 'subsonic', address, local=False)
        before = self.store.source(remote.id)
        self.assertEqual(self.source.source_address(remote.id), address)
        self.assertEqual(self.store.source(remote.id), before)

    def test_playback_records_real_album_order_without_library_changes(self):
        source = self.store.add_source('Music', 'local-folder', self.root / 'music', local=True)
        tracks = []
        for title in ('A', 'Z'):
            track, _ = self.store.upsert_copy(source.id, MediaMetadata(
                uri=(self.root / (title + '.flac')).as_uri(), title=title,
                album=title, artist='Artist', content_digest=title.lower() * 64))
            tracks.append(track)
        library = self.source.load()
        self.assertEqual([a.title for a in library.albums], ['A', 'Z'])
        stop = self.source.subscribe(lambda _player: None)
        self.controller.replace_queue([tracks[1].id], play=True)
        self.source._history_worker.shutdown(wait=True)
        self.assertEqual([a.title for a in self.source.order_albums(library).albums], ['Z', 'A'])
        self.assertEqual(len(self.store.tracks()), 2)
        self.assertTrue((self.root / 'album-history.json').is_file())
        stop()

    def test_closing_while_history_is_writing_suppresses_late_callbacks(self):
        source = self.store.add_source('Music', 'local-folder', self.root / 'music', local=True)
        track, _ = self.store.upsert_copy(source.id, MediaMetadata(
            uri=(self.root / 'a.flac').as_uri(), title='A', album='A',
            artist='Artist', content_digest='a' * 64))
        started, release = threading.Event(), threading.Event()
        calls = []
        def delayed(_identifier):
            started.set()
            release.wait(2)
        with patch.object(self.source._recency, 'record', side_effect=delayed):
            self.source.subscribe(calls.append)
            try:
                self.controller.replace_queue([track.id], play=True)
                self.assertTrue(started.wait(1))
                self.source.close()
                count = len(calls)
            finally:
                release.set()
                self.source._history_worker.shutdown(wait=True)
        self.assertEqual(len(calls), count)
