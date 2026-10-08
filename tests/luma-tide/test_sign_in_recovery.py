# SPDX-License-Identifier: Apache-2.0
"""A server source can always be signed in to again, and a retired preview
build's library, history and sign-in reference come along once.

All servers, accounts and paths here are placeholders made up for the test.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path

from luma_tide.credentials import MemoryCredentialStore, reference_for
from luma_tide.model import LibraryStore, MediaMetadata, SourceState
from luma_tide.preview_import import find_preview_homes, import_preview_library
from luma_tide.remote import KIND, RemoteSources
from luma_tide.subsonic import AuthenticationFailed
from subsonic_fixture import FakeSubsonic

PASSWORD = "correct horse"
REMOTE_ID = "11111111-2222-5333-8444-555555555555"
LOCAL_ID = "66666666-7777-5888-9999-000000000000"

# What the preview build's own migrations produced on top of schema 1.
_PREVIEW_STEPS = """
BEGIN IMMEDIATE;
ALTER TABLE sources ADD COLUMN account TEXT;
ALTER TABLE sources ADD COLUMN sync_marker TEXT;
CREATE TABLE offline_items (
  copy_id TEXT PRIMARY KEY REFERENCES copies(id) ON DELETE CASCADE,
  state TEXT NOT NULL CHECK (state IN ('queued','downloading','complete','failed')),
  path TEXT, size INTEGER NOT NULL DEFAULT 0, error TEXT,
  requested_at INTEGER NOT NULL, completed_at INTEGER
);
ALTER TABLE sources ADD COLUMN shown INTEGER NOT NULL DEFAULT 1 CHECK (shown IN (0, 1));
PRAGMA user_version = 3;
COMMIT;
"""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PreviewFixture:
    """A preview checkout: its library, cached server artwork, and the
    launcher a retired D-Bus override pointed at."""

    def __init__(self, root: Path, server_address: str = "http://music.invalid:4533", account: str = "listener") -> None:
        self.home = root / "home"
        self.state_home = self.home / ".local/state"
        self.data_home = self.home / ".local/share"
        self.cache_home = self.home / ".cache"
        checkout = self.data_home / "luma-dev/tide-navidrome"
        self.preview_data = checkout / "data"
        self.preview_cache = checkout / "cache"
        self.library = self.preview_data / "luma-tide/library.db"
        self.library.parent.mkdir(parents=True)
        art_folder = self.preview_cache / "luma-tide/remote-artwork" / REMOTE_ID
        art_folder.mkdir(parents=True)
        self.cover = art_folder / "cover.image"
        self.cover.write_bytes(b"not really a picture")
        (art_folder / "covers.json").write_text(json.dumps({"cover.image": "al-1"}))

        connection = sqlite3.connect(self.library, isolation_level=None)
        LibraryStore._create_schema_1(connection)
        connection.executescript(_PREVIEW_STEPS)
        connection.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (REMOTE_ID, "Navidrome", KIND, server_address, 0, "browse,offline,play,stream,unencrypted",
             "online", reference_for(REMOTE_ID), 100, 200, account, "3:2026-09-01T10:00:00Z", 1),
        )
        connection.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (LOCAL_ID, "This device", "local-folder", (self.home / "Music").as_uri(), 1, "browse,play",
             "online", None, 100, 200, None, None, 1),
        )
        for index in range(3):
            track_id = f"track-{index}"
            connection.execute(
                "INSERT INTO tracks(id,identity_kind,identity_value,title,artist,album,album_artist,duration_ns,"
                "disc_number,track_number,year,genre,search_text,added_at,favorite,play_count) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (track_id, "source-item", f"{REMOTE_ID}\0song-{index}", f"Song {index}", "Artist", "Album",
                 "Artist", 60_000_000_000, 1, index + 1, 2020, "", f"song {index} artist album", 1000 + index,
                 int(index == 0), 7 * index),
            )
            connection.execute(
                "INSERT INTO copies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"copy-{index}", track_id, REMOTE_ID, f"{server_address}/rest/stream?id=song-{index}", "",
                 "FLAC", 900_000, 44100, 2, 1234, 0, self.cover.as_uri(), None, "available", 200),
            )
            connection.execute(
                "INSERT INTO queue(position,track_id,selected_copy_id,added_at) VALUES(?,?,?,?)",
                (index, track_id, None, 4_000_000_000),  # more recent than this library's queue
            )
        connection.execute(
            "UPDATE session SET current_position=1, position_ns=42, playback_state='paused' WHERE singleton=1"
        )
        connection.execute("INSERT INTO playlists VALUES('pl','Evening',1,1)")
        connection.execute("INSERT INTO playlist_items VALUES('pl',0,'track-2')")
        connection.close()

        launcher = checkout / "run-preview"
        launcher.write_text(
            "#!/bin/sh\n"
            f'export XDG_DATA_HOME="{self.preview_data}"\n'
            f'export XDG_CACHE_HOME="{self.preview_cache}"\n'
            "exec python3 -m luma_tide \"$@\"\n"
        )
        retired = self.state_home / "luma-tide/retired-overrides-20260917T145819Z/dbus-1/services"
        retired.mkdir(parents=True)
        (retired / "org.projectluma.Tide.service").write_text(
            f"[D-BUS Service]\nName=org.projectluma.Tide\nExec={launcher} --gapplication-service\n"
        )

    def import_into(self, store: LibraryStore, log: logging.Logger):
        return import_preview_library(
            store, log=log, state_home=self.state_home, data_home=self.data_home, cache_home=self.cache_home,
        )


class PreviewImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.preview = PreviewFixture(self.root)
        self.log = logging.getLogger("tide.test-sign-in-recovery")
        self.store = LibraryStore(self.preview.data_home / "luma-tide/library.db")
        self.addCleanup(self.store.close)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_retired_override_leads_to_the_preview_library(self) -> None:
        [home] = find_preview_homes(self.preview.state_home, self.preview.data_home)
        self.assertEqual(home.library, self.preview.library)

    def test_sources_songs_history_and_queue_come_along_once(self) -> None:
        # This library already had its own local folder and an older queue.
        local = self.store.add_source("This device", "local-folder", self.root / "Elsewhere", local=True)
        track, _copy = self.store.upsert_copy(local.id, MediaMetadata(
            uri=(self.root / "Elsewhere/a.flac").as_uri(), content_digest="a" * 64, title="Mine"))
        self.store.replace_queue([track.id])
        before = _digest(self.preview.library)

        result = self.preview.import_into(self.store, self.log)

        self.assertEqual((result.sources, result.tracks, result.copies, result.playlists), (2, 3, 3, 1))
        self.assertTrue(result.queue_restored)
        remote = self.store.source(REMOTE_ID)
        self.assertEqual((remote.account, remote.auth_ref), ("listener", reference_for(REMOTE_ID)))
        # Connecting decides the real state; the import never claims it's online.
        self.assertIs(remote.state, SourceState.OFFLINE)
        songs = {item.title: item for item in self.store.tracks(source_id=REMOTE_ID)}
        self.assertEqual(sorted(songs), ["Song 0", "Song 1", "Song 2"])
        self.assertEqual(songs["Song 2"].play_count, 14)
        self.assertTrue(songs["Song 0"].favorite)
        self.assertEqual([entry.track.title for entry in self.store.queue()], ["Song 0", "Song 1", "Song 2"])
        session = self.store.session_state()
        self.assertEqual((session.current_position, session.position_ns, session.playback_state), (1, 42, "paused"))
        [(playlist_id, name, count)] = self.store.playlists()
        self.assertEqual((name, count), ("Evening", 1))
        # Cached server artwork is copied into Tide's own cache and pointed at.
        copied = self.preview.cache_home / "luma-tide/remote-artwork" / REMOTE_ID / "cover.image"
        self.assertTrue(copied.is_file())
        self.assertTrue((copied.parent / "covers.json").is_file())
        [copy] = [item for item in self.store.copies_for_track("track-0")]
        self.assertEqual(copy.artwork_uri, copied.as_uri())
        # Credits are read again once, since the preview never stored them.
        self.assertTrue(self.store.consume_pending_artist_reindex())
        # The preview's own database is never written to.
        self.assertEqual(_digest(self.preview.library), before)
        # Once only.
        self.assertIsNone(self.preview.import_into(self.store, self.log))
        self.assertEqual(len(self.store.tracks(source_id=REMOTE_ID)), 3)

    def test_an_earlier_partial_adoption_is_completed_without_duplicates(self) -> None:
        # 2.luma.18-20 copied only the source row, without its account name.
        self.store.add_source("Navidrome", KIND, "http://music.invalid:4533", local=False,
                              auth_ref=reference_for(REMOTE_ID), source_id=REMOTE_ID)
        result = self.preview.import_into(self.store, self.log)
        self.assertEqual(result.sources, 1)  # only the local folder was new
        self.assertEqual(self.store.source(REMOTE_ID).account, "listener")
        self.assertEqual(len(self.store.tracks(source_id=REMOTE_ID)), 3)

    def test_an_unreadable_preview_library_is_left_for_a_later_start(self) -> None:
        self.preview.library.write_bytes(b"not a database")
        self.assertIsNone(self.preview.import_into(self.store, self.log))
        self.assertFalse(self.store.preview_import_done())

    def test_both_earlier_schema_lineages_converge(self) -> None:
        preview_copy = self.root / "preview-copy.db"
        preview_copy.write_bytes(self.preview.library.read_bytes())
        with LibraryStore(preview_copy) as upgraded:
            self.assertEqual(len(upgraded.tracks()), 3)
            self.assertEqual(upgraded.tracks()[0].artists, ("Artist",))
            self.assertTrue(upgraded.consume_pending_artist_reindex())
        connection = sqlite3.connect(preview_copy)
        self.addCleanup(connection.close)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"offline_items", "track_artists", "track_album_artists", "schema_state"} <= tables)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)


class SignInAgainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.server = FakeSubsonic(password=PASSWORD, songs=5)
        self.store = LibraryStore(self.root / "library.db")
        self.keyring = MemoryCredentialStore()
        self.remote = RemoteSources(
            self.store, lambda: self.keyring,
            offline_root=self.root / "offline", artwork_root=self.root / "artwork",
        )
        self.source = self.store.add_source(
            "Navidrome", KIND, self.server.address, local=False,
            capabilities=("browse", "play", "stream", "unencrypted"),
            auth_ref=reference_for("placeholder"), account="listener",
        )

    def tearDown(self) -> None:
        self.remote.close()
        self.server.close()
        self.store.close()
        self.temporary.cleanup()

    def test_a_missing_password_asks_for_a_sign_in_and_signing_in_resumes_sync(self) -> None:
        with self.assertRaises(AuthenticationFailed):
            self.remote.sync(self.source.id).result(10)
        self.assertIs(self.store.source(self.source.id).state, SourceState.AUTH_REQUIRED)

        with self.assertRaises(AuthenticationFailed):
            self.remote.add_server(self.server.address, "listener", "wrong", allow_plaintext=True).result(10)
        self.assertIs(self.store.source(self.source.id).state, SourceState.AUTH_REQUIRED)

        signed_in = self.remote.add_server(self.server.address, "listener", PASSWORD, allow_plaintext=True).result(10)
        self.assertEqual(signed_in.id, self.source.id)
        self.assertIs(signed_in.state, SourceState.ONLINE)
        result = self.remote.sync(signed_in.id).result(10)
        self.assertEqual(result.songs, 5)
        self.assertEqual(len(self.store.tracks(source_id=signed_in.id)), 5)
        self.assertEqual(list(self.keyring.secrets.values()), [PASSWORD])

    def test_a_sign_in_saved_by_earlier_packaged_builds_still_works(self) -> None:
        self.store.set_source_account(self.source.id, None)
        self.keyring.store(self.source.auth_ref, "old", json.dumps({"username": "listener", "password": PASSWORD}))
        self.assertIs(self.remote.connect(self.source.id).result(10), SourceState.ONLINE)
        self.assertEqual(self.store.source(self.source.id).account, "listener")
        self.assertEqual(self.keyring.lookup(self.source.auth_ref), PASSWORD)

    def test_testing_a_connection_saves_nothing(self) -> None:
        info = self.remote.test_server(self.server.address, "listener", PASSWORD, allow_plaintext=True).result(10)
        self.assertTrue(info.display_name)
        self.assertEqual(self.keyring.secrets, {})
        with self.assertRaises(AuthenticationFailed):
            self.remote.test_server(self.server.address, "listener", "wrong", allow_plaintext=True).result(10)


if __name__ == "__main__":
    unittest.main()
