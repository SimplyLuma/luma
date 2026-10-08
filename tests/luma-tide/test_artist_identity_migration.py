# SPDX-License-Identifier: Apache-2.0
"""The schema v2 -> v3 migration and the automatic, one-time re-index it
queues up for a database that already held tracks indexed before
multi-artist support existed.

`_V2_SCHEMA` below is a frozen snapshot of the schema LibraryStore's v0 and
v1 migration steps built (see `LibraryStore._migrate` in model.py) — the
exact shape a real pre-upgrade database has on disk. Migrations are
forward-only and the historical steps that produced it never change, so
hardcoding that snapshot here is the correct way to fixture "an existing
user's database", rather than re-deriving it from the current source (which
would just test the migration against itself).
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from luma_tide.indexer import LibraryIndexer
from luma_tide.metadata import BasicMetadataReader, MutagenMetadataReader, content_digest
from luma_tide.model import IDENTITY_NAMESPACE, LibraryStore, _canonical_uri

try:
    import mutagen  # noqa: F401

    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# Verbatim v0 schema (trimmed to the tables this test touches: sources,
# tracks, copies) plus the v1 `sync_marker` column — see model.py's
# `_migrate` for the full original script this is copied from.
_V2_SCHEMA = """
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
  last_seen INTEGER,
  sync_marker TEXT
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
PRAGMA user_version = 2;
COMMIT;
"""


def _build_v2_fixture(
    db_path: Path, *, source_id: str, source_uri: str, track_uri: str, digest: str,
) -> str:
    """A schema-v2 database file with one source and one track/copy row
    carrying old-style lossy single-artist data — no `track_artists`/
    `track_album_artists` tables exist yet, because v2 predates them."""
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(_V2_SCHEMA)
        track_id = str(uuid.uuid5(IDENTITY_NAMESPACE, f"track\0sha256\0{digest}"))
        copy_id = str(uuid.uuid5(IDENTITY_NAMESPACE, f"copy\0{source_id}\0{track_uri}"))
        connection.execute(
            """
            INSERT INTO sources(id,name,kind,uri,local,capabilities,state,auth_ref,created_at,last_seen)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (source_id, "This device", "local-folder", source_uri, 1, "browse,play", "online", None, 0, 0),
        )
        connection.execute(
            """
            INSERT INTO tracks(id,identity_kind,identity_value,title,artist,album,album_artist,
              duration_ns,disc_number,track_number,year,genre,search_text,added_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                track_id, "sha256", digest, "Low Tide",
                "Artist A",  # the old lossy result: `_text()` used to keep only value[0]
                "A Compilation", "Various Artists",
                180_000_000_000, 0, 1, 2020, "", "low tide artist a", 0,
            ),
        )
        connection.execute(
            """
            INSERT INTO copies(id,track_id,source_id,uri,content_digest,format,bitrate,
              sample_rate,channels,size,mtime_ns,artwork_uri,replaygain_track_gain,
              availability,last_seen)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (copy_id, track_id, source_id, track_uri, digest, "FLAC", 0, 0, 0, 0, 0, None, None, "available", 0),
        )
        connection.commit()
    finally:
        connection.close()
    return track_id


class ArtistIdentityMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_upgrading_a_populated_v2_database_flags_a_one_time_reindex(self) -> None:
        music = self.root / "Music"
        music.mkdir()
        track_path = music / "song.flac"
        track_path.write_bytes(b"not real audio, only its digest matters here")
        digest = content_digest(track_path)
        source_id = str(uuid.uuid4())
        db_path = self.root / "library.db"
        old_track_id = _build_v2_fixture(
            db_path,
            source_id=source_id,
            source_uri=music.resolve().as_uri(),
            track_uri=_canonical_uri(track_path),
            digest=digest,
        )

        # Opening the v2 fixture runs the v2->v3 migration for real.
        store = LibraryStore(db_path)
        try:
            raw = sqlite3.connect(db_path)
            try:
                version = raw.execute("PRAGMA user_version").fetchone()[0]
                pending = raw.execute(
                    "SELECT value FROM schema_state WHERE key='pending_artist_reindex'"
                ).fetchone()[0]
            finally:
                raw.close()
            self.assertEqual(version, 4)
            self.assertEqual(pending, "1")

            # The pre-migration track is readable and, since it has no
            # track_artists rows yet, correctly falls back to its old
            # single-name display value rather than an empty list.
            before = store.track(old_track_id)
            self.assertEqual(before.artist, "Artist A")
            self.assertEqual(before.artists, ("Artist A",))

            # This is the exact call application.py's do_startup makes: it
            # claims the flag exactly once.
            self.assertTrue(store.consume_pending_artist_reindex())
            self.assertFalse(store.consume_pending_artist_reindex())
        finally:
            store.close()

    def test_a_fresh_database_is_never_flagged_for_reindex(self) -> None:
        store = LibraryStore(self.root / "fresh.db")
        try:
            self.assertFalse(store.consume_pending_artist_reindex())
        finally:
            store.close()

    def test_forced_local_rescan_recovers_the_real_artist_list(self) -> None:
        """The end-to-end recovery path: a v2 database is upgraded, flagged,
        and then a `force=True` local re-scan (as application.py triggers
        for every local source when the flag was set) re-reads the actual
        file with the fixed reader and overwrites the old lossy row — not a
        second, duplicate track."""
        if not MUTAGEN_AVAILABLE:
            self.skipTest("mutagen is not installed in this environment")
        from mutagen.flac import FLAC

        music = self.root / "Music"
        music.mkdir()
        track_path = music / "song.flac"

        def streaminfo() -> bytes:
            block = (4096).to_bytes(2, "big") * 2 + (0).to_bytes(3, "big") * 2
            packed = (44100 << 44) | (1 << 41) | (15 << 36) | 88200
            return block + packed.to_bytes(8, "big") + b"\x00" * 16

        payload = streaminfo()
        header = bytes([0x80]) + len(payload).to_bytes(3, "big") + payload
        track_path.write_bytes(b"fLaC" + header)
        flac = FLAC(track_path)
        flac["title"] = ["Low Tide"]
        flac["artist"] = ["Artist A", "Artist B"]
        flac["albumartist"] = ["Various Artists"]
        flac.save()

        digest = content_digest(track_path)
        source_id = str(uuid.uuid4())
        db_path = self.root / "library.db"
        old_track_id = _build_v2_fixture(
            db_path,
            source_id=source_id,
            source_uri=music.resolve().as_uri(),
            track_uri=_canonical_uri(track_path),
            digest=digest,
        )

        store = LibraryStore(db_path)
        indexer = LibraryIndexer(store, MutagenMetadataReader(cache_root=self.root / "artwork"), workers=1)
        try:
            self.assertTrue(store.consume_pending_artist_reindex())
            result = indexer.scan_async(source_id, force=True).result(timeout=10)
            self.assertEqual(result.indexed, 1)
            self.assertEqual(result.unchanged, 0)

            recovered = store.track(old_track_id)
            self.assertEqual(recovered.id, old_track_id, "must update the same track, not duplicate it")
            self.assertEqual(recovered.artists, ("Artist A", "Artist B"))
            self.assertEqual(recovered.artist, "Artist A")
            self.assertEqual(store.artists_for_track(old_track_id), ["Artist A", "Artist B"])
            self.assertEqual(len(store.tracks()), 1)

            # The reindex is one-time: a second startup does not re-flag it.
            self.assertFalse(store.consume_pending_artist_reindex())
        finally:
            indexer.close()
            store.close()

    def test_an_unforced_scan_of_unchanged_files_does_not_recover_lossy_data(self) -> None:
        """Sanity check on the `force` flag itself: without it, an
        unchanged-on-disk file is left alone (`unchanged_copy`'s fast
        path), so the old lossy row would silently survive forever — this
        is exactly why the one-time re-index must pass `force=True`."""
        music = self.root / "Music"
        music.mkdir()
        track_path = music / "song.flac"
        track_path.write_bytes(b"not real audio, only its digest matters here")
        digest = content_digest(track_path)
        source_id = str(uuid.uuid4())
        db_path = self.root / "library.db"
        old_track_id = _build_v2_fixture(
            db_path,
            source_id=source_id,
            source_uri=music.resolve().as_uri(),
            track_uri=_canonical_uri(track_path),
            digest=digest,
        )
        store = LibraryStore(db_path)
        # Register the copy's current (size, mtime) as already "seen" by
        # writing them via the same helper a normal scan uses, so the
        # unforced scan below takes the unchanged fast path.
        stat = track_path.stat()
        raw = sqlite3.connect(db_path)
        try:
            raw.execute(
                "UPDATE copies SET size=?, mtime_ns=? WHERE track_id=?",
                (stat.st_size, stat.st_mtime_ns, old_track_id),
            )
            raw.commit()
        finally:
            raw.close()
        indexer = LibraryIndexer(store, BasicMetadataReader(), workers=1)
        try:
            store.consume_pending_artist_reindex()
            result = indexer.scan_async(source_id, force=False).result(timeout=10)
            self.assertEqual(result.unchanged, 1)
            self.assertEqual(result.indexed, 0)
            still_lossy = store.track(old_track_id)
            self.assertEqual(still_lossy.artists, ("Artist A",))
        finally:
            indexer.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
