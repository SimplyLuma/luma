# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

from luma_tide.credentials import MemoryCredentialStore
from luma_tide.model import LibraryStore, MediaMetadata, OfflineState, SourceState
from luma_tide.playback import PlaybackController, PlaybackState, PlaybackUnavailable
from luma_tide.model import Source
from luma_tide.remote import PLAINTEXT_CAPABILITY, RemoteSources, accepts_plaintext, song_id_from_uri
from luma_tide.subsonic import AuthenticationFailed, Cancelled, Unreachable
from subsonic_fixture import FakeSubsonic, song
from test_playback import FakeEngine

PASSWORD = "correct horse"


class RemoteSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.server = FakeSubsonic(password=PASSWORD, songs=12)
        self.store = LibraryStore(self.root / "library.db")
        self.keyring = MemoryCredentialStore()
        self.remote = RemoteSources(
            self.store, lambda: self.keyring,
            offline_root=self.root / "offline", artwork_root=self.root / "artwork",
        )

    def tearDown(self) -> None:
        self.remote.close()
        self.server.close()
        self.store.close()
        self.temporary.cleanup()

    def add(self, password: str = PASSWORD):
        return self.remote.add_server(
            self.server.address, "listener", password, allow_plaintext=True
        ).result(10)

    def wait_for(self, condition, timeout: float = 10) -> None:
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if condition():
                return
            time.sleep(0.02)
        self.fail("condition was not reached")

    def test_adding_a_server_keeps_the_password_only_in_the_keyring(self) -> None:
        source = self.add()
        self.assertEqual((source.kind, source.name, source.account), ("subsonic", "Navidrome", "listener"))
        self.assertEqual(source.uri, self.server.address)
        self.assertIn(PLAINTEXT_CAPABILITY, source.capabilities)
        legacy = Source(**{**{name: getattr(source, name) for name in Source.__slots__},
                           "capabilities": ("browse", "unencrypted-private-network")})
        self.assertTrue(accepts_plaintext(legacy), "a preview-era plain-HTTP source still connects")
        self.assertEqual(self.keyring.lookup(source.auth_ref), PASSWORD)
        self.remote.sync(source.id).result(10)
        self.store.close()
        for path in self.root.glob("library.db*"):
            self.assertNotIn(PASSWORD.encode(), path.read_bytes(), path.name)
        self.store = LibraryStore(self.root / "library.db")

    def test_sign_out_preserves_downloads_and_can_be_undone(self):
        source = self.add()
        self.remote.sync(source.id).result(10)
        track = self.store.tracks()[0]
        self.remote.request_offline([track.id])
        self.wait_for(lambda: self.store.offline_summary(source.id).complete == 1)
        before = {p: p.read_bytes() for p in (self.root / 'offline').rglob('*') if p.is_file()}
        token = self.remote.sign_out(source.id).result(10)
        self.assertFalse(self.remote.connected(source.id))
        self.assertIsNone(self.keyring.lookup(source.auth_ref))
        self.assertEqual(self.store.source(source.id).state, SourceState.AUTH_REQUIRED)
        self.assertEqual(before, {p: p.read_bytes() for p in (self.root / 'offline').rglob('*') if p.is_file()})
        self.assertTrue(self.remote.resolve(self.store.select_copy(track.id)).startswith('file://'))
        backups = list((self.root / 'backups').glob('sign-out-*.db'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertNotIn(PASSWORD.encode(), backups[0].read_bytes())
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertNotIn(PASSWORD, repr(token))
        self.remote.undo_sign_out(token).result(10)
        self.assertEqual(self.keyring.lookup(source.auth_ref), PASSWORD)
        self.assertTrue(self.remote.connected(source.id))
        self.assertEqual(self.store.source(source.id).state, source.state)

    def test_sign_out_backup_failure_changes_nothing(self):
        source = self.add()
        (self.root / 'backups').write_text('not a directory')
        with self.assertRaises(OSError):
            self.remote.sign_out(source.id).result(10)
        self.assertEqual(self.keyring.lookup(source.auth_ref), PASSWORD)
        self.assertTrue(self.remote.connected(source.id))
        self.assertEqual(self.store.source(source.id).state, source.state)

    def test_a_rejected_account_stores_nothing(self) -> None:
        with self.assertRaises(AuthenticationFailed):
            self.add("wrong")
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(self.keyring.secrets, {})

    def test_sync_indexes_songs_with_source_scoped_identity_and_artwork(self) -> None:
        source = self.add()
        result = self.remote.sync(source.id).result(10)
        self.assertEqual((result.songs, result.missing, result.unchanged, result.artwork_failed), (12, 0, False, 0))
        tracks = self.store.tracks()
        self.assertEqual(len(tracks), 12)
        copy = self.store.select_copy(tracks[0].id)
        self.assertFalse(copy.source_local)
        self.assertEqual(copy.bitrate, 900_000)
        self.assertEqual(tracks[0].duration_ns, 180_000_000_000)
        self.assertNotIn("t=", copy.uri)
        artwork = Path(urllib.parse.unquote(urllib.parse.urlsplit(copy.artwork_uri).path))
        self.assertTrue(artwork.is_file())
        images = [path for path in (self.root / "artwork" / source.id).iterdir() if path.suffix == ".image"]
        self.assertEqual(len(images), 2, "one cover per album, not one per song")
        self.assertEqual(self.server.artwork_requests, 2)
        album = self.store.tracks(album="Album 0")
        self.assertEqual(len({self.store.select_copy(track.id).artwork_uri for track in album}), 1)

    def test_a_busy_server_gets_no_artwork_queue_and_covers_arrive_later(self) -> None:
        self.server.songs = [song(index) for index in range(60)]
        self.server.scan["count"] = 60
        self.server.artwork_status = 429
        source = self.add()
        result = self.remote.sync(source.id).result(10)
        self.assertEqual((result.songs, result.artwork_failed), (60, 6))
        self.assertLessEqual(self.server.artwork_requests, 2, "stops at the first throttled answer")
        self.assertIsNotNone(self.store.source(source.id).sync_marker, "songs are synced regardless")
        requests = self.server.artwork_requests
        self.assertEqual(self.remote.sync(source.id).result(10).artwork_failed, 6)
        self.assertEqual(self.server.artwork_requests, requests, "backs off instead of retrying at once")
        self.server.artwork_status = 200
        self.remote._artwork_resume_at.clear()
        result = self.remote.sync(source.id).result(10)
        self.assertTrue(result.unchanged)
        self.assertEqual(result.artwork_failed, 0)
        self.assertEqual(len(list((self.root / "artwork" / source.id).glob("*.image"))), 6)

    def test_an_unchanged_server_library_skips_a_full_sync(self) -> None:
        source = self.add()
        self.remote.sync(source.id).result(10)
        searches = sum("search3" in path for path in self.server.requests)
        self.assertTrue(self.remote.sync(source.id).result(10).unchanged)
        self.assertEqual(sum("search3" in path for path in self.server.requests), searches)
        (self.root / "artwork" / source.id / "covers.json").unlink()
        self.assertFalse(self.remote.sync(source.id).result(10).unchanged, "a cleared cache reads the library again")
        self.server.songs.pop()
        self.server.scan.update(count=11, lastScan="2026-09-02T10:00:00Z")
        result = self.remote.sync(source.id).result(10)
        self.assertEqual((result.songs, result.missing), (11, 1))

    def test_a_recording_id_merges_a_server_song_with_a_local_file(self) -> None:
        local = self.store.add_source("Here", "local-folder", self.root / "music", local=True)
        track, _copy = self.store.upsert_copy(local.id, MediaMetadata(
            uri=(self.root / "music/one.flac").as_uri(), content_digest="1" * 64,
            title="Song 0", recording_id="mbid-0",
        ))
        self.server.songs[0]["musicBrainzId"] = "mbid-0"
        source = self.add()
        self.remote.sync(source.id).result(10)
        self.assertEqual(len(self.store.copies_for_track(track.id)), 2)
        self.assertEqual(len(self.store.tracks()), 12)

    def test_playback_resolves_an_authenticated_stream_only_when_connected(self) -> None:
        source = self.add()
        self.remote.sync(source.id).result(10)
        copy = self.store.select_copy(self.store.tracks()[0].id)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.remote.resolve(copy)).query)
        self.assertEqual((query["id"], query["format"]), ([song_id_from_uri(copy.uri)], ["raw"]))
        self.assertIn("t", query)
        self.remote.mark_offline(source.id)
        self.assertIsNone(self.store.select_copy(copy.track_id))
        with self.assertRaisesRegex(PlaybackUnavailable, "out of reach"):
            self.remote.resolve(copy)

    def test_restart_reconnects_from_the_keyring(self) -> None:
        source = self.add()
        self.remote.close()
        self.remote = RemoteSources(
            self.store, lambda: self.keyring,
            offline_root=self.root / "offline", artwork_root=self.root / "artwork",
        )
        self.assertFalse(self.remote.connected(source.id))
        self.assertIs(self.remote.connect(source.id).result(10), SourceState.ONLINE)
        self.assertTrue(self.remote.connected(source.id))
        self.keyring.clear(source.auth_ref)
        self.remote.mark_offline(source.id)
        with self.assertRaises(AuthenticationFailed):
            self.remote.connect(source.id).result(10)
        self.assertIs(self.store.source(source.id).state, SourceState.AUTH_REQUIRED)

    def test_an_unreachable_server_is_out_of_reach_not_broken(self) -> None:
        source = self.add()
        self.remote.mark_offline(source.id)
        self.server.close()
        with self.assertRaises(Unreachable):
            self.remote.sync(source.id).result(10)
        self.assertIs(self.store.source(source.id).state, SourceState.OFFLINE)

    def test_offline_download_plays_without_the_server_and_can_be_removed(self) -> None:
        source = self.add()
        self.remote.sync(source.id).result(10)
        album = [track.id for track in self.store.tracks(album="Album 0")]
        self.assertEqual(self.remote.request_offline(album), 10)
        self.wait_for(lambda: self.store.offline_summary(source.id).complete == 10)
        self.assertEqual(self.store.offline_summary(source.id).bytes, 10 * 2048)
        self.assertEqual(self.remote.request_offline(album), 0, "already downloaded")

        self.remote.mark_offline(source.id)
        copy = self.store.select_copy(album[0])
        self.assertTrue(copy.offline_ready)
        uri = self.remote.resolve(copy)
        self.assertTrue(uri.startswith("file://"))
        controller = PlaybackController(self.store, FakeEngine(), self.remote.resolve)
        controller.replace_queue(album)
        self.assertEqual(controller.snapshot.state, PlaybackState.PLAYING)
        self.assertEqual(controller.engine.loaded[-1][0], uri)
        controller.close()

        files = list((self.root / "offline" / source.id).iterdir())
        self.assertEqual(self.remote.remove_offline(album[:4]), 4)
        self.assertEqual(len(list((self.root / "offline" / source.id).iterdir())), len(files) - 4)
        self.assertIsNone(self.store.select_copy(album[0]))

    def test_a_download_lost_to_the_network_waits_for_the_server(self) -> None:
        source = self.add()
        self.remote.sync(source.id).result(10)
        track = self.store.tracks()[0]
        self.server.truncate_download = True
        self.remote.request_offline([track.id])
        self.wait_for(lambda: not self.remote._downloading)
        [item] = self.store.offline_items()
        self.assertIs(item.state, OfflineState.QUEUED)
        self.server.truncate_download = False
        self.assertEqual(self.remote.pump_downloads(), 1)
        self.wait_for(lambda: self.store.offline_summary().complete == 1)

    def test_a_permanent_download_failure_is_reported_and_can_be_retried(self) -> None:
        source = self.add()
        self.remote.sync(source.id).result(10)
        track = self.store.tracks()[0]
        self.server.songs = self.server.songs[1:]  # gone from the server since the sync
        self.remote.request_offline([track.id])
        self.wait_for(lambda: self.store.offline_summary().failed == 1)
        self.assertIn("couldn’t find", self.store.offline_items()[0].error)
        self.assertEqual(self.store.request_offline([self.store.offline_items()[0].copy_id]), 1)

    def test_removing_a_server_forgets_everything_tide_kept_for_it(self) -> None:
        source = self.add()
        self.remote.sync(source.id).result(10)
        tracks = self.store.tracks()
        kept = self.store.create_playlist("Kept")
        self.store.set_playlist_tracks(kept, [tracks[0].id])
        self.remote.request_offline([tracks[1].id, tracks[2].id])
        self.wait_for(lambda: self.store.offline_summary().complete == 2)
        self.assertEqual(self.remote.remove_source(source.id).result(10), 2)
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(self.keyring.secrets, {})
        self.assertFalse((self.root / "offline" / source.id).exists())
        self.assertFalse((self.root / "artwork" / source.id).exists())
        self.assertEqual([track.id for track in self.store.tracks()], [tracks[0].id])
        self.assertEqual([track.id for track in self.store.playlist_tracks(kept)], [tracks[0].id])

    def test_removing_a_server_cancels_its_sync_instead_of_waiting(self) -> None:
        source = self.add()
        self.server.hold_search = threading.Event()
        syncing = self.remote.sync(source.id)
        self.assertTrue(self.server.search_started.wait(10))
        removing = self.remote.remove_source(source.id)
        self.server.hold_search.set()
        with self.assertRaises(Cancelled):
            syncing.result(10)
        removing.result(10)
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(self.store.tracks(), [])
        self.assertEqual(self.keyring.secrets, {})

    def test_a_version_one_library_migrates_in_place(self) -> None:
        path = self.root / "old.db"
        connection = sqlite3.connect(path, isolation_level=None)
        LibraryStore._create_schema_1(connection)
        connection.execute(
            "INSERT INTO sources(id,name,kind,uri,local,state,created_at) VALUES('s','Old','local-folder','file:///m',1,'online',0)"
        )
        connection.close()
        with LibraryStore(path) as store:
            [source] = store.sources()
            self.assertEqual((source.name, source.account, source.sync_marker), ("Old", None, None))
            self.assertEqual(store.offline_summary().complete, 0)
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)


class ResolverPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = LibraryStore(root / "library.db")
        source = self.store.add_source("Server", "subsonic", "https://music.example", local=False)
        self.tracks = [
            self.store.upsert_copy(source.id, MediaMetadata(
                uri=f"https://music.example/rest/stream?id={index}", content_digest="",
                title=f"Track {index}", source_item_id=str(index), duration_ns=1,
            ))[0]
            for index in range(3)
        ]
        self.engine = FakeEngine()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_an_unresolvable_copy_is_skipped_and_playback_continues(self) -> None:
        def resolve(copy):
            if copy.uri.endswith("id=0"):
                raise PlaybackUnavailable("Sign in to Server again to play this song.")
            return copy.uri + "&t=secret"

        controller = PlaybackController(self.store, self.engine, resolve)
        controller.replace_queue([track.id for track in self.tracks])
        self.assertEqual(controller.snapshot.track.title, "Track 1")
        self.assertEqual(controller.snapshot.state, PlaybackState.PLAYING)
        self.assertEqual(self.engine.loaded[-1][0], "https://music.example/rest/stream?id=1&t=secret")
        controller.close()

    def test_engine_errors_never_publish_stream_credentials(self) -> None:
        controller = PlaybackController(self.store, self.engine, lambda copy: copy.uri + "&t=secret")
        controller.replace_queue([self.tracks[0].id])
        self.engine.handler("error", "Not Found (404), URL: https://music.example/rest/stream?id=0&t=secret")
        self.assertEqual(controller.snapshot.error, "Not Found (404), URL: https://music.example/rest/stream")
        controller.close()


if __name__ == "__main__":
    unittest.main()
