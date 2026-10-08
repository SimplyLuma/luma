# SPDX-License-Identifier: Apache-2.0
"""Transactional library, source/copy truth, playlists, and queue state.

Tide only merges copies when it has a strong identity: an upstream recording
identifier or an exact content digest. Human-readable tags are never treated
as identity, so two recordings with the same title and artist remain distinct.
A remote item without either keeps an identity scoped to its source and server
item, so it never merges with anything it has not been proven to be.
"""
from __future__ import annotations

import contextlib
import enum
import logging
import os
import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence
from urllib.parse import unquote, urlparse

# Version 4 is the first schema both earlier lineages converge on. Two builds
# independently numbered their own versions 2 and 3 (a dev preview with remote
# sources and downloads; the packaged build with multi-artist credits), so a
# database at 1-3 is brought forward by inspecting what it actually holds.
SCHEMA_VERSION = 4
IDENTITY_NAMESPACE = uuid.UUID("79dc5ec4-b9d2-4b29-8b20-71d56ca16a67")
# SQLite's default bound on host parameters in one statement is 32766; batch
# lookups stay well below it so a very large library never hits it.
_PARAMETER_BATCH = 900

# Same logger name application.py logs to, so a store warning lands in the
# same journal stream under one configured handler.
_log = logging.getLogger("tide")


class SourceState(str, enum.Enum):
    ONLINE = "online"
    SYNCING = "syncing"
    OFFLINE = "offline"
    AUTH_REQUIRED = "auth-required"
    ERROR = "error"


class CopyAvailability(str, enum.Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"


class OfflineState(str, enum.Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    FAILED = "failed"


class RepeatMode(str, enum.Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    name: str
    kind: str
    uri: str
    local: bool
    capabilities: tuple[str, ...]
    state: SourceState
    auth_ref: str | None
    last_seen: int | None
    account: str | None = None
    sync_marker: str | None = None
    shown: bool = True


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    uri: str
    content_digest: str
    title: str
    artist: str = "Unknown artist"
    album: str = "Unknown album"
    album_artist: str = ""
    # The full ordered list of artist names a tag format that structurally
    # supports more than one (Vorbis/FLAC repeated fields, MP4 multi-value
    # atoms, ID3v2.4 null-separated text frames, OpenSubsonic artist arrays)
    # actually carries. Empty when the reader could not tell; the single
    # `artist`/`album_artist` string is then the whole credit.
    artists: tuple[str, ...] = ()
    album_artists: tuple[str, ...] = ()
    duration_ns: int = 0
    disc_number: int = 0
    track_number: int = 0
    year: int = 0
    genre: str = ""
    format: str = ""
    bitrate: int = 0
    sample_rate: int = 0
    channels: int = 0
    size: int = 0
    mtime_ns: int = 0
    artwork_uri: str | None = None
    recording_id: str | None = None
    replaygain_track_gain: float | None = None
    # A server's own item identifier, used only when neither a recording ID nor
    # a content digest is known. It never merges across sources.
    source_item_id: str | None = None


@dataclass(frozen=True, slots=True)
class Track:
    id: str
    title: str
    artist: str
    album: str
    album_artist: str
    # The ordered per-track artist credit; always at least one name. It falls
    # back to (artist,) / (album_artist,) for a single-artist source or a track
    # not yet re-read since multi-artist credits were added.
    artists: tuple[str, ...]
    album_artists: tuple[str, ...]
    duration_ns: int
    disc_number: int
    track_number: int
    year: int
    genre: str
    added_at: int
    favorite: bool
    play_count: int


@dataclass(frozen=True, slots=True)
class TrackCopy:
    id: str
    track_id: str
    source_id: str
    uri: str
    format: str
    bitrate: int
    sample_rate: int
    channels: int
    artwork_uri: str | None
    availability: CopyAvailability
    source_name: str
    source_local: bool
    source_state: SourceState
    size: int = 0
    offline_state: OfflineState | None = None
    offline_path: str | None = None

    @property
    def offline_ready(self) -> bool:
        """A completed download whose file is still on this device."""
        return (
            self.offline_state is OfflineState.COMPLETE
            and self.offline_path is not None
            and Path(self.offline_path).is_file()
        )

    @property
    def here(self) -> bool:
        return self.source_local or self.offline_ready


@dataclass(frozen=True, slots=True)
class OfflineItem:
    copy_id: str
    track_id: str
    source_id: str
    uri: str
    format: str
    size: int
    state: OfflineState
    path: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class OfflineSummary:
    complete: int
    pending: int
    failed: int
    bytes: int


@dataclass(frozen=True, slots=True)
class SearchResults:
    """Matches grouped the way a person looks for music: albums, artists, songs."""

    query: str
    albums: list[tuple[str, str, int, int, str | None]]
    artists: list[tuple[str, int]]
    songs: list["Track"]


@dataclass(frozen=True, slots=True)
class PreviewImport:
    sources: int
    tracks: int
    copies: int
    playlists: int
    queue_restored: bool


@dataclass(frozen=True, slots=True)
class QueueEntry:
    position: int
    track: Track
    selected_copy_id: str | None


@dataclass(frozen=True, slots=True)
class SessionState:
    current_position: int
    position_ns: int
    shuffle: bool
    repeat: RepeatMode
    volume: float
    playback_state: str


def _normalized(text: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKD", text).casefold().split()
    )


# Placeholders tag readers and servers put where a name is missing (Navidrome
# sends "[Unknown Artist]"). Tide says "Unknown artist" in sentence case.
_UNKNOWN_NAMES = {"[unknown artist]", "[unknown album]", "[unknown]", "unknown artist", "unknown album"}


def _named(value: str) -> str:
    """A real name, or "" where a source only had a placeholder."""
    value = value.strip()
    return "" if value.casefold() in _UNKNOWN_NAMES else value


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def _cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def preview_library_path(data_home: Path | None = None) -> Path:
    """Where a dev-preview build of Tide kept its own library. The preview ran
    with its own XDG data directory inside its checkout."""
    return (data_home or _data_home()) / "luma-dev/tide-navidrome/data/luma-tide/library.db"


def _canonical_uri(value: str | Path) -> str:
    if isinstance(value, Path):
        return value.expanduser().resolve().as_uri()
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).expanduser().resolve().as_uri()
    if not parsed.scheme:
        return Path(value).expanduser().resolve().as_uri()
    return value


class LibraryStore:
    """A SQLite store with atomic, forward-only migrations.

    Each thread gets its own connection. WAL lets the GTK thread read while a
    sync or scan thread writes, so a large import never holds the window up.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = _data_home() / "luma-tide/library.db"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self._closed = False
        # Bumped whenever the queue's contents change, so views can tell
        # without reading it again.
        self.queue_revision = 0
        self._migrate()
        self._name_unknowns()

    def _thread_connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            return connection
        with self._connections_lock:
            if self._closed:
                raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
            connection = sqlite3.connect(
                self.path, timeout=15, isolation_level=None, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            self._connections.append(connection)
        self._local.connection = connection
        self._local.lock = threading.RLock()
        return connection

    @property
    def _connection(self) -> sqlite3.Connection:
        return self._thread_connection()

    @property
    def _lock(self) -> threading.RLock:
        self._thread_connection()
        return self._local.lock

    def close(self) -> None:
        with self._connections_lock:
            self._closed = True
            connections, self._connections = self._connections, []
        for connection in connections:
            connection.close()

    def __enter__(self) -> "LibraryStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def _migrate(self) -> None:
        # sqlite3.executescript() owns its transaction boundary, so each schema
        # script explicitly begins and commits as one atomic migration.
        with self._lock:
            db = self._connection
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            starting_version = version
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Tide library schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version == 0:
                self._create_schema_1(db)
                version = 1
            if version < SCHEMA_VERSION:
                self._converge_schema(db, fresh=starting_version == 0)
                version = SCHEMA_VERSION
            # A one-time migration is forced out of the WAL at once, so it
            # survives a hard crash moments later and not only a clean exit.
            if version != starting_version:
                _log.info("Tide library schema migrated from %d to %d", starting_version, version)
                self._checkpoint(f"schema migration {starting_version}->{version}")

    @staticmethod
    def _columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _converge_schema(db: sqlite3.Connection, *, fresh: bool) -> None:
        """Bring a version 1, 2 or 3 database of either earlier lineage to
        version 4 by adding whatever it lacks. Nothing is dropped or rewritten.

        An existing database that had no artist credit tables is flagged for one re-read of its sources, since the credits it never
        stored can only come from the files and servers themselves."""
        db.execute("BEGIN IMMEDIATE")
        try:
            sources = LibraryStore._columns(db, "sources")
            if "account" not in sources:
                db.execute("ALTER TABLE sources ADD COLUMN account TEXT")
            if "sync_marker" not in sources:
                db.execute("ALTER TABLE sources ADD COLUMN sync_marker TEXT")
            if "shown" not in sources:
                db.execute(
                    "ALTER TABLE sources ADD COLUMN shown INTEGER NOT NULL DEFAULT 1 CHECK (shown IN (0, 1))"
                )
            had_credits = bool(LibraryStore._columns(db, "track_artists"))
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS offline_items (
                  copy_id TEXT PRIMARY KEY REFERENCES copies(id) ON DELETE CASCADE,
                  state TEXT NOT NULL CHECK (state IN ('queued','downloading','complete','failed')),
                  path TEXT,
                  size INTEGER NOT NULL DEFAULT 0,
                  error TEXT,
                  requested_at INTEGER NOT NULL,
                  completed_at INTEGER
                )
                """
            )
            for table in ("track_artists", "track_album_artists"):
                db.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                      track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                      position INTEGER NOT NULL,
                      name TEXT NOT NULL,
                      PRIMARY KEY (track_id, position)
                    )
                    """
                )
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_track_idx ON {table}(track_id)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            pending = "1" if (not fresh and not had_credits) else "0"
            db.execute(
                "INSERT INTO schema_state(key, value) VALUES ('pending_artist_reindex', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (pending,),
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")

    def _name_unknowns(self) -> None:
        """Songs synced before placeholders were recognised say "Unknown
        artist" and "Unknown album" like every other song without one."""
        placeholders = tuple(sorted(_UNKNOWN_NAMES))
        marks = ",".join("?" * len(placeholders))
        with self.transaction() as db:
            for column, name in (("artist", "Unknown artist"), ("album_artist", "Unknown artist"),
                                 ("album", "Unknown album")):
                db.execute(
                    f"UPDATE tracks SET {column}=? WHERE lower({column}) IN ({marks}) AND {column}<>?",
                    (name, *placeholders, name),
                )
            for table in ("track_artists", "track_album_artists"):
                db.execute(
                    f"UPDATE {table} SET name='Unknown artist' WHERE lower(name) IN ({marks})"
                    " AND name<>'Unknown artist'",
                    placeholders,
                )

    def _checkpoint(self, reason: str) -> None:
        """Fold the WAL back into the database file now. Used right after a
        one-time commit (a schema migration, a preview library import). A
        checkpoint blocked by a reader is logged, never raised: the data is
        already committed either way."""
        with self._lock:
            try:
                row = self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            except sqlite3.Error:
                _log.warning("wal checkpoint after %s failed; the data is already committed", reason)
                return
        if row is not None and row[0]:
            _log.warning(
                "wal checkpoint after %s could not complete (busy=%s); the data is already committed",
                reason, row[0],
            )

    def consume_pending_artist_reindex(self) -> bool:
        """True exactly once for a database flagged as holding tracks indexed
        before multi-artist credits existed; the caller re-reads every source."""
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM schema_state WHERE key='pending_artist_reindex'"
            ).fetchone()
            if row is None or row["value"] != "1":
                return False
            db.execute("UPDATE schema_state SET value='0' WHERE key='pending_artist_reindex'")
        return True

    def preview_import_done(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM schema_state WHERE key='preview_library_imported'"
            ).fetchone()
        return row is not None and row["value"] == "1"

    def import_preview_library(
        self,
        preview_path: str | Path,
        *,
        rewrite: Callable[[str | None], str | None] = lambda value: value,
    ) -> PreviewImport | None:
        """Bring a dev-preview build's library into this one, once.

        Some people ran a preview build of Tide, with its own library, before
        the packaged Tide had remote sources. This copies that library's
        sources (with the account name and keyring reference, never a
        secret), songs, copies, play counts, favorites, playlists, corrections
        and, when it is the more recent one, the queue and where playback
        stopped. Rows this library already has are kept; nothing is deleted.
        `rewrite` maps a file URI or path under the preview's own data or
        cache directory to where the caller copied that file.

        The preview database is opened read-only. Returns what was imported,
        or None when the import already ran or there is nothing compatible to
        import. The one-time flag is recorded only after a successful import,
        so an unreadable preview database is tried again on a later start."""
        if self.preview_import_done():
            return None
        path = Path(preview_path)
        if not path.is_file():
            return None
        try:
            preview = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            _log.warning("Tide couldn't open the preview library at %s", path)
            return None
        preview.row_factory = sqlite3.Row
        try:
            return self._import_preview(preview, rewrite)
        except sqlite3.Error as error:
            _log.warning("Tide couldn't read the preview library at %s: %s", path, error)
            return None
        finally:
            preview.close()

    def _import_preview(
        self, preview: sqlite3.Connection, rewrite: Callable[[str | None], str | None]
    ) -> PreviewImport | None:
        tables = {row[0] for row in preview.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sources", "tracks", "copies"} <= tables:
            _log.warning("The preview library has no Tide tables; nothing imported")
            return None

        def rows(table: str) -> list[sqlite3.Row]:
            return preview.execute(f"SELECT * FROM {table}").fetchall() if table in tables else []

        def get(row: sqlite3.Row, key: str, default: object = None) -> object:
            return row[key] if key in row.keys() else default

        with self.transaction() as db:
            known = {row["id"]: row for row in db.execute("SELECT * FROM sources")}
            known_uris = {row["uri"]: row["id"] for row in known.values()}
            same_sources: set[str] = set()
            sources_added = 0
            for row in rows("sources"):
                if row["id"] in known:
                    same_sources.add(row["id"])
                    # Fill in what an earlier, partial adoption left out.
                    db.execute(
                        """
                        UPDATE sources SET account=COALESCE(account, ?), auth_ref=COALESCE(auth_ref, ?)
                         WHERE id=?
                        """,
                        (get(row, "account"), row["auth_ref"], row["id"]),
                    )
                    continue
                if row["uri"] in known_uris:
                    continue  # the same folder under another identity: rescanned, not copied
                remote = not row["local"]
                db.execute(
                    """
                    INSERT INTO sources(id,name,kind,uri,local,capabilities,state,auth_ref,created_at,
                      last_seen,account,sync_marker,shown)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["id"], row["name"], row["kind"], row["uri"], row["local"],
                        row["capabilities"], (SourceState.OFFLINE if remote else SourceState.ONLINE).value,
                        row["auth_ref"], row["created_at"], row["last_seen"], get(row, "account"),
                        get(row, "sync_marker"), get(row, "shown", 1),
                    ),
                )
                same_sources.add(row["id"])
                sources_added += 1

            tracks_added = 0
            for row in rows("tracks"):
                result = db.execute(
                    """
                    INSERT INTO tracks(id,identity_kind,identity_value,title,artist,album,album_artist,
                      duration_ns,disc_number,track_number,year,genre,search_text,added_at,favorite,play_count)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        row["id"], row["identity_kind"], row["identity_value"], row["title"], row["artist"],
                        row["album"], row["album_artist"], row["duration_ns"], row["disc_number"],
                        row["track_number"], row["year"], row["genre"], row["search_text"], row["added_at"],
                        row["favorite"], row["play_count"],
                    ),
                )
                if result.rowcount:
                    tracks_added += 1
                else:
                    db.execute(
                        """
                        UPDATE tracks SET play_count=MAX(play_count, ?), favorite=MAX(favorite, ?),
                          added_at=MIN(added_at, ?)
                         WHERE id=?
                        """,
                        (row["play_count"], row["favorite"], row["added_at"], row["id"]),
                    )
            track_ids = {row[0] for row in db.execute("SELECT id FROM tracks")}

            copies_added = 0
            for row in rows("copies"):
                if row["source_id"] not in same_sources or row["track_id"] not in track_ids:
                    continue
                result = db.execute(
                    """
                    INSERT INTO copies(id,track_id,source_id,uri,content_digest,format,bitrate,sample_rate,
                      channels,size,mtime_ns,artwork_uri,replaygain_track_gain,availability,last_seen)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        row["id"], row["track_id"], row["source_id"], row["uri"], row["content_digest"],
                        row["format"], row["bitrate"], row["sample_rate"], row["channels"], row["size"],
                        row["mtime_ns"], rewrite(row["artwork_uri"]), row["replaygain_track_gain"],
                        row["availability"], row["last_seen"],
                    ),
                )
                copies_added += result.rowcount
            copy_ids = {row[0] for row in db.execute("SELECT id FROM copies")}

            for row in rows("offline_items"):
                path = rewrite(row["path"])
                if row["copy_id"] not in copy_ids or (row["state"] == "complete" and not (path and Path(path).is_file())):
                    continue
                db.execute(
                    """
                    INSERT INTO offline_items(copy_id,state,path,size,error,requested_at,completed_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING
                    """,
                    (row["copy_id"], row["state"], path, row["size"], row["error"],
                     row["requested_at"], row["completed_at"]),
                )

            for row in rows("corrections"):
                if row["track_id"] in track_ids:
                    db.execute(
                        "INSERT INTO corrections(track_id,field,value) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                        (row["track_id"], row["field"], row["value"]),
                    )

            playlists_added = 0
            items = rows("playlist_items")
            for row in rows("playlists"):
                result = db.execute(
                    "INSERT INTO playlists(id,name,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                    (row["id"], row["name"], row["created_at"], row["updated_at"]),
                )
                if not result.rowcount:
                    continue  # same id or same name: this library's playlist stays as it is
                playlists_added += 1
                position = 0
                for item in sorted((i for i in items if i["playlist_id"] == row["id"]), key=lambda i: i["position"]):
                    if item["track_id"] in track_ids:
                        db.execute(
                            "INSERT INTO playlist_items(playlist_id,position,track_id) VALUES(?,?,?)",
                            (row["id"], position, item["track_id"]),
                        )
                        position += 1

            queue_restored = False
            preview_queue = [row for row in rows("queue") if row["track_id"] in track_ids]
            if preview_queue:
                newest_here = db.execute("SELECT MAX(added_at) FROM queue").fetchone()[0]
                newest_there = max(row["added_at"] for row in preview_queue)
                if newest_here is None or newest_there > newest_here:
                    db.execute("DELETE FROM queue")
                    for position, row in enumerate(sorted(preview_queue, key=lambda r: r["position"])):
                        selected = row["selected_copy_id"] if row["selected_copy_id"] in copy_ids else None
                        db.execute(
                            "INSERT INTO queue(position,track_id,selected_copy_id,added_at) VALUES(?,?,?,?)",
                            (position, row["track_id"], selected, row["added_at"]),
                        )
                    session = rows("session")
                    if session:
                        state = session[0]
                        db.execute(
                            """
                            UPDATE session SET current_position=?, position_ns=?, shuffle=?, repeat_mode=?,
                              volume=?, playback_state=? WHERE singleton=1
                            """,
                            (
                                min(max(0, state["current_position"]), len(preview_queue) - 1),
                                state["position_ns"], state["shuffle"], state["repeat_mode"], state["volume"],
                                # Nothing is playing at launch; a paused song resumes on request.
                                "paused" if state["playback_state"] in ("playing", "paused") else "stopped",
                            ),
                        )
                    queue_restored = True
                    self.queue_revision += 1

            if tracks_added:
                # The preview never stored multi-artist credits: re-read sources once.
                db.execute(
                    "INSERT INTO schema_state(key, value) VALUES ('pending_artist_reindex', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
            db.execute(
                "INSERT INTO schema_state(key, value) VALUES ('preview_library_imported', '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
        summary = PreviewImport(sources_added, tracks_added, copies_added, playlists_added, queue_restored)
        self._checkpoint("preview library import")
        return summary

    @staticmethod
    def _create_schema_1(db: sqlite3.Connection) -> None:
        db.executescript(
            """
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
        )

    @staticmethod
    def _now(db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT unixepoch()").fetchone()[0])

    def add_source(
        self,
        name: str,
        kind: str,
        uri: str | Path,
        *,
        local: bool,
        capabilities: Sequence[str] = ("browse", "play"),
        auth_ref: str | None = None,
        account: str | None = None,
        source_id: str | None = None,
    ) -> Source:
        canonical = _canonical_uri(uri)
        parsed = urlparse(canonical)
        if parsed.username or parsed.password:
            raise ValueError("source credentials must be stored in the system secret service")
        stable_id = source_id or self.source_id_for(kind, canonical)
        with self.transaction() as db:
            now = self._now(db)
            db.execute(
                """
                INSERT INTO sources(id,name,kind,uri,local,capabilities,state,auth_ref,created_at,last_seen,account)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,
                  uri=excluded.uri, local=excluded.local,
                  capabilities=excluded.capabilities, auth_ref=excluded.auth_ref,
                  account=excluded.account
                """,
                (
                    stable_id,
                    name.strip() or "Music",
                    kind,
                    canonical,
                    int(local),
                    ",".join(sorted(set(capabilities))),
                    SourceState.ONLINE.value,
                    auth_ref,
                    now,
                    now,
                    account,
                ),
            )
        return self.source(stable_id)

    @staticmethod
    def source_id_for(kind: str, uri: str | Path) -> str:
        """The stable identifier `add_source` gives a source at this location."""
        canonical = _canonical_uri(uri)
        return str(uuid.uuid5(IDENTITY_NAMESPACE, f"source\0{kind}\0{canonical}"))

    def source(self, source_id: str) -> Source:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._source_from_row(row)

    def sources(self) -> list[Source]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sources ORDER BY local DESC, name COLLATE NOCASE"
            ).fetchall()
        return [self._source_from_row(row) for row in rows]

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> Source:
        return Source(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            uri=row["uri"],
            local=bool(row["local"]),
            capabilities=tuple(filter(None, row["capabilities"].split(","))),
            state=SourceState(row["state"]),
            auth_ref=row["auth_ref"],
            last_seen=row["last_seen"],
            account=row["account"],
            sync_marker=row["sync_marker"],
            shown=bool(row["shown"]),
        )

    def set_source_state(self, source_id: str, state: SourceState) -> None:
        with self.transaction() as db:
            now = self._now(db) if state in (SourceState.ONLINE, SourceState.SYNCING) else None
            result = db.execute(
                "UPDATE sources SET state=?, last_seen=COALESCE(?,last_seen) WHERE id=?",
                (state.value, now, source_id),
            )
            if result.rowcount != 1:
                raise KeyError(source_id)

    def set_source_shown(self, source_id: str, shown: bool) -> None:
        with self.transaction() as db:
            result = db.execute("UPDATE sources SET shown=? WHERE id=?", (int(shown), source_id))
            if result.rowcount != 1:
                raise KeyError(source_id)

    def show_only_source(self, source_id: str) -> None:
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone() is None:
                raise KeyError(source_id)
            db.execute("UPDATE sources SET shown=(id=?)", (source_id,))

    def show_all_sources(self) -> None:
        with self.transaction() as db:
            db.execute("UPDATE sources SET shown=1")

    def _shown_clause(self) -> str | None:
        """SQL limiting `t` to songs with a copy in a shown source, or None when
        every source is shown (then songs without any copy stay visible too)."""
        hidden = self._connection.execute("SELECT 1 FROM sources WHERE shown=0 LIMIT 1").fetchone()
        if hidden is None:
            return None
        return ("EXISTS (SELECT 1 FROM copies shown_copy JOIN sources shown_source"
                " ON shown_source.id=shown_copy.source_id"
                " WHERE shown_copy.track_id=t.id AND shown_source.shown=1)")

    def set_source_account(self, source_id: str, account: str | None) -> None:
        with self.transaction() as db:
            result = db.execute("UPDATE sources SET account=? WHERE id=?", (account, source_id))
            if result.rowcount != 1:
                raise KeyError(source_id)

    def set_sync_marker(self, source_id: str, marker: str | None) -> None:
        with self.transaction() as db:
            result = db.execute(
                "UPDATE sources SET sync_marker=? WHERE id=?", (marker, source_id)
            )
            if result.rowcount != 1:
                raise KeyError(source_id)

    def remove_source(self, source_id: str) -> list[str]:
        """Forget a source and its database copies; never unlink media files.

        Tracks left with no copy anywhere are forgotten too, unless a playlist
        still holds them. Returns the paths of this source's offline downloads,
        which Tide owns and the caller deletes once the records are gone.
        """
        with self.transaction() as db:
            downloads = [
                row["path"]
                for row in db.execute(
                    """
                    SELECT o.path FROM offline_items o JOIN copies c ON c.id=o.copy_id
                     WHERE c.source_id=? AND o.path IS NOT NULL
                    """,
                    (source_id,),
                )
            ]
            result = db.execute("DELETE FROM sources WHERE id=?", (source_id,))
            if result.rowcount != 1:
                raise KeyError(source_id)
            self.queue_revision += 1
            db.execute(
                """
                DELETE FROM tracks
                 WHERE NOT EXISTS (SELECT 1 FROM copies c WHERE c.track_id=tracks.id)
                   AND NOT EXISTS (SELECT 1 FROM playlist_items i WHERE i.track_id=tracks.id)
                """
            )
        return downloads

    def upsert_copy(self, source_id: str, metadata: MediaMetadata) -> tuple[Track, str]:
        track_id, copy_id = self.upsert_copies(source_id, (metadata,))[0]
        return self.track(track_id), copy_id

    def upsert_copies(
        self, source_id: str, items: Sequence[MediaMetadata]
    ) -> list[tuple[str, str]]:
        """Record many copies from one source in a single durable transaction."""
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone() is None:
                raise KeyError(source_id)
            now = self._now(db)
            written = [self._upsert_copy(db, source_id, metadata, now) for metadata in items]
            db.execute(
                "UPDATE sources SET state=?, last_seen=? WHERE id=?",
                (SourceState.ONLINE.value, now, source_id),
            )
        return written

    @staticmethod
    def _identity(source_id: str, metadata: MediaMetadata) -> tuple[str, str]:
        if metadata.recording_id:
            return "recording-id", metadata.recording_id
        if metadata.content_digest:
            return "sha256", metadata.content_digest
        if metadata.source_item_id:
            return "source-item", f"{source_id}\0{metadata.source_item_id}"
        raise ValueError("a recording ID, exact content digest, or source item is required")

    @staticmethod
    def _upsert_copy(
        db: sqlite3.Connection, source_id: str, metadata: MediaMetadata, now: int
    ) -> tuple[str, str]:
        canonical_uri = _canonical_uri(metadata.uri)
        identity_kind, identity_value = LibraryStore._identity(source_id, metadata)
        track_id = str(
            uuid.uuid5(IDENTITY_NAMESPACE, f"track\0{identity_kind}\0{identity_value}")
        )
        copy_id = str(
            uuid.uuid5(IDENTITY_NAMESPACE, f"copy\0{source_id}\0{canonical_uri}")
        )
        title = metadata.title.strip() or Path(unquote(urlparse(canonical_uri).path)).stem
        artist = _named(metadata.artist) or "Unknown artist"
        album = _named(metadata.album) or "Unknown album"
        album_artist = _named(metadata.album_artist) or artist
        search_text = _normalized(" ".join((title, artist, album, album_artist, metadata.genre)))
        # The identity decides the song; RETURNING gives the id it already has,
        # which an imported library may have assigned differently.
        track_id = db.execute(
            """
            INSERT INTO tracks(id,identity_kind,identity_value,title,artist,album,album_artist,
              duration_ns,disc_number,track_number,year,genre,search_text,added_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(identity_kind,identity_value) DO UPDATE SET
              title=excluded.title, artist=excluded.artist, album=excluded.album,
              album_artist=excluded.album_artist, duration_ns=excluded.duration_ns,
              disc_number=excluded.disc_number, track_number=excluded.track_number,
              year=excluded.year, genre=excluded.genre, search_text=excluded.search_text
            RETURNING id
            """,
            (
                track_id,
                identity_kind,
                identity_value,
                title,
                artist,
                album,
                album_artist,
                max(0, metadata.duration_ns),
                max(0, metadata.disc_number),
                max(0, metadata.track_number),
                max(0, metadata.year),
                metadata.genre.strip(),
                search_text,
                now,
            ),
        ).fetchone()[0]
        db.execute(
            """
            INSERT INTO copies(id,track_id,source_id,uri,content_digest,format,bitrate,
              sample_rate,channels,size,mtime_ns,artwork_uri,replaygain_track_gain,
              availability,last_seen)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id,uri) DO UPDATE SET
              track_id=excluded.track_id, content_digest=excluded.content_digest,
              format=excluded.format, bitrate=excluded.bitrate,
              sample_rate=excluded.sample_rate, channels=excluded.channels,
              size=excluded.size, mtime_ns=excluded.mtime_ns,
              artwork_uri=excluded.artwork_uri,
              replaygain_track_gain=excluded.replaygain_track_gain,
              availability=excluded.availability, last_seen=excluded.last_seen
            """,
            (
                copy_id,
                track_id,
                source_id,
                canonical_uri,
                metadata.content_digest,
                metadata.format,
                max(0, metadata.bitrate),
                max(0, metadata.sample_rate),
                max(0, metadata.channels),
                max(0, metadata.size),
                max(0, metadata.mtime_ns),
                metadata.artwork_uri,
                metadata.replaygain_track_gain,
                CopyAvailability.AVAILABLE.value,
                now,
            ),
        )
        # The structured, ordered credit: what the source structurally carried,
        # or else the single display value above.
        artists = [_named(name) for name in metadata.artists if _named(name)] or [artist]
        album_artists = [_named(name) for name in metadata.album_artists if _named(name)] or [album_artist]
        for table, names in (("track_artists", artists), ("track_album_artists", album_artists)):
            db.execute(f"DELETE FROM {table} WHERE track_id=?", (track_id,))
            db.executemany(
                f"INSERT INTO {table}(track_id,position,name) VALUES(?,?,?)",
                ((track_id, position, name) for position, name in enumerate(names)),
            )
        return track_id, copy_id

    def unchanged_copy(
        self, source_id: str, uri: str | Path, *, size: int, mtime_ns: int
    ) -> str | None:
        """Refresh reachability without re-reading unchanged media metadata."""
        canonical = _canonical_uri(uri)
        with self._lock:
            row = self._connection.execute(
                "SELECT id,size,mtime_ns FROM copies WHERE source_id=? AND uri=?",
                (source_id, canonical),
            ).fetchone()
            if row is None or row["size"] != size or row["mtime_ns"] != mtime_ns:
                return None
            self._connection.execute(
                "UPDATE copies SET availability=?,last_seen=unixepoch() WHERE id=?",
                (CopyAvailability.AVAILABLE.value, row["id"]),
            )
        return str(row["id"])

    def finish_source_scan(self, source_id: str, seen_copy_ids: set[str]) -> int:
        """Mark copies absent from a completed scan unavailable, retaining history."""
        with self.transaction() as db:
            rows = db.execute(
                "SELECT id FROM copies WHERE source_id=?", (source_id,)
            ).fetchall()
            missing = [row["id"] for row in rows if row["id"] not in seen_copy_ids]
            if missing:
                db.executemany(
                    "UPDATE copies SET availability=? WHERE id=?",
                    ((CopyAvailability.UNAVAILABLE.value, copy_id) for copy_id in missing),
                )
            now = self._now(db)
            db.execute(
                "UPDATE sources SET state=?, last_seen=? WHERE id=?",
                (SourceState.ONLINE.value, now, source_id),
            )
        return len(missing)

    def set_copy_availability(
        self, copy_id: str, availability: CopyAvailability
    ) -> None:
        with self.transaction() as db:
            result = db.execute(
                "UPDATE copies SET availability=? WHERE id=?",
                (availability.value, copy_id),
            )
            if result.rowcount != 1:
                raise KeyError(copy_id)

    def track(self, track_id: str) -> Track:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM tracks WHERE id=?", (track_id,)
            ).fetchone()
            if row is None:
                raise KeyError(track_id)
            return self._tracks_from_rows([row])[0]

    def _credits(self, track_ids: Sequence[str], table: str) -> dict[str, list[str]]:
        """Ordered credit names for many tracks. `table` is always one of the
        two literal credit table names, never caller input. A large listing
        reads the table once instead of binding thousands of parameters.
        Callers hold `self._lock`."""
        if not track_ids:
            return {}
        grouped: dict[str, list[str]] = {}
        if len(track_ids) > _PARAMETER_BATCH:
            wanted = set(track_ids)
            rows = self._connection.execute(
                f"SELECT track_id, name FROM {table} ORDER BY track_id, position"
            )
        else:
            wanted = None
            rows = self._connection.execute(
                f"SELECT track_id, name FROM {table} WHERE track_id IN ({','.join('?' * len(track_ids))}) "
                "ORDER BY track_id, position",
                list(track_ids),
            )
        for track_id, name in rows:
            if wanted is None or track_id in wanted:
                grouped.setdefault(track_id, []).append(name)
        return grouped

    def _tracks_from_rows(self, rows: Sequence[sqlite3.Row]) -> list[Track]:
        """Tracks with their credits. Callers hold `self._lock`."""
        ids = [row["id"] for row in rows]
        artists = self._credits(ids, "track_artists")
        album_artists = self._credits(ids, "track_album_artists")
        return [
            self._track_from_row(row, artists.get(row["id"], ()), album_artists.get(row["id"], ()))
            for row in rows
        ]

    def artists_for_track(self, track_id: str) -> list[str]:
        return list(self.track(track_id).artists)

    def album_artists_for_track(self, track_id: str) -> list[str]:
        return list(self.track(track_id).album_artists)

    @staticmethod
    def _track_from_row(
        row: sqlite3.Row, artists: Sequence[str] = (), album_artists: Sequence[str] = ()
    ) -> Track:
        return Track(
            id=row["id"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            album_artist=row["album_artist"],
            artists=tuple(artists) or (row["artist"],),
            album_artists=tuple(album_artists) or (row["album_artist"],),
            duration_ns=row["duration_ns"],
            disc_number=row["disc_number"],
            track_number=row["track_number"],
            year=row["year"],
            genre=row["genre"],
            added_at=row["added_at"],
            favorite=bool(row["favorite"]),
            play_count=row["play_count"],
        )

    def tracks(
        self,
        *,
        query: str = "",
        album: str | None = None,
        artist: str | None = None,
        source_id: str | None = None,
        recently_added: bool = False,
        limit: int | None = None,
        shown_only: bool = True,
    ) -> list[Track]:
        clauses: list[str] = []
        values: list[object] = []
        joins = ""
        if query.strip():
            clauses.append("t.search_text LIKE ? ESCAPE '\\'")
            needle = _normalized(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append(f"%{needle}%")
        if album is not None:
            clauses.append("t.album = ?")
            values.append(album)
        if artist is not None:
            clauses.append("(t.artist = ? OR t.album_artist = ?)")
            values.extend((artist, artist))
        if source_id is not None:
            joins = " JOIN copies filter_copy ON filter_copy.track_id=t.id"
            clauses.append("filter_copy.source_id = ?")
            values.append(source_id)
        order = "t.added_at DESC" if recently_added else "t.album_artist COLLATE NOCASE, t.album COLLATE NOCASE, t.disc_number, t.track_number, t.title COLLATE NOCASE"
        values.append(-1 if limit is None else max(1, limit))
        with self._lock:
            if shown_only and (shown := self._shown_clause()):
                clauses.append(shown)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self._connection.execute(
                f"SELECT DISTINCT t.* FROM tracks t{joins}{where} ORDER BY {order} LIMIT ?",
                values,
            ).fetchall()
            return self._tracks_from_rows(rows)

    def has_tracks(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1 FROM tracks LIMIT 1").fetchone() is not None

    def library_counts(self, *, shown_only: bool = True) -> dict[str, int]:
        with self._lock:
            shown = self._shown_clause() if shown_only else None
            where = f"WHERE {shown}" if shown else ""
            row = self._connection.execute(
                f"""
                SELECT COUNT(*) AS songs,
                       COUNT(DISTINCT album || char(0) || album_artist) AS albums,
                       COUNT(DISTINCT album_artist) AS artists
                  FROM tracks t {where}
                """
            ).fetchone()
        return {"songs": row["songs"], "albums": row["albums"], "artists": row["artists"]}

    def album_artwork(self, album: str, album_artist: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT MAX(c.artwork_uri) FROM tracks t JOIN copies c ON c.track_id=t.id
                 WHERE t.album=? AND t.album_artist=?
                """,
                (album, album_artist),
            ).fetchone()
        return row[0] if row else None

    def albums(self, query: str = "", *, artist: str | None = None) -> list[tuple[str, str, int, int, str | None]]:
        """Albums, alphabetically; or, for one artist, that artist's releases
        newest first -- albums they lead, and ones they are credited on."""
        values: list[object] = []
        clauses: list[str] = []
        if query.strip():
            clauses.append("t.search_text LIKE ?")
            values.append(f"%{_normalized(query)}%")
        if artist is not None:
            clauses.append(
                "(t.album_artist = ? COLLATE NOCASE OR EXISTS (SELECT 1 FROM tracks mine"
                " WHERE mine.album=t.album AND mine.album_artist=t.album_artist"
                " AND (mine.artist = ? COLLATE NOCASE OR EXISTS (SELECT 1 FROM track_artists credit"
                " WHERE credit.track_id=mine.id AND credit.name = ? COLLATE NOCASE))))"
            )
            values.extend((artist, artist, artist))
        order = ("MAX(t.year) DESC, t.album COLLATE NOCASE" if artist is not None
                 else "t.album_artist COLLATE NOCASE, t.album COLLATE NOCASE")
        with self._lock:
            if shown := self._shown_clause():
                clauses.append(shown)
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self._connection.execute(
                f"""
                SELECT t.album, t.album_artist, MAX(t.year) AS year, COUNT(DISTINCT t.id) AS tracks,
                       MAX(c.artwork_uri) AS artwork_uri
                  FROM tracks t LEFT JOIN copies c ON c.track_id=t.id
                  {where}
                 GROUP BY t.album, t.album_artist
                 ORDER BY {order}
                """,
                values,
            ).fetchall()
        return [
            (row["album"], row["album_artist"], row["year"], row["tracks"], row["artwork_uri"])
            for row in rows
        ]

    def top_tracks(self, artist: str, limit: int = 10) -> list[Track]:
        """An artist's most played songs; with no plays yet, their newest."""
        with self._lock:
            clauses = ["(t.artist = ? COLLATE NOCASE OR t.album_artist = ? COLLATE NOCASE"
                       " OR EXISTS (SELECT 1 FROM track_artists credit WHERE credit.track_id=t.id"
                       " AND credit.name = ? COLLATE NOCASE))"]
            if shown := self._shown_clause():
                clauses.append(shown)
            rows = self._connection.execute(
                f"""
                SELECT t.* FROM tracks t WHERE {' AND '.join(clauses)}
                 ORDER BY t.play_count DESC, t.favorite DESC, t.year DESC,
                          t.album COLLATE NOCASE, t.disc_number, t.track_number
                 LIMIT ?
                """,
                (artist, artist, artist, max(1, limit)),
            ).fetchall()
            return self._tracks_from_rows(rows)

    def artists(self) -> list[tuple[str, int]]:
        with self._lock:
            shown = self._shown_clause()
            where = f"WHERE {shown}" if shown else ""
            rows = self._connection.execute(
                f"""
                SELECT t.album_artist AS name, COUNT(*) AS tracks FROM tracks t {where}
                 GROUP BY t.album_artist ORDER BY t.album_artist COLLATE NOCASE
                """
            ).fetchall()
        return [(row["name"], row["tracks"]) for row in rows]

    def search(self, query: str) -> SearchResults:
        """Albums whose title or artist matches, artists whose name matches, and
        songs matching any of their text. Closer matches come first. Respects
        the source filter."""
        needle = _normalized(query)
        if not needle:
            return SearchResults(query, [], [], [])

        def rank(text: str) -> int:
            normalized = _normalized(text)
            return 0 if normalized.startswith(needle) else 1 if needle in normalized else 2

        albums = [album for album in self.albums()
                  if min(rank(album[0]), rank(album[1])) < 2]
        albums.sort(key=lambda album: min(rank(album[0]), rank(album[1]) + 1))
        artists = [artist for artist in self.artists() if rank(artist[0]) < 2]
        artists.sort(key=lambda artist: rank(artist[0]))
        songs = self.tracks(query=query)
        songs.sort(key=lambda track: rank(track.title))
        return SearchResults(query, albums, artists, songs)

    def copies_for_track(self, track_id: str) -> list[TrackCopy]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT c.*, s.name AS source_name, s.local AS source_local,
                       s.state AS source_state, o.state AS offline_state,
                       o.path AS offline_path
                  FROM copies c JOIN sources s ON s.id=c.source_id
                  LEFT JOIN offline_items o ON o.copy_id=c.id
                 WHERE c.track_id=?
                 ORDER BY s.local DESC, c.sample_rate DESC, c.bitrate DESC, s.name
                """,
                (track_id,),
            ).fetchall()
        return [
            TrackCopy(
                id=row["id"],
                track_id=row["track_id"],
                source_id=row["source_id"],
                uri=row["uri"],
                format=row["format"],
                bitrate=row["bitrate"],
                sample_rate=row["sample_rate"],
                channels=row["channels"],
                artwork_uri=row["artwork_uri"],
                availability=CopyAvailability(row["availability"]),
                source_name=row["source_name"],
                source_local=bool(row["source_local"]),
                source_state=SourceState(row["source_state"]),
                size=row["size"],
                offline_state=OfflineState(row["offline_state"]) if row["offline_state"] else None,
                offline_path=row["offline_path"],
            )
            for row in rows
        ]

    @staticmethod
    def reachable(copy: TrackCopy) -> bool:
        """A download plays without its server; any other copy needs its source."""
        return copy.offline_ready or (
            copy.availability is CopyAvailability.AVAILABLE
            and copy.source_state in (SourceState.ONLINE, SourceState.SYNCING)
        )

    def select_copy(self, track_id: str, explicit_copy_id: str | None = None) -> TrackCopy | None:
        copies = self.copies_for_track(track_id)
        reachable = self.reachable

        if explicit_copy_id:
            selected = next((item for item in copies if item.id == explicit_copy_id), None)
            if selected is not None and reachable(selected):
                return selected
        # Prefer what is already on this device: a local file, then a download,
        # then anything a reachable source can stream.
        return (
            next((item for item in copies if item.source_local and reachable(item)), None)
            or next((item for item in copies if item.offline_ready), None)
            or next((item for item in copies if reachable(item)), None)
        )

    def offline_candidates(self, track_ids: Iterable[str]) -> list[TrackCopy]:
        """The remote copy to download for each track not already on this device."""
        candidates: list[TrackCopy] = []
        for track_id in dict.fromkeys(track_ids):
            copies = self.copies_for_track(track_id)
            if any(item.source_local or item.offline_state is not None for item in copies):
                continue
            remote = next(
                (item for item in copies
                 if not item.source_local and item.availability is CopyAvailability.AVAILABLE),
                None,
            )
            if remote is not None:
                candidates.append(remote)
        return candidates

    def request_offline(self, copy_ids: Iterable[str]) -> int:
        """Queue downloads; a failed one is queued again, a finished one is kept."""
        requested = 0
        with self.transaction() as db:
            now = self._now(db)
            for copy_id in dict.fromkeys(copy_ids):
                row = db.execute(
                    "SELECT s.local FROM copies c JOIN sources s ON s.id=c.source_id WHERE c.id=?",
                    (copy_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(copy_id)
                if row["local"]:
                    raise ValueError("a local copy is already on this device")
                result = db.execute(
                    """
                    INSERT INTO offline_items(copy_id,state,requested_at) VALUES(?,?,?)
                    ON CONFLICT(copy_id) DO UPDATE SET state=excluded.state, error=NULL,
                      requested_at=excluded.requested_at
                     WHERE offline_items.state='failed'
                    """,
                    (copy_id, OfflineState.QUEUED.value, now),
                )
                requested += result.rowcount
        return requested

    def offline_items(
        self, *, states: Iterable[OfflineState] | None = None, source_id: str | None = None
    ) -> list[OfflineItem]:
        clauses: list[str] = []
        values: list[object] = []
        if states is not None:
            wanted = [state.value for state in states]
            clauses.append(f"o.state IN ({','.join('?' * len(wanted))})")
            values.extend(wanted)
        if source_id is not None:
            clauses.append("c.source_id=?")
            values.append(source_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT o.*, c.track_id, c.source_id, c.uri, c.format, c.size AS copy_size
                  FROM offline_items o JOIN copies c ON c.id=o.copy_id{where}
                 ORDER BY o.requested_at, o.copy_id
                """,
                values,
            ).fetchall()
        return [
            OfflineItem(
                copy_id=row["copy_id"],
                track_id=row["track_id"],
                source_id=row["source_id"],
                uri=row["uri"],
                format=row["format"],
                size=row["size"] or row["copy_size"],
                state=OfflineState(row["state"]),
                path=row["path"],
                error=row["error"],
            )
            for row in rows
        ]

    def _set_offline(self, copy_id: str, assignments: str, values: Sequence[object]) -> bool:
        with self.transaction() as db:
            result = db.execute(
                f"UPDATE offline_items SET {assignments} WHERE copy_id=?", (*values, copy_id)
            )
        return result.rowcount == 1

    def start_offline(self, copy_id: str) -> bool:
        """Claim a queued download; False when it was removed or already claimed."""
        with self.transaction() as db:
            result = db.execute(
                "UPDATE offline_items SET state=? WHERE copy_id=? AND state=?",
                (OfflineState.DOWNLOADING.value, copy_id, OfflineState.QUEUED.value),
            )
        return result.rowcount == 1

    def complete_offline(self, copy_id: str, path: str | Path, size: int) -> bool:
        return self._set_offline(
            copy_id,
            "state=?, path=?, size=?, error=NULL, completed_at=unixepoch()",
            (OfflineState.COMPLETE.value, str(path), max(0, size)),
        )

    def fail_offline(self, copy_id: str, error: str) -> bool:
        return self._set_offline(
            copy_id, "state=?, error=?", (OfflineState.FAILED.value, error[:300])
        )

    def requeue_offline(self, copy_id: str) -> bool:
        """A download interrupted by a lost connection waits to start again."""
        with self.transaction() as db:
            result = db.execute(
                "UPDATE offline_items SET state=? WHERE copy_id=? AND state=?",
                (OfflineState.QUEUED.value, copy_id, OfflineState.DOWNLOADING.value),
            )
        return result.rowcount == 1

    def requeue_interrupted_offline(self) -> int:
        """A download that was running when Tide stopped starts again."""
        with self.transaction() as db:
            result = db.execute(
                "UPDATE offline_items SET state=? WHERE state=?",
                (OfflineState.QUEUED.value, OfflineState.DOWNLOADING.value),
            )
        return result.rowcount

    def remove_offline(self, track_ids: Iterable[str]) -> list[str]:
        """Forget the downloads of these tracks; returns files for the caller to delete."""
        paths: list[str] = []
        with self.transaction() as db:
            for track_id in dict.fromkeys(track_ids):
                rows = db.execute(
                    """
                    SELECT o.copy_id, o.path FROM offline_items o JOIN copies c ON c.id=o.copy_id
                     WHERE c.track_id=?
                    """,
                    (track_id,),
                ).fetchall()
                for row in rows:
                    if row["path"]:
                        paths.append(row["path"])
                    db.execute("DELETE FROM offline_items WHERE copy_id=?", (row["copy_id"],))
        return paths

    def offline_summary(self, source_id: str | None = None) -> OfflineSummary:
        where = "WHERE c.source_id=?" if source_id is not None else ""
        values = (source_id,) if source_id is not None else ()
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT
                  COALESCE(SUM(o.state='complete'),0) AS complete,
                  COALESCE(SUM(o.state IN ('queued','downloading')),0) AS pending,
                  COALESCE(SUM(o.state='failed'),0) AS failed,
                  COALESCE(SUM(CASE WHEN o.state='complete' THEN o.size ELSE 0 END),0) AS bytes
                  FROM offline_items o JOIN copies c ON c.id=o.copy_id {where}
                """,
                values,
            ).fetchone()
        return OfflineSummary(row["complete"], row["pending"], row["failed"], row["bytes"])

    def set_favorite(self, track_id: str, favorite: bool) -> None:
        with self.transaction() as db:
            result = db.execute(
                "UPDATE tracks SET favorite=? WHERE id=?", (int(favorite), track_id)
            )
            if result.rowcount != 1:
                raise KeyError(track_id)

    def increment_play_count(self, track_id: str) -> None:
        with self.transaction() as db:
            result = db.execute(
                "UPDATE tracks SET play_count=play_count+1 WHERE id=?", (track_id,)
            )
            if result.rowcount != 1:
                raise KeyError(track_id)

    def create_playlist(self, name: str) -> str:
        clean = name.strip()
        if not clean:
            raise ValueError("playlist name cannot be empty")
        playlist_id = str(uuid.uuid4())
        with self.transaction() as db:
            now = self._now(db)
            db.execute(
                "INSERT INTO playlists(id,name,created_at,updated_at) VALUES(?,?,?,?)",
                (playlist_id, clean, now, now),
            )
        return playlist_id

    def playlists(self) -> list[tuple[str, str, int]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT p.id,p.name,COUNT(i.track_id) AS tracks
                  FROM playlists p LEFT JOIN playlist_items i ON i.playlist_id=p.id
                 GROUP BY p.id ORDER BY p.name COLLATE NOCASE
                """
            ).fetchall()
        return [(row["id"], row["name"], row["tracks"]) for row in rows]

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        clean = name.strip()
        if not clean:
            raise ValueError("playlist name cannot be empty")
        with self.transaction() as db:
            result = db.execute(
                "UPDATE playlists SET name=?,updated_at=unixepoch() WHERE id=?",
                (clean, playlist_id),
            )
            if result.rowcount != 1:
                raise KeyError(playlist_id)

    def delete_playlist(self, playlist_id: str) -> None:
        with self.transaction() as db:
            result = db.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
            if result.rowcount != 1:
                raise KeyError(playlist_id)

    def set_playlist_tracks(self, playlist_id: str, track_ids: Sequence[str]) -> None:
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM playlists WHERE id=?", (playlist_id,)).fetchone() is None:
                raise KeyError(playlist_id)
            db.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
            db.executemany(
                "INSERT INTO playlist_items(playlist_id,position,track_id) VALUES(?,?,?)",
                ((playlist_id, position, track_id) for position, track_id in enumerate(track_ids)),
            )
            db.execute(
                "UPDATE playlists SET updated_at=unixepoch() WHERE id=?", (playlist_id,)
            )

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT t.* FROM playlist_items i JOIN tracks t ON t.id=i.track_id
                 WHERE i.playlist_id=? ORDER BY i.position
                """,
                (playlist_id,),
            ).fetchall()
            return self._tracks_from_rows(rows)

    def move_playlist_item(self, playlist_id: str, old: int, new: int) -> None:
        tracks = [track.id for track in self.playlist_tracks(playlist_id)]
        if old < 0 or old >= len(tracks) or new < 0 or new >= len(tracks):
            raise IndexError("playlist position out of range")
        track_id = tracks.pop(old)
        tracks.insert(new, track_id)
        self.set_playlist_tracks(playlist_id, tracks)

    def replace_queue(
        self,
        track_ids: Sequence[str],
        *,
        current_position: int = 0,
        selected_copy_ids: Sequence[str | None] | None = None,
    ) -> None:
        if selected_copy_ids is not None and len(selected_copy_ids) != len(track_ids):
            raise ValueError("copy selection count must match queue length")
        copies = selected_copy_ids or [None] * len(track_ids)
        self.queue_revision += 1
        with self.transaction() as db:
            db.execute("DELETE FROM queue")
            now = self._now(db)
            db.executemany(
                "INSERT INTO queue(position,track_id,selected_copy_id,added_at) VALUES(?,?,?,?)",
                (
                    (position, track_id, copies[position], now)
                    for position, track_id in enumerate(track_ids)
                ),
            )
            bounded = min(max(0, current_position), max(0, len(track_ids) - 1))
            db.execute(
                "UPDATE session SET current_position=?,position_ns=0,playback_state='stopped' WHERE singleton=1",
                (bounded,),
            )

    def append_queue(self, track_ids: Iterable[str]) -> None:
        self.queue_revision += 1
        with self.transaction() as db:
            position = int(db.execute("SELECT COALESCE(MAX(position)+1,0) FROM queue").fetchone()[0])
            now = self._now(db)
            for track_id in track_ids:
                db.execute(
                    "INSERT INTO queue(position,track_id,added_at) VALUES(?,?,?)",
                    (position, track_id, now),
                )
                position += 1

    def queue(self) -> list[QueueEntry]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT q.position,q.selected_copy_id,t.*
                  FROM queue q JOIN tracks t ON t.id=q.track_id ORDER BY q.position
                """
            ).fetchall()
            tracks = self._tracks_from_rows(rows)
        return [
            QueueEntry(row["position"], track, row["selected_copy_id"])
            for row, track in zip(rows, tracks)
        ]

    def queue_length(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT COUNT(*) FROM queue").fetchone()[0])

    def queue_slice(self, start: int, count: int) -> list[QueueEntry]:
        """Queue entries from `start` onward, at most `count` of them."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT q.position,q.selected_copy_id,t.*
                  FROM queue q JOIN tracks t ON t.id=q.track_id
                 WHERE q.position >= ? ORDER BY q.position LIMIT ?
                """,
                (max(0, start), max(0, count)),
            ).fetchall()
            tracks = self._tracks_from_rows(rows)
        return [
            QueueEntry(row["position"], track, row["selected_copy_id"])
            for row, track in zip(rows, tracks)
        ]

    def move_queue_item(self, old: int, new: int) -> None:
        entries = self.queue()
        if old < 0 or old >= len(entries) or new < 0 or new >= len(entries):
            raise IndexError("queue position out of range")
        item = entries.pop(old)
        entries.insert(new, item)
        state = self.session_state()
        self.replace_queue(
            [entry.track.id for entry in entries],
            current_position=min(state.current_position, len(entries) - 1),
            selected_copy_ids=[entry.selected_copy_id for entry in entries],
        )

    def remove_queue_item(self, position: int) -> None:
        entries = self.queue()
        if position < 0 or position >= len(entries):
            raise IndexError("queue position out of range")
        entries.pop(position)
        state = self.session_state()
        self.replace_queue(
            [entry.track.id for entry in entries],
            current_position=min(state.current_position, max(0, len(entries) - 1)),
            selected_copy_ids=[entry.selected_copy_id for entry in entries],
        )

    def set_queue_copy(self, position: int, copy_id: str | None) -> None:
        self.queue_revision += 1
        with self.transaction() as db:
            result = db.execute(
                "UPDATE queue SET selected_copy_id=? WHERE position=?", (copy_id, position)
            )
            if result.rowcount != 1:
                raise IndexError(position)

    def session_state(self) -> SessionState:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM session WHERE singleton=1"
            ).fetchone()
        return SessionState(
            current_position=row["current_position"],
            position_ns=row["position_ns"],
            shuffle=bool(row["shuffle"]),
            repeat=RepeatMode(row["repeat_mode"]),
            volume=float(row["volume"]),
            playback_state=row["playback_state"],
        )

    def update_session(
        self,
        *,
        current_position: int | None = None,
        position_ns: int | None = None,
        shuffle: bool | None = None,
        repeat: RepeatMode | None = None,
        volume: float | None = None,
        playback_state: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[object] = []
        for column, value in (
            ("current_position", current_position),
            ("position_ns", position_ns),
            ("shuffle", None if shuffle is None else int(shuffle)),
            ("repeat_mode", None if repeat is None else repeat.value),
            ("volume", None if volume is None else min(1.0, max(0.0, volume))),
            ("playback_state", playback_state),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(value)
        if not assignments:
            return
        with self.transaction() as db:
            db.execute(
                f"UPDATE session SET {','.join(assignments)} WHERE singleton=1", values
            )
