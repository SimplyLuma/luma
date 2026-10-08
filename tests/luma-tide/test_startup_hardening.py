# SPDX-License-Identifier: Apache-2.0
"""Startup hardening added for 2.luma.19, after a real production incident:
a real user's library was found stuck at schema `PRAGMA user_version = 1`
(never migrated, never adopted a preview library) with ZERO stdout/stderr in
the journal for the whole session, even though `do_startup` calls
`LibraryStore()` (which runs `_migrate()` unconditionally) and
`adopt_preview_library()` completely synchronously with, at the time, no
try/except around either. This file covers the two defense-in-depth fixes
that came out of that:

  1. `LibraryStore._migrate()` and the one-time preview library import
     force a `PRAGMA wal_checkpoint(TRUNCATE)` right after a one-time,
     durability-sensitive commit, instead of trusting SQLite's own
     opportunistic checkpoint to run before a later hard crash.
  2. `startup.open_library_store` and `preview_import.import_preview_library`
     wrap those two steps so a failure is always logged (`log.exception`,
     never silent) and an import failing can never take down the rest of
     startup (covered in test_preview_import.py).

`_V1_SCHEMA` below is a frozen snapshot of exactly what `LibraryStore._migrate`'s
`version == 0` branch creates (see model.py) — i.e. a real pre-2.luma.17
user's database on disk: `PRAGMA user_version = 1`, no `schema_state` table,
no `track_artists`/`track_album_artists`. It is hardcoded rather than built
by stripping down a live `LibraryStore`, for the same reason
`test_artist_identity_migration.py`'s `_V2_SCHEMA` is: migrations are
forward-only and the historical step that produced this shape never changes,
so a frozen copy fixtures "an existing user's database" without the test
just re-deriving its fixture from the very migration code it is meant to
exercise.
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from luma_tide.model import LibraryStore
from luma_tide.startup import open_library_store

_V1_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  uri TEXT NOT NULL UNIQUE,
  local INTEGER NOT NULL CHECK (local IN (0, 1)),
  capabilities TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL,
  auth_ref TEXT,
  created_at INTEGER NOT NULL,
  last_seen INTEGER
);
CREATE TABLE tracks (
  id TEXT PRIMARY KEY,
  identity_kind TEXT NOT NULL,
  identity_value TEXT NOT NULL,
  title TEXT NOT NULL,
  artist TEXT NOT NULL,
  album TEXT NOT NULL,
  album_artist TEXT NOT NULL,
  duration_ns INTEGER NOT NULL,
  disc_number INTEGER NOT NULL,
  track_number INTEGER NOT NULL,
  year INTEGER NOT NULL,
  genre TEXT NOT NULL,
  search_text TEXT NOT NULL,
  added_at INTEGER NOT NULL,
  favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
  play_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(identity_kind, identity_value)
);
CREATE TABLE copies (
  id TEXT PRIMARY KEY,
  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  uri TEXT NOT NULL,
  content_digest TEXT NOT NULL,
  format TEXT NOT NULL,
  bitrate INTEGER NOT NULL,
  sample_rate INTEGER NOT NULL,
  channels INTEGER NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  artwork_uri TEXT,
  replaygain_track_gain REAL,
  availability TEXT NOT NULL,
  last_seen INTEGER NOT NULL,
  UNIQUE(source_id, uri)
);
CREATE INDEX copies_track_idx ON copies(track_id);
CREATE INDEX copies_source_idx ON copies(source_id);
CREATE INDEX tracks_album_idx ON tracks(album_artist, album, disc_number, track_number);
CREATE INDEX tracks_search_idx ON tracks(search_text);
CREATE TABLE playlists (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE playlist_items (
  playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  PRIMARY KEY (playlist_id, position)
);
CREATE TABLE queue (
  position INTEGER PRIMARY KEY,
  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  selected_copy_id TEXT REFERENCES copies(id) ON DELETE SET NULL,
  added_at INTEGER NOT NULL
);
CREATE TABLE session (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  current_position INTEGER NOT NULL DEFAULT 0,
  position_ns INTEGER NOT NULL DEFAULT 0,
  shuffle INTEGER NOT NULL DEFAULT 0 CHECK (shuffle IN (0, 1)),
  repeat_mode TEXT NOT NULL DEFAULT 'off',
  volume REAL NOT NULL DEFAULT 1.0,
  playback_state TEXT NOT NULL DEFAULT 'stopped'
);
INSERT INTO session(singleton) VALUES(1);
CREATE TABLE corrections (
  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(track_id, field)
);
PRAGMA user_version = 1;
COMMIT;
"""


def _wal_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + "-wal")


def _build_v1_fixture(db_path: Path, *, source_id: str, source_uri: str) -> None:
    """A schema-v1 database file carrying exactly one local-folder source
    and no tracks — the same shape the real incident's library.db was
    found stuck at ("only the original two local-folder sources")."""
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(_V1_SCHEMA)
        connection.execute(
            "INSERT INTO sources(id,name,kind,uri,local,capabilities,state,auth_ref,"
            "created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (source_id, "This device", "local-folder", source_uri, 1, "browse,play",
             "online", None, 1_700_000_000, 1_700_000_000),
        )
        connection.commit()
    finally:
        connection.close()


class MigrationDurabilityTests(unittest.TestCase):
    """Task 3, bullet 1: a real v1 library migrates to v3 regardless of, and
    with no knowledge of, any preview database — the two are only ever
    related through `adopt_preview_library`, called separately."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_v1_database_migrates_to_the_current_schema(self) -> None:
        db_path = self.root / "library.db"
        local_folder = self.root / "Music"
        source_id = str(uuid.uuid4())
        _build_v1_fixture(db_path, source_id=source_id, source_uri=local_folder.as_uri())

        store = LibraryStore(db_path)
        self.addCleanup(store.close)

        raw = sqlite3.connect(db_path)
        try:
            version = raw.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0] for row in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            pending = raw.execute(
                "SELECT value FROM schema_state WHERE key='pending_artist_reindex'"
            ).fetchone()[0]
        finally:
            raw.close()

        self.assertEqual(version, 4)
        self.assertIn("offline_items", tables)
        self.assertIn("schema_state", tables)
        self.assertIn("track_artists", tables)
        self.assertIn("track_album_artists", tables)
        # starting_version was 1, not 0, so this pre-existing (if empty)
        # database is still flagged — see _migrate()'s pending_reindex rule.
        self.assertEqual(pending, "1")
        # The migrated-in source survived, under its original id.
        self.assertEqual({source.id for source in store.sources()}, {source_id})

    def test_migration_checkpoints_the_wal_immediately(self) -> None:
        """The durability fix itself: after a real migration runs, the WAL
        is truncated right away rather than left for SQLite's own later,
        opportunistic checkpoint — proven here by the -wal file being
        emptied out immediately after LibraryStore() returns, without an
        explicit close (a clean close would checkpoint on its own and mask
        exactly what this test needs to prove)."""
        db_path = self.root / "library.db"
        _build_v1_fixture(
            db_path, source_id=str(uuid.uuid4()), source_uri=(self.root / "Music").as_uri()
        )

        store = LibraryStore(db_path)
        self.addCleanup(store.close)

        wal = _wal_path(db_path)
        # WAL mode always leaves a -wal file behind; TRUNCATE checkpointing
        # it empties it back to (near) zero bytes instead of leaving the
        # migration's frames sitting there uncommitted-to-the-base-file.
        self.assertTrue(wal.exists())
        self.assertEqual(wal.stat().st_size, 0)

    def test_a_database_already_at_schema_version_is_not_re_checkpointed(self) -> None:
        """No migration ran, so _migrate() must not force a checkpoint —
        verified by observing the checkpoint helper is never called."""
        store = LibraryStore(self.root / "fresh.db")
        self.addCleanup(store.close)
        with mock.patch.object(LibraryStore, "_checkpoint") as checkpoint:
            # Re-opening the connection's migrate path directly (a fresh
            # database is already at SCHEMA_VERSION, so _migrate() is a
            # no-op here) — call it again the same way __init__ does.
            store._migrate()
        checkpoint.assert_not_called()


class GuardedStartupTests(unittest.TestCase):
    """Task 1 / Task 3 bullets 5-6: `startup.py`'s wrapping around the
    riskiest part of `do_startup`, tested directly since `application.py`
    cannot be imported without a real GTK/Adw stack (it imports `gi`
    unconditionally at module scope)."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.logger = logging.getLogger("tide.test-startup-hardening")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_open_library_store_returns_a_working_store(self) -> None:
        store = open_library_store(self.root / "library.db", log=self.logger)
        try:
            self.assertEqual(store.sources(), [])
        finally:
            store.close()

    def test_open_library_store_logs_and_reraises_on_failure(self) -> None:
        with mock.patch("luma_tide.startup.LibraryStore", side_effect=RuntimeError("disk gone")):
            with self.assertLogs(self.logger, level="ERROR") as captured:
                with self.assertRaises(RuntimeError):
                    open_library_store(self.root / "library.db", log=self.logger)
        self.assertTrue(any("could not open or migrate" in message for message in captured.output))


if __name__ == "__main__":
    unittest.main()
