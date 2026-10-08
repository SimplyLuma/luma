# SPDX-License-Identifier: Apache-2.0

"""Persistent, source-aware media library for Luma Photos.

The database is authoritative for user organization and last-known source
state. Thumbnails, decoded previews, and hashes are rebuildable caches. File
enumeration is deliberately synchronous at this layer so callers can run it
in a bounded worker; the GTK application never calls it on the main thread.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
from urllib.parse import quote, unquote, urlparse
import uuid


APPLICATION_ID = "org.projectluma.Photos"
SCHEMA_VERSION = 2
IMAGE_MIME_PREFIX = "image/"
VIDEO_MIME_PREFIX = "video/"
IMAGE_SUFFIXES = frozenset(
    (".avif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".webp")
)
VIDEO_SUFFIXES = frozenset(
    (".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm")
)
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_datetime(value: str | None, fallback: float = 0) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback, timezone.utc)


def _xdg_path(environment: Mapping[str, str], variable: str, default: str) -> Path:
    value = environment.get(variable, "").strip()
    if value:
        return Path(value).expanduser()
    return Path(environment.get("HOME", str(Path.home()))) / default


def pictures_directory(environment: Mapping[str, str] | None = None) -> Path:
    from .user_directories import photos_directory
    return photos_directory(environment)


def _shared_library_mounted(directory: Path, mounts: str | None = None) -> bool:
    """The family's explicit Flatpak bind must exist before opening SQLite.

    An ungranted path in the sandbox's synthetic home can be writable tmpfs.
    Creating a new catalog there would look like the user's library was lost.
    mountinfo describes bind mounts even when parent/child share a filesystem.
    """
    import re
    text = Path('/proc/self/mountinfo').read_text() if mounts is None else mounts
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        target = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4])
        if target == str(directory):
            return True
    return False


def default_database_path(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    # Camera and Photos own one media family catalog. Flatpak rewrites the
    # app's XDG_DATA_HOME, but its supported HOST_XDG_DATA_HOME retains the
    # host location exposed by the narrow xdg-data/luma-photos permission.
    # Never copy the live WAL catalog into two independently updated profiles.
    if env.get("FLATPAK_ID") in {"org.projectluma.Photos", "org.projectluma.Camera"}:
        root = env.get("HOST_XDG_DATA_HOME", "").strip()
        data = Path(root) if root else Path(env.get("HOME", str(Path.home()))) / ".local/share"
        if not data.is_absolute():
            raise ValueError("The shared Photos library requires an absolute data directory.")
        if environment is None and not _shared_library_mounted(data / 'luma-photos'):
            raise PermissionError("Allow access to the shared Photos library to use Camera or Photos. Your existing library is preserved.")
        return data / "luma-photos/library.sqlite3"
    return (
        _xdg_path(env, "XDG_DATA_HOME", ".local/share")
        / "luma-photos"
        / "library.sqlite3"
    )


def default_thumbnail_root(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    return _xdg_path(env, "XDG_CACHE_HOME", ".cache") / "thumbnails"


@dataclass(frozen=True)
class SourceRecord:
    id: str
    name: str
    root: Path
    kind: str
    reachable: bool
    last_seen: datetime | None
    asset_count: int = 0


@dataclass(frozen=True)
class MediaCopy:
    id: str
    asset_id: str
    source_id: str
    path: Path
    uri: str
    reachable: bool
    bytes: int
    modified: datetime
    mime_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    content_hash: str | None = None
    trashed: bool = False
    deleted_at: datetime | None = None
    original_path: Path | None = None


@dataclass(frozen=True)
class PhotoRecord:
    """One logical asset with its known physical copies."""

    path: Path
    modified: datetime
    bytes: int
    id: str = ""
    display_name: str = ""
    mime_type: str = "image/jpeg"
    captured: datetime | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    favorite: bool = False
    caption: str = ""
    place: str = ""
    edited: bool = False
    deleted: bool = False
    copies: tuple[MediaCopy, ...] = ()

    @property
    def available(self) -> bool:
        if self.copies:
            return any(copy.reachable for copy in self.copies)
        return self.path.is_file()

    @property
    def is_video(self) -> bool:
        return self.mime_type.startswith(VIDEO_MIME_PREFIX)

    @property
    def at_risk(self) -> bool:
        return len(self.copies) == 1

    @property
    def reachable_copy_count(self) -> int:
        return sum(copy.reachable for copy in self.copies)


@dataclass(frozen=True)
class AlbumRecord:
    id: str
    name: str
    created: datetime
    asset_count: int


@dataclass(frozen=True)
class ScanResult:
    source_id: str
    discovered: int
    added: int
    updated: int
    unavailable: int
    unsupported: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferResult:
    completed: tuple[Path, ...]
    skipped_duplicates: int = 0
    unsupported: int = 0
    errors: tuple[str, ...] = ()


def _source_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, root.resolve(strict=False).as_uri()))


def _file_key(stat: os.stat_result) -> str:
    return f"{stat.st_dev}:{stat.st_ino}"


def _path_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"unsupported source URI: {parsed.scheme}")
    return Path(unquote(parsed.path))


def _unique_destination(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 2
    while True:
        candidate = directory / f"{stem} {index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".luma-import-", suffix=".partial", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(
            descriptor, "wb", closefd=True
        ) as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _walk_files(
    root: Path, cancelled: Callable[[], bool] | None = None
) -> Iterable[Path]:
    stack = [root]
    while stack:
        if cancelled and cancelled():
            return
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if cancelled and cancelled():
                return
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            except OSError:
                continue


def _sniff_mime(path: Path) -> str | None:
    """Recognize supported media by content, with a conservative MIME fallback."""

    try:
        with path.open("rb") as stream:
            head = stream.read(64)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:16]
        if any(value in brand for value in (b"avif", b"avis")):
            return "image/avif"
        if any(
            value in brand
            for value in (b"heic", b"heix", b"hevc", b"mif1", b"msf1")
        ):
            return "image/heif"
        return "video/mp4"
    return None


def _image_dimensions(
    path: Path, mime_type: str
) -> tuple[int | None, int | None]:
    """Read cheap dimensions for common formats without decoding pixels."""

    try:
        with path.open("rb") as stream:
            if mime_type == "image/png":
                stream.seek(16)
                return struct.unpack(">II", stream.read(8))
            if mime_type == "image/gif":
                stream.seek(6)
                return struct.unpack("<HH", stream.read(4))
            if mime_type == "image/webp":
                stream.seek(12)
                chunk = stream.read(16)
                if chunk[:4] == b"VP8X" and len(chunk) >= 14:
                    width = 1 + int.from_bytes(chunk[8:11], "little")
                    height = 1 + int.from_bytes(chunk[11:14], "little")
                    return width, height
            if mime_type == "image/jpeg":
                stream.seek(2)
                while True:
                    marker = stream.read(2)
                    if len(marker) != 2 or marker[0] != 0xFF:
                        break
                    while marker[1] == 0xFF:
                        byte = stream.read(1)
                        if not byte:
                            return None, None
                        marker = bytes((0xFF, byte[0]))
                    if marker[1] in (0xD8, 0xD9):
                        continue
                    length_data = stream.read(2)
                    if len(length_data) != 2:
                        break
                    length = int.from_bytes(length_data, "big")
                    if marker[1] in {
                        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                    }:
                        data = stream.read(5)
                        if len(data) == 5:
                            return (
                                int.from_bytes(data[3:5], "big"),
                                int.from_bytes(data[1:3], "big"),
                            )
                        break
                    stream.seek(max(0, length - 2), os.SEEK_CUR)
    except (OSError, ValueError, IndexError, struct.error):
        pass
    return None, None


class PhotoLibrary:
    """Transactional Photos catalog with migration-safe schema ownership."""

    def __init__(self, database: Path | None = None) -> None:
        self.database = default_database_path() if database is None else Path(database)
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.database.parent.chmod(0o700)
        except OSError:
            pass
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    root_uri TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK(kind IN ('local','removable','remote')),
                    reachable INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT,
                    last_scan TEXT
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),
                    mime_type TEXT NOT NULL,
                    captured_at TEXT,
                    modified_at TEXT NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    duration_ms INTEGER,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    caption TEXT NOT NULL DEFAULT '',
                    place TEXT NOT NULL DEFAULT '',
                    edited INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS copies (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    uri TEXT NOT NULL,
                    file_key TEXT,
                    bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    reachable INTEGER NOT NULL DEFAULT 1,
                    trashed INTEGER NOT NULL DEFAULT 0,
                    original_uri TEXT,
                    trash_info_uri TEXT,
                    deleted_at TEXT,
                    content_hash TEXT,
                    last_seen TEXT NOT NULL,
                    UNIQUE(source_id, uri)
                );
                CREATE INDEX IF NOT EXISTS copies_asset_idx ON copies(asset_id);
                CREATE INDEX IF NOT EXISTS assets_moment_idx
                    ON assets(COALESCE(captured_at,modified_at) DESC,id);
                CREATE INDEX IF NOT EXISTS copies_file_key_idx
                    ON copies(source_id, file_key);
                CREATE TABLE IF NOT EXISTS albums (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS album_assets (
                    album_id TEXT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(album_id, asset_id)
                );
                """
            )
            copy_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(copies)")
            }
            for name, declaration in (
                ("original_uri", "TEXT"),
                ("trash_info_uri", "TEXT"),
                ("deleted_at", "TEXT"),
            ):
                if name not in copy_columns:
                    connection.execute(
                        f"ALTER TABLE copies ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS copies_asset_state_idx ON copies(asset_id,trashed)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS copies_trashed_idx "
                "ON copies(trashed,deleted_at)"
            )
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def add_source(
        self, root: Path, name: str | None = None, kind: str = "local"
    ) -> SourceRecord:
        if kind not in {"local", "removable", "remote"}:
            raise ValueError("unsupported source kind")
        path = Path(root).expanduser().resolve(strict=False)
        source_id = _source_id(path)
        with self._connect() as connection:
            for existing in connection.execute("SELECT id,root_uri FROM sources"):
                if _path_from_uri(existing["root_uri"]).resolve(strict=False) == path:
                    source_id = existing["id"]
                    break
        display_name = name or (
            "Photos"
            if path == pictures_directory().resolve(strict=False)
            else path.name or str(path)
        )
        reachable = path.is_dir()
        now = _utc_now() if reachable else None
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO sources(id,name,root_uri,kind,reachable,last_seen)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                     root_uri=excluded.root_uri,kind=excluded.kind,
                     reachable=excluded.reachable,
                     last_seen=COALESCE(excluded.last_seen,sources.last_seen)""",
                (source_id, display_name, path.as_uri(), kind, reachable, now),
            )
        return self.source(source_id)

    def ensure_default_source(self) -> SourceRecord:
        return self.add_source(pictures_directory(), "Photos", "local")

    def source(self, source_id: str) -> SourceRecord:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT s.*, COUNT(DISTINCT c.asset_id) AS asset_count
                   FROM sources s LEFT JOIN copies c
                     ON c.source_id=s.id AND c.trashed=0
                   WHERE s.id=? GROUP BY s.id""",
                (source_id,),
            ).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._source_from_row(row)

    def sources(self) -> tuple[SourceRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT s.*, COUNT(DISTINCT c.asset_id) AS asset_count
                   FROM sources s LEFT JOIN copies c
                     ON c.source_id=s.id AND c.trashed=0
                   GROUP BY s.id ORDER BY s.kind, s.name COLLATE NOCASE"""
            ).fetchall()
        return tuple(self._source_from_row(row) for row in rows)

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            id=row["id"],
            name=row["name"],
            root=_path_from_uri(row["root_uri"]),
            kind=row["kind"],
            reachable=bool(row["reachable"]),
            last_seen=(
                _as_datetime(row["last_seen"]) if row["last_seen"] else None
            ),
            asset_count=row["asset_count"],
        )

    def scan_source(
        self, source_id: str, cancelled: Callable[[], bool] | None = None
    ) -> ScanResult:
        source = self.source(source_id)
        root = source.root
        if not root.is_dir():
            with self._connect() as connection:
                connection.execute(
                    "UPDATE sources SET reachable=0,last_scan=? WHERE id=?",
                    (_utc_now(), source_id),
                )
                unavailable = connection.execute(
                    "UPDATE copies SET reachable=0 WHERE source_id=? AND trashed=0",
                    (source_id,),
                ).rowcount
            return ScanResult(source_id, 0, 0, 0, unavailable, 0)

        now = _utc_now()
        discovered = added = updated = unsupported = 0
        errors: list[str] = []
        seen: set[str] = set()
        with self._connect() as connection:
            connection.execute(
                "UPDATE sources SET reachable=1,last_seen=?,last_scan=? WHERE id=?",
                (now, now, source_id),
            )
            connection.commit()
            # Directory I/O must never hold the catalog's write lock. Each
            # changed asset/copy pair is atomic; unchanged copies need no writes.
            for path in _walk_files(root, cancelled):
                if cancelled and cancelled():
                    break
                try:
                    mime_type = _sniff_mime(path)
                    if mime_type is None:
                        if path.suffix.casefold() in MEDIA_SUFFIXES:
                            unsupported += 1
                        continue
                    stat = path.stat(follow_symlinks=False)
                    uri = path.resolve(strict=False).as_uri()
                    seen.add(uri)
                    discovered += 1
                    file_key = _file_key(stat)
                    existing = connection.execute(
                        """SELECT id,asset_id,uri,modified_ns,bytes,reachable,trashed FROM copies
                           WHERE source_id=? AND (file_key=? OR uri=?)
                           ORDER BY file_key=? DESC LIMIT 1""",
                        (source_id, file_key, uri, file_key),
                    ).fetchone()
                    if (existing is not None and existing["uri"] == uri
                            and existing["modified_ns"] == stat.st_mtime_ns
                            and existing["bytes"] == stat.st_size
                            and existing["reachable"] and not existing["trashed"]):
                        continue
                    width, height = (
                        _image_dimensions(path, mime_type)
                        if mime_type.startswith(IMAGE_MIME_PREFIX)
                        else (None, None)
                    )
                    modified = datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).isoformat()
                    media_type = (
                        "video"
                        if mime_type.startswith(VIDEO_MIME_PREFIX)
                        else "image"
                    )
                    if existing is None:
                        asset_id = str(uuid.uuid4())
                        copy_id = str(uuid.uuid4())
                        connection.execute(
                            """INSERT INTO assets(
                               id,display_name,media_type,mime_type,captured_at,
                               modified_at,width,height,created_at
                               ) VALUES(?,?,?,?,?,?,?,?,?)""",
                            (
                                asset_id, path.name, media_type, mime_type,
                                modified, modified, width, height, now,
                            ),
                        )
                        connection.execute(
                            """INSERT INTO copies(
                               id,asset_id,source_id,uri,file_key,bytes,
                               modified_ns,reachable,trashed,last_seen
                               ) VALUES(?,?,?,?,?,?,?,1,0,?)""",
                            (
                                copy_id, asset_id, source_id, uri, file_key,
                                stat.st_size, stat.st_mtime_ns, now,
                            ),
                        )
                        added += 1
                    else:
                        connection.execute(
                            """UPDATE copies SET uri=?,file_key=?,bytes=?,
                               modified_ns=?,reachable=1,trashed=0,last_seen=?
                               WHERE id=?""",
                            (
                                uri, file_key, stat.st_size, stat.st_mtime_ns,
                                now, existing["id"],
                            ),
                        )
                        connection.execute(
                            """UPDATE assets SET display_name=?,mime_type=?,
                               media_type=?,modified_at=?,width=COALESCE(?,width),
                               height=COALESCE(?,height) WHERE id=?""",
                            (
                                path.name, mime_type, media_type, modified,
                                width, height, existing["asset_id"],
                            ),
                        )
                        updated += 1
                    connection.commit()
                except (OSError, sqlite3.Error) as error:
                    connection.rollback()
                    errors.append(f"{path.name}: {error}")
            known = connection.execute(
                "SELECT id,uri FROM copies WHERE source_id=? AND trashed=0",
                (source_id,),
            ).fetchall()
            was_cancelled = bool(cancelled and cancelled())
            missing_ids = (
                []
                if was_cancelled
                else [row["id"] for row in known if row["uri"] not in seen]
            )
            if missing_ids:
                connection.executemany(
                    "UPDATE copies SET reachable=0 WHERE id=?",
                    ((item,) for item in missing_ids),
                )
            unavailable = len(missing_ids)
        return ScanResult(
            source_id, discovered, added, updated, unavailable, unsupported,
            tuple(errors),
        )

    def scan_all(
        self, cancelled: Callable[[], bool] | None = None
    ) -> tuple[ScanResult, ...]:
        sources = self.sources() or (self.ensure_default_source(),)
        results = []
        for source in sources:
            if cancelled and cancelled():
                break
            results.append(self.scan_source(source.id, cancelled))
        return tuple(results)

    def page_offset_for_path(self, path: Path, limit: int = 200) -> int | None:
        """Locate an externally opened photo without loading preceding records."""
        uri = Path(path).resolve(strict=False).as_uri()
        with self._connect() as connection:
            target = connection.execute(
                "SELECT a.id, COALESCE(a.captured_at,a.modified_at) AS moment FROM assets a "
                "JOIN copies c ON c.asset_id=a.id WHERE c.uri=? AND c.trashed=0 LIMIT 1", (uri,)
            ).fetchone()
            if target is None:
                return None
            count = connection.execute(
                "SELECT COUNT(*) FROM assets a WHERE "
                "(COALESCE(a.captured_at,a.modified_at)>? OR "
                "(COALESCE(a.captured_at,a.modified_at)=? AND a.id<?)) "
                "AND EXISTS (SELECT 1 FROM copies c WHERE c.asset_id=a.id AND c.trashed=0)",
                (target["moment"], target["moment"], target["id"]),
            ).fetchone()[0]
        return (count // limit) * limit

    def collection_counts(self) -> dict[str, int]:
        """Count in SQLite without constructing photos or their copies."""
        with self._connect() as connection:
            row = connection.execute("""
                SELECT COUNT(*) AS all_count,
                       COALESCE(SUM(favorite != 0),0) AS favorites,
                       COALESCE(SUM(media_type='video'),0) AS videos,
                       COALESCE(SUM(edited != 0),0) AS edited,
                       COALESCE(SUM(captured_at IS NULL OR julianday('now')-julianday(captured_at)<31),0) AS recent
                FROM assets a WHERE EXISTS (
                    SELECT 1 FROM copies c WHERE c.asset_id=a.id AND c.trashed=0)
            """).fetchone()
            deleted = connection.execute(
                "SELECT COUNT(DISTINCT asset_id) FROM copies WHERE trashed=1"
            ).fetchone()[0]
        return {"all": row["all_count"], "recent": row["recent"],
                "favorites": row["favorites"], "videos": row["videos"],
                "edited": row["edited"], "deleted": deleted}

    def page(
        self, *, query: str = "", collection: str = "all",
        source_id: str | None = None, album_id: str | None = None,
        offset: int = 0, limit: int = 200,
        cancelled: Callable[[], bool] | None = None,
        summary: dict | None = None,
    ) -> tuple[tuple[PhotoRecord, ...], int]:
        """Read a bounded page; filter the full catalog before applying its limit.

        Call from a worker. Search is metadata search, never image recognition.
        The progress handler interrupts obsolete searches instead of queuing them.
        """
        if limit < 1 or limit > 500 or offset < 0:
            raise ValueError("Invalid photo page bounds")
        trashed = int(collection == "deleted")
        predicates = ["EXISTS (SELECT 1 FROM copies c WHERE c.asset_id=a.id AND c.trashed=?)"]
        parameters: list[object] = [trashed]
        collection_filters = {
            "favorites": "a.favorite != 0", "videos": "a.media_type='video'",
            "edited": "a.edited != 0",
            "recent": "(a.captured_at IS NULL OR julianday('now')-julianday(a.captured_at)<31)",
        }
        if collection in collection_filters:
            predicates.append(collection_filters[collection])
        if source_id:
            predicates.append("EXISTS (SELECT 1 FROM copies c WHERE c.asset_id=a.id AND c.source_id=? AND c.trashed=?)")
            parameters.extend((source_id, trashed))
        if album_id:
            predicates.append("EXISTS (SELECT 1 FROM album_assets aa WHERE aa.asset_id=a.id AND aa.album_id=?)")
            parameters.append(album_id)
        terms = tuple(term.casefold() for term in query.split() if term)
        search_join = ""
        if terms:
            # Preaggregate album names once; avoid a scan of every membership
            # for each photograph when searching a large catalog.
            search_join = """LEFT JOIN (
                SELECT aa.asset_id, GROUP_CONCAT(al.name, ' ') AS names
                FROM album_assets aa JOIN albums al ON al.id=aa.album_id
                GROUP BY aa.asset_id) album_search ON album_search.asset_id=a.id"""
            haystack = """photo_casefold(
                a.display_name || ' ' || a.mime_type || ' ' || a.media_type || ' ' ||
                a.caption || ' ' || a.place || ' ' || photo_dates(COALESCE(a.captured_at,a.modified_at)) || ' ' ||
                COALESCE(album_search.names,'') || ' ' ||
                CASE WHEN a.favorite THEN 'favorite favourite favourites starred ' ELSE '' END ||
                CASE WHEN a.edited THEN 'edited derivative ' ELSE 'original ' END ||
                CASE WHEN a.media_type='video' THEN 'videos ' ELSE 'photos photographs pictures ' END ||
                COALESCE((SELECT GROUP_CONCAT(s.name || ' ' || s.root_uri || ' ' || s.kind || ' ' ||
                    photo_path(c.uri) || ' ' || CASE WHEN c.reachable THEN 'available in reach reachable'
                    ELSE 'unavailable out of reach offline' END, ' ')
                    FROM copies c JOIN sources s ON s.id=c.source_id
                    WHERE c.asset_id=a.id AND c.trashed=""" + str(trashed) + "),''))"
            predicates.append("photo_matches(" + haystack + ")")
        where = " AND ".join(predicates)
        order = ("(SELECT MAX(c.deleted_at) FROM copies c WHERE c.asset_id=a.id AND c.trashed=1)"
                 if trashed else "COALESCE(a.captured_at,a.modified_at)")
        with self._connect() as connection:
            if cancelled:
                connection.set_progress_handler(lambda: int(cancelled()), 1000)
            connection.create_function("photo_casefold", 1, lambda text: text.casefold(), deterministic=True)
            connection.create_function("photo_matches", 1,
                lambda text: int(all(term in text for term in terms)), deterministic=True)
            connection.create_function("photo_path", 1, lambda uri: str(_path_from_uri(uri)), deterministic=True)
            connection.create_function("photo_dates", 1,
                lambda value: value + " " + _as_datetime(value).strftime("%Y %B %b %A %a %-d %m %d"),
                deterministic=True)
            facts=connection.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT NULLIF(a.place,'')), MAX(COALESCE(a.captured_at,a.modified_at)) FROM assets a {search_join} WHERE {where}" if summary is not None else f"SELECT COUNT(*) FROM assets a {search_join} WHERE {where}",parameters
            ).fetchone()
            total=facts[0]
            if summary is not None:
                years=()
                if summary.get('include_years',True):
                    years=connection.execute(
                        f"SELECT substr(COALESCE(a.captured_at,a.modified_at),1,4) AS year,COUNT(*) AS count,MIN(a.id) AS photo FROM assets a {search_join} WHERE {where} GROUP BY year ORDER BY year DESC",parameters
                    ).fetchall()
                summary.update(places=facts[1],latest=facts[2],years=tuple(dict(row) for row in years))
            rows = connection.execute(
                f"SELECT a.id FROM assets a {search_join} WHERE {where} ORDER BY {order} DESC, a.id LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
        if cancelled and cancelled():
            raise InterruptedError("Photo query superseded")
        records = self.assets(collection=collection, asset_ids=tuple(row["id"] for row in rows))
        return records, total

    def assets(
        self,
        *,
        query: str = "",
        collection: str = "all",
        source_id: str | None = None,
        album_id: str | None = None,
        asset_ids: tuple[str, ...] | None = None,
    ) -> tuple[PhotoRecord, ...]:
        if asset_ids == ():
            return ()
        asset_filter = "" if asset_ids is None else " AND a.id IN (" + ",".join("?" for _ in asset_ids) + ")"
        copy_filter = "" if asset_ids is None else " AND c.asset_id IN (" + ",".join("?" for _ in asset_ids) + ")"
        id_parameters = asset_ids or ()
        deleted = collection == "deleted"
        trash_state = 1 if deleted else 0
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT a.* FROM assets a
                   WHERE EXISTS(
                     SELECT 1 FROM copies c
                     WHERE c.asset_id=a.id AND c.trashed=?
                   ) {asset_filter}
                   ORDER BY CASE WHEN ? THEN (
                     SELECT MAX(c.deleted_at) FROM copies c
                     WHERE c.asset_id=a.id AND c.trashed=1
                   ) ELSE COALESCE(a.captured_at,a.modified_at) END DESC, a.id""",
                (trash_state, *id_parameters, deleted),
            ).fetchall()
            copy_rows = connection.execute(
                f"""SELECT c.*,a.mime_type,a.width,a.height,a.duration_ms
                   FROM copies c JOIN assets a ON a.id=c.asset_id
                   WHERE c.trashed=? {copy_filter}
                   ORDER BY c.reachable DESC,c.modified_ns DESC""",
                (trash_state, *id_parameters),
            ).fetchall()
            memberships = (
                {
                    row["asset_id"]
                    for row in connection.execute(
                        "SELECT asset_id FROM album_assets WHERE album_id=?",
                        (album_id,),
                    ).fetchall()
                }
                if album_id
                else None
            )
            album_rows = connection.execute(
                "SELECT aa.asset_id,a.name FROM album_assets aa JOIN albums a ON a.id=aa.album_id"
                + (" WHERE aa.asset_id IN (" + ",".join("?" for _ in asset_ids) + ")" if asset_ids is not None else ""),
                id_parameters,
            ).fetchall()
            source_rows = connection.execute(
                "SELECT id,name,root_uri,kind FROM sources"
            ).fetchall()
        albums_by_asset: dict[str, list[str]] = {}
        for row in album_rows:
            albums_by_asset.setdefault(row["asset_id"], []).append(row["name"])
        sources_by_id = {
            row["id"]: (
                row["name"], row["root_uri"], row["kind"]
            )
            for row in source_rows
        }
        by_asset: dict[str, list[MediaCopy]] = {}
        for row in copy_rows:
            path = _path_from_uri(row["uri"])
            by_asset.setdefault(row["asset_id"], []).append(
                MediaCopy(
                    id=row["id"],
                    asset_id=row["asset_id"],
                    source_id=row["source_id"],
                    path=path,
                    uri=row["uri"],
                    reachable=bool(row["reachable"]),
                    bytes=row["bytes"],
                    modified=datetime.fromtimestamp(
                        row["modified_ns"] / 1_000_000_000,
                        timezone.utc,
                    ),
                    mime_type=row["mime_type"],
                    width=row["width"],
                    height=row["height"],
                    duration_ms=row["duration_ms"],
                    content_hash=row["content_hash"],
                    trashed=bool(row["trashed"]),
                    deleted_at=(
                        _as_datetime(row["deleted_at"])
                        if row["deleted_at"]
                        else None
                    ),
                    original_path=(
                        _path_from_uri(row["original_uri"])
                        if row["original_uri"]
                        else None
                    ),
                )
            )
        result: list[PhotoRecord] = []
        terms = tuple(term.casefold() for term in query.split() if term)
        now = datetime.now(timezone.utc)
        for row in rows:
            copies = tuple(by_asset.get(row["id"], ()))
            if not copies or (
                source_id
                and not any(copy.source_id == source_id for copy in copies)
            ):
                continue
            if memberships is not None and row["id"] not in memberships:
                continue
            captured = (
                _as_datetime(row["captured_at"])
                if row["captured_at"]
                else None
            )
            if collection == "favorites" and not row["favorite"]:
                continue
            if collection == "videos" and row["media_type"] != "video":
                continue
            if collection == "edited" and not row["edited"]:
                continue
            if collection == "recent" and captured and (now - captured).days > 30:
                continue
            when = captured or _as_datetime(row["modified_at"])
            date_terms = " ".join(
                (
                    when.isoformat(),
                    when.strftime("%Y %B %b %A %a %-d %m %d"),
                )
            )
            copy_terms: list[str] = []
            for copy in copies:
                source_name, source_uri, source_kind = sources_by_id.get(
                    copy.source_id, ("", "", "")
                )
                copy_terms.extend(
                    (
                        source_name,
                        source_uri,
                        source_kind,
                        str(copy.path),
                        "reachable" if copy.reachable else "out of reach",
                    )
                )
            haystack = " ".join(
                (
                    row["display_name"], row["mime_type"], row["media_type"],
                    row["caption"], row["place"], date_terms,
                    *albums_by_asset.get(row["id"], ()),
                    *copy_terms,
                    "favorite favourite starred" if row["favorite"] else "",
                    "edited derivative" if row["edited"] else "original",
                    "available in reach reachable"
                    if any(copy.reachable for copy in copies)
                    else "unavailable out of reach offline",
                )
            ).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            preferred = next(
                (copy for copy in copies if copy.reachable), copies[0]
            )
            result.append(
                PhotoRecord(
                    path=preferred.path,
                    modified=_as_datetime(row["modified_at"]),
                    bytes=preferred.bytes,
                    id=row["id"],
                    display_name=row["display_name"],
                    mime_type=row["mime_type"],
                    captured=captured,
                    width=row["width"],
                    height=row["height"],
                    duration_ms=row["duration_ms"],
                    favorite=bool(row["favorite"]),
                    caption=row["caption"],
                    place=row["place"],
                    edited=bool(row["edited"]),
                    deleted=deleted,
                    copies=copies,
                )
            )
        return tuple(result)

    def asset(self, asset_id: str) -> PhotoRecord:
        for record in self.assets(asset_ids=(asset_id,)):
            if record.id == asset_id:
                return record
        raise KeyError(asset_id)

    def set_favorite(self, asset_id: str, favorite: bool) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE assets SET favorite=? WHERE id=?",
                (bool(favorite), asset_id),
            ).rowcount
            if changed != 1:
                raise KeyError(asset_id)

    def set_metadata(
        self,
        asset_id: str,
        *,
        caption: str | None = None,
        place: str | None = None,
    ) -> None:
        updates: list[str] = []
        values: list[object] = []
        if caption is not None:
            updates.append("caption=?")
            values.append(caption)
        if place is not None:
            updates.append("place=?")
            values.append(place)
        if not updates:
            return
        values.append(asset_id)
        with self._connect() as connection:
            changed = connection.execute(
                f"UPDATE assets SET {','.join(updates)} WHERE id=?", values
            ).rowcount
            if changed != 1:
                raise KeyError(asset_id)

    def albums(self) -> tuple[AlbumRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT a.*,COUNT(aa.asset_id) AS asset_count
                   FROM albums a
                   LEFT JOIN album_assets aa ON aa.album_id=a.id
                   GROUP BY a.id ORDER BY a.name COLLATE NOCASE"""
            ).fetchall()
        return tuple(
            AlbumRecord(
                row["id"], row["name"], _as_datetime(row["created_at"]),
                row["asset_count"],
            )
            for row in rows
        )

    def create_album(self, name: str) -> AlbumRecord:
        clean = name.strip()
        if not clean:
            raise ValueError("album name cannot be empty")
        album_id = str(uuid.uuid4())
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO albums(id,name,created_at) VALUES(?,?,?)",
                    (album_id, clean, _utc_now()),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("an album with that name already exists") from error
        return next(album for album in self.albums() if album.id == album_id)

    def rename_album(self, album_id: str, name: str) -> None:
        clean = name.strip()
        if not clean:
            raise ValueError("album name cannot be empty")
        with self._connect() as connection:
            try:
                changed = connection.execute(
                    "UPDATE albums SET name=? WHERE id=?", (clean, album_id)
                ).rowcount
                if changed != 1:
                    raise KeyError(album_id)
            except sqlite3.IntegrityError as error:
                raise ValueError("an album with that name already exists") from error

    def delete_album(self, album_id: str) -> None:
        with self._connect() as connection:
            if connection.execute(
                "DELETE FROM albums WHERE id=?", (album_id,)
            ).rowcount != 1:
                raise KeyError(album_id)

    def add_to_album(self, album_id: str, asset_ids: Iterable[str]) -> None:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM albums WHERE id=?", (album_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(album_id)
            position = connection.execute(
                """SELECT COALESCE(MAX(position),-1)+1
                   FROM album_assets WHERE album_id=?""",
                (album_id,),
            ).fetchone()[0]
            for asset_id in asset_ids:
                if connection.execute(
                    "SELECT 1 FROM assets WHERE id=?", (asset_id,)
                ).fetchone() is None:
                    raise KeyError(asset_id)
                connection.execute(
                    """INSERT OR IGNORE INTO album_assets(
                       album_id,asset_id,position) VALUES(?,?,?)""",
                    (album_id, asset_id, position),
                )
                position += 1

    def remove_from_album(
        self, album_id: str, asset_ids: Iterable[str]
    ) -> None:
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM album_assets WHERE album_id=? AND asset_id=?",
                ((album_id, asset_id) for asset_id in asset_ids),
            )

    def mark_copy_trashed(self, copy_id: str) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE copies SET trashed=1,reachable=0 WHERE id=?", (copy_id,)
            ).rowcount
            if changed != 1:
                raise KeyError(copy_id)

    def trash_copy(
        self, copy_id: str, data_home: Path | None = None
    ) -> Path:
        """Move one local copy into freedesktop Trash and retain recovery data."""

        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.uri,c.trashed,s.root_uri
                   FROM copies c JOIN sources s ON s.id=c.source_id
                   WHERE c.id=?""",
                (copy_id,),
            ).fetchone()
        if row is None:
            raise KeyError(copy_id)
        if row["trashed"]:
            raise ValueError("copy is already in Recently Deleted")
        original_uri = row["uri"]
        original = _path_from_uri(original_uri)
        source_root = _path_from_uri(row["root_uri"])
        destination, metadata = trash_photo(
            original, root=source_root, data_home=data_home
        )
        try:
            with self._connect() as connection:
                changed = connection.execute(
                    """UPDATE copies SET uri=?,original_uri=?,trash_info_uri=?,
                         deleted_at=?,trashed=1,reachable=1,last_seen=?
                       WHERE id=? AND trashed=0""",
                    (
                        destination.resolve().as_uri(),
                        original_uri,
                        metadata.resolve().as_uri(),
                        _utc_now(),
                        _utc_now(),
                        copy_id,
                    ),
                ).rowcount
                if changed != 1:
                    raise KeyError(copy_id)
        except BaseException:
            if destination.exists() and not original.exists():
                original.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.move(destination, original)
            metadata.unlink(missing_ok=True)
            raise
        return destination

    def trash_copies(
        self, copy_ids: Iterable[str], data_home: Path | None = None
    ) -> TransferResult:
        completed: list[Path] = []
        errors: list[str] = []
        for copy_id in dict.fromkeys(copy_ids):
            try:
                completed.append(self.trash_copy(copy_id, data_home))
            except (KeyError, OSError, ValueError) as error:
                errors.append(f"{copy_id}: {error}")
        return TransferResult(tuple(completed), errors=tuple(errors))

    def restore_copy(self, copy_id: str) -> Path:
        """Restore a retained Trash copy, preserving both files on collision."""

        with self._connect() as connection:
            row = connection.execute(
                """SELECT uri,original_uri,trash_info_uri,trashed
                   FROM copies WHERE id=?""",
                (copy_id,),
            ).fetchone()
        if row is None:
            raise KeyError(copy_id)
        if not row["trashed"] or not row["original_uri"]:
            raise ValueError("copy has no recoverable Trash location")
        source = _path_from_uri(row["uri"])
        original = _path_from_uri(row["original_uri"])
        if not source.is_file():
            raise FileNotFoundError("the Trash copy is no longer available")
        if not original.parent.is_dir():
            raise FileNotFoundError("the original source is not connected")
        destination = _unique_destination(original.parent, original.name)
        shutil.move(source, destination)
        try:
            stat = destination.stat(follow_symlinks=False)
            with self._connect() as connection:
                changed = connection.execute(
                    """UPDATE copies SET uri=?,original_uri=NULL,
                         trash_info_uri=NULL,deleted_at=NULL,trashed=0,
                         reachable=1,file_key=?,bytes=?,modified_ns=?,last_seen=?
                       WHERE id=? AND trashed=1""",
                    (
                        destination.resolve().as_uri(),
                        _file_key(stat),
                        stat.st_size,
                        stat.st_mtime_ns,
                        _utc_now(),
                        copy_id,
                    ),
                ).rowcount
                if changed != 1:
                    raise KeyError(copy_id)
        except BaseException:
            if destination.exists() and not source.exists():
                shutil.move(destination, source)
            raise
        if row["trash_info_uri"]:
            _path_from_uri(row["trash_info_uri"]).unlink(missing_ok=True)
        return destination

    def restore_copies(self, copy_ids: Iterable[str]) -> TransferResult:
        completed: list[Path] = []
        errors: list[str] = []
        for copy_id in dict.fromkeys(copy_ids):
            try:
                completed.append(self.restore_copy(copy_id))
            except (KeyError, OSError, ValueError) as error:
                errors.append(f"{copy_id}: {error}")
        return TransferResult(tuple(completed), errors=tuple(errors))

    def delete_copy_permanently(self, copy_id: str) -> None:
        """Destroy one already-trashed copy and prune an orphaned asset."""

        with self._connect() as connection:
            row = connection.execute(
                """SELECT asset_id,uri,trash_info_uri,trashed
                   FROM copies WHERE id=?""",
                (copy_id,),
            ).fetchone()
        if row is None:
            raise KeyError(copy_id)
        if not row["trashed"]:
            raise ValueError("only Recently Deleted copies can be destroyed")
        path = _path_from_uri(row["uri"])
        if path.exists():
            path.unlink()
        if row["trash_info_uri"]:
            _path_from_uri(row["trash_info_uri"]).unlink(missing_ok=True)
        with self._connect() as connection:
            connection.execute("DELETE FROM copies WHERE id=?", (copy_id,))
            connection.execute(
                """DELETE FROM assets WHERE id=? AND NOT EXISTS(
                     SELECT 1 FROM copies WHERE asset_id=?
                   )""",
                (row["asset_id"], row["asset_id"]),
            )

    def delete_copies_permanently(
        self, copy_ids: Iterable[str]
    ) -> TransferResult:
        completed: list[Path] = []
        errors: list[str] = []
        for copy_id in dict.fromkeys(copy_ids):
            try:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT uri FROM copies WHERE id=?", (copy_id,)
                    ).fetchone()
                if row is None:
                    raise KeyError(copy_id)
                path = _path_from_uri(row["uri"])
                self.delete_copy_permanently(copy_id)
                completed.append(path)
            except (KeyError, OSError, ValueError) as error:
                errors.append(f"{copy_id}: {error}")
        return TransferResult(tuple(completed), errors=tuple(errors))

    def import_files(
        self,
        paths: Iterable[Path],
        destination: Path | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> TransferResult:
        """Copy supported originals into Pictures and index committed results.

        Each file is written to a private sibling temporary and atomically
        renamed only after it is flushed. The source is never moved or
        modified. Exact duplicates already known to the catalog are skipped.
        """

        target = (
            pictures_directory().resolve(strict=False)
            if destination is None
            else Path(destination).expanduser().resolve(strict=False)
        )
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        source_record = self.add_source(target, target.name or "Photos")
        completed: list[Path] = []
        errors: list[str] = []
        duplicates = 0
        unsupported = 0
        for candidate in paths:
            if cancelled and cancelled():
                break
            source = Path(candidate).expanduser().resolve(strict=False)
            if not source.is_file():
                errors.append(f"{source.name or source}: not a readable file")
                continue
            if _sniff_mime(source) is None:
                unsupported += 1
                continue
            try:
                size = source.stat().st_size
                digest = _sha256(source)
                with self._connect() as connection:
                    candidates = connection.execute(
                        """SELECT id,uri,content_hash FROM copies
                           WHERE bytes=? AND trashed=0""",
                        (size,),
                    ).fetchall()
                duplicate = False
                for row in candidates:
                    known = row["content_hash"]
                    if not known:
                        known_path = _path_from_uri(row["uri"])
                        if known_path.is_file():
                            known = _sha256(known_path)
                            with self._connect() as connection:
                                connection.execute(
                                    "UPDATE copies SET content_hash=? WHERE id=?",
                                    (known, row["id"]),
                                )
                    if known == digest:
                        duplicate = True
                        break
                if duplicate:
                    duplicates += 1
                    continue
                output = _unique_destination(target, source.name)
                _atomic_copy(source, output)
                completed.append(output)
            except OSError as error:
                errors.append(f"{source.name}: {error}")
        if completed:
            self.scan_source(source_record.id, cancelled)
            with self._connect() as connection:
                connection.executemany(
                    "UPDATE copies SET content_hash=? WHERE source_id=? AND uri=?",
                    (
                        (_sha256(path), source_record.id, path.as_uri())
                        for path in completed
                    ),
                )
            self.reconcile_exact_copies()
        return TransferResult(
            tuple(completed), duplicates, unsupported, tuple(errors)
        )

    def export_assets(
        self,
        asset_ids: Iterable[str],
        destination: Path,
        cancelled: Callable[[], bool] | None = None,
        render_photo: Callable[[Path, Mapping[str, object]], bytes] | None = None,
    ) -> TransferResult:
        """Export saved photo edits, or exact original bytes when unedited.

        The GTK caller supplies the shared preview renderer; catalog workers
        never create a second image engine or silently discard saved edits.
        Edited output is lossless PNG and never replaces a destination file.
        """
        from .photos_adjustments import AdjustmentStore, has_edits

        target = Path(destination).expanduser().resolve(strict=False)
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        completed: list[Path] = []
        errors: list[str] = []
        for asset_id in dict.fromkeys(asset_ids):
            if cancelled and cancelled():
                break
            try:
                record = self.asset(asset_id)
            except KeyError:
                errors.append(f"{asset_id}: no longer exists")
                continue
            copy = next((item for item in record.copies if item.reachable), None)
            if copy is None:
                errors.append(f"{record.display_name}: original is unavailable")
                continue
            try:
                settings = AdjustmentStore(self.database).load(asset_id) if record.mime_type.startswith(IMAGE_MIME_PREFIX) else {}
                if has_edits(settings):
                    if render_photo is None:
                        raise ValueError('The saved photo edits need an image renderer to export')
                    original_hash = _sha256(copy.path)
                    payload = render_photo(copy.path, settings)
                    if not isinstance(payload, bytes) or not 8 < len(payload) <= 512 * 1024 * 1024 or not payload.startswith(b'\x89PNG\r\n\x1a\n'):
                        raise ValueError('The edited photo did not produce a valid PNG')
                    if cancelled and cancelled():
                        break
                    if _sha256(copy.path) != original_hash:
                        raise OSError('The original photo changed while exporting; try again')
                    output = _unique_destination(target, Path(record.display_name).stem + '.png')
                    descriptor, name = tempfile.mkstemp(prefix='.luma-export-', suffix='.partial', dir=target)
                    temporary = Path(name)
                    try:
                        with os.fdopen(descriptor, 'wb') as stream:
                            stream.write(payload)
                            stream.flush()
                            os.fsync(stream.fileno())
                        # Publish exclusively: a concurrent save must not be
                        # replaced after the non-atomic name availability check.
                        os.link(temporary, output)
                        directory = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory)
                        finally:
                            os.close(directory)
                    finally:
                        temporary.unlink(missing_ok=True)
                else:
                    output = _unique_destination(target, record.display_name)
                    _atomic_copy(copy.path, output)
                    if _sha256(output) != self.exact_hash(copy.id):
                        output.unlink(missing_ok=True)
                        raise OSError("verification failed")
                completed.append(output)
            except (OSError, ValueError, RuntimeError) as error:
                errors.append(f"{record.display_name}: {error}")
        return TransferResult(tuple(completed), errors=tuple(errors))

    def exact_hash(self, copy_id: str, chunk_size: int = 1024 * 1024) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT uri,reachable,content_hash FROM copies WHERE id=?",
                (copy_id,),
            ).fetchone()
            if row is None:
                raise KeyError(copy_id)
            if row["content_hash"]:
                return row["content_hash"]
            if not row["reachable"]:
                raise FileNotFoundError("copy is currently unavailable")
            path = _path_from_uri(row["uri"])
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(chunk_size):
                    digest.update(block)
            value = digest.hexdigest()
            connection.execute(
                "UPDATE copies SET content_hash=? WHERE id=?",
                (value, copy_id),
            )
            return value

    def reconcile_exact_copies(
        self, copy_ids: Iterable[str] | None = None
    ) -> int:
        """Merge logical assets only after full byte-for-byte identity proof.

        This operation is intentionally explicit: callers run it from a
        worker when duplicate reconciliation is requested. Ordinary indexing
        never hashes an entire library merely to make the grid look tidier.
        """

        with self._connect() as connection:
            if copy_ids is None:
                rows = connection.execute(
                    "SELECT id,asset_id FROM copies WHERE reachable=1 AND trashed=0"
                ).fetchall()
            else:
                identifiers = tuple(copy_ids)
                if not identifiers:
                    return 0
                placeholders = ",".join("?" for _ in identifiers)
                rows = connection.execute(
                    f"""SELECT id,asset_id FROM copies
                        WHERE reachable=1 AND trashed=0
                          AND id IN ({placeholders})""",
                    identifiers,
                ).fetchall()
        groups: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            digest = self.exact_hash(row["id"])
            groups.setdefault(digest, []).append((row["id"], row["asset_id"]))

        merged = 0
        with self._connect() as connection:
            for copies in groups.values():
                asset_ids = list(dict.fromkeys(asset_id for _copy_id, asset_id in copies))
                if len(asset_ids) < 2:
                    continue
                target = asset_ids[0]
                duplicates = asset_ids[1:]
                for duplicate in duplicates:
                    favorite = connection.execute(
                        "SELECT favorite FROM assets WHERE id=?", (duplicate,)
                    ).fetchone()
                    if favorite and favorite["favorite"]:
                        connection.execute(
                            "UPDATE assets SET favorite=1 WHERE id=?", (target,)
                        )
                    memberships = connection.execute(
                        "SELECT album_id,position FROM album_assets WHERE asset_id=?",
                        (duplicate,),
                    ).fetchall()
                    for membership in memberships:
                        connection.execute(
                            """INSERT OR IGNORE INTO album_assets(
                               album_id,asset_id,position) VALUES(?,?,?)""",
                            (
                                membership["album_id"], target,
                                membership["position"],
                            ),
                        )
                    connection.execute(
                        "UPDATE copies SET asset_id=? WHERE asset_id=?",
                        (target, duplicate),
                    )
                    connection.execute("DELETE FROM assets WHERE id=?", (duplicate,))
                    merged += 1
        return merged


class ThumbnailCache:
    """Freedesktop thumbnail path and validity helpers."""

    def __init__(self, root: Path | None = None, size: str = "large") -> None:
        if size not in {"normal", "large", "x-large", "xx-large"}:
            raise ValueError("unsupported freedesktop thumbnail size")
        self.root = default_thumbnail_root() if root is None else Path(root)
        self.size = size
        self.directory = self.root / size
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path_for_uri(self, uri: str) -> Path:
        digest = hashlib.md5(
            uri.encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return self.directory / f"{digest}.png"

    def lookup(self, copy: MediaCopy) -> Path | None:
        path = self.path_for_uri(copy.uri)
        if not path.is_file():
            return None
        try:
            modified_ns = int(copy.modified.timestamp() * 1_000_000_000)
            return path if path.stat().st_mtime_ns >= modified_ns else None
        except OSError:
            return None


def list_photos(root: Path | None = None) -> tuple[PhotoRecord, ...]:
    """Compatibility one-shot image index for tests and recovery tools."""

    directory = pictures_directory() if root is None else Path(root)
    if not directory.is_dir():
        return ()
    result: list[PhotoRecord] = []
    for path in _walk_files(directory):
        mime_type = _sniff_mime(path)
        if not mime_type or not mime_type.startswith(IMAGE_MIME_PREFIX):
            continue
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError:
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        copy = MediaCopy(
            str(uuid.uuid4()), "", "", path, path.resolve().as_uri(), True,
            stat.st_size, modified, mime_type,
        )
        result.append(
            PhotoRecord(
                path, modified, stat.st_size, display_name=path.name,
                mime_type=mime_type, captured=modified, copies=(copy,),
            )
        )
    return tuple(sorted(result, key=lambda item: item.modified, reverse=True))


def trash_photo(
    path: Path,
    root: Path | None = None,
    data_home: Path | None = None,
) -> tuple[Path, Path]:
    """Portable freedesktop Trash fallback for non-GIO environments."""

    pictures = (pictures_directory() if root is None else Path(root)).resolve()
    target = Path(path).resolve()
    mime_type = (
        _sniff_mime(target)
        if target.is_file() and not target.is_symlink()
        else None
    )
    if not mime_type or pictures not in target.parents:
        raise ValueError("media path is outside the Pictures library")
    trash_root = (
        Path(data_home)
        if data_home
        else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    )
    files = trash_root / "Trash/files"
    info = trash_root / "Trash/info"
    files.mkdir(mode=0o700, parents=True, exist_ok=True)
    info.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = target.name
    while (files / name).exists() or (info / f"{name}.trashinfo").exists():
        name = f"{target.stem}-{uuid.uuid4().hex[:8]}{target.suffix}"
    destination = files / name
    metadata = info / f"{name}.trashinfo"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".trashinfo-", dir=info
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("[Trash Info]\n")
            stream.write(f"Path={quote(str(target))}\n")
            stream.write(
                f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        shutil.move(target, destination)
        temporary.replace(metadata)
    except BaseException:
        temporary.unlink(missing_ok=True)
        if destination.exists() and not target.exists():
            shutil.move(destination, target)
        raise
    return destination, metadata
