# SPDX-License-Identifier: Apache-2.0

"""Recents — the files you have looked at, and where you were in them.

This is not a session list. It persists, it survives a restart, and it is what
makes Viewer pleasant rather than merely functional: reopening a 200-page PDF
lands on the page you left, and a row appears instantly from a cached thumbnail
while the real render arrives behind it.

Clearing it clears everything — the list, the thumbnails and the remembered
positions. A privacy control that leaves cached thumbnails on disk is a lie.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile


MAX_ENTRIES = 400


def _data_home() -> Path:
    preview_root = os.environ.get("LUMA_VIEWER_PREVIEW_STATE_ROOT") if os.environ.get("LUMA_VIEWER_PREVIEW") else None
    if preview_root:
        return Path(preview_root) / "data" / "luma-viewer"
    root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(root) / "luma-viewer"


def _cache_home() -> Path:
    preview_root = os.environ.get("LUMA_VIEWER_PREVIEW_STATE_ROOT") if os.environ.get("LUMA_VIEWER_PREVIEW") else None
    if preview_root:
        return Path(preview_root) / "cache" / "luma-viewer"
    root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(root) / "luma-viewer"


@dataclass
class RecentFile:
    path: str
    name: str = ""
    kind: str = ""
    size: int = 0
    opened_at: float = 0.0
    page: int = 0
    zoom: float = 1.0
    scroll: float = 0.0

    def __post_init__(self) -> None:
        if not self.name:
            self.name = Path(self.path).name

    @property
    def exists(self) -> bool:
        return Path(self.path).exists()

    @property
    def opened_day(self) -> date:
        return datetime.fromtimestamp(self.opened_at or 0).date()

    @property
    def size_text(self) -> str:
        return format_size(self.size)


def format_size(size: int) -> str:
    """A size a person reads, not a number of bytes."""
    if size <= 0:
        return "—"
    for unit, step in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if size >= step:
            value = size / step
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{size} bytes"


def day_heading(day: date) -> str:
    today = date.today()
    delta = (today - day).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if delta < 7:
        return day.strftime("%A")
    if day.year == today.year:
        return day.strftime("%-d %B")
    return day.strftime("%-d %B %Y")


class Recents:
    """The stored list, its thumbnails, and the positions within files."""

    def __init__(self, directory: Path | None = None, cache: Path | None = None) -> None:
        self.directory = directory or _data_home()
        self.cache = cache or (_cache_home() / "thumbnails")
        self.file = self.directory / "recents.json"
        self.entries: list[RecentFile] = []
        self._saved_entries = {}
        self._clear_requested = False
        self.load()

    # ── Storage ──────────────────────────────────────────────────────────

    def load(self) -> None:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.entries = []
            return
        entries = []
        if not isinstance(raw, dict) or not isinstance(raw.get("files"), list):
            self.entries = []
            return
        for item in raw["files"]:
            if not isinstance(item, dict):
                continue
            try:
                entry = RecentFile(**{key: value for key, value in item.items()
                                      if key in RecentFile.__dataclass_fields__})
                if not isinstance(entry.path, str) or not isinstance(entry.name, str) or not isinstance(entry.kind, str):
                    continue
                if not isinstance(entry.page, int) or not 0 <= entry.page <= 1000000:
                    continue
                if not isinstance(entry.size, int) or entry.size < 0:
                    continue
                if not isinstance(entry.opened_at, (float,int)) or not math.isfinite(entry.opened_at):
                    continue
                datetime.fromtimestamp(entry.opened_at)
                entries.append(entry)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
        entries.sort(key=lambda entry: entry.opened_at, reverse=True)
        self.entries = entries[:MAX_ENTRIES]
        self._saved_entries = {entry.path: asdict(entry) for entry in self.entries}

    def save(self) -> None:
        """Merge edited fields into the latest file and back it up before writing.

        Unknown fields and records from other writers survive. An unreadable
        existing store is left intact; history is optional to opening a file.
        """
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (self.directory / "recents.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    original = self.file.read_bytes()
                except FileNotFoundError:
                    original = None
                payload = json.loads(original) if original is not None else {"version": 1, "files": []}
                if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
                    return
                current = {entry.path: asdict(entry) for entry in self.entries}
                deleted = self._saved_entries.keys() - current.keys()
                rows = [] if self._clear_requested else [row for row in payload["files"]
                        if not (isinstance(row, dict) and isinstance(row.get("path"), str)
                                and row["path"] in deleted)]
                by_path = {row["path"]: row for row in rows
                           if isinstance(row, dict) and isinstance(row.get("path"), str)}
                for path, fields in current.items():
                    before = self._saved_entries.get(path, {})
                    edits = {key: value for key, value in fields.items() if before.get(key) != value}
                    if not edits:
                        continue
                    if path not in by_path:
                        # A concurrently forgotten record is not restored by
                        # an unrelated position update from this window.
                        if path in self._saved_entries:
                            continue
                        row = dict(fields)
                        rows.insert(0, row)
                        by_path[path] = row
                    else:
                        by_path[path].update(edits)
                payload["files"] = rows
                if original is not None:
                    self._backup_once(original)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory,
                                                 delete=False) as handle:
                    temporary = handle.name
                    json.dump(payload, handle, indent=1)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.file)
                self._saved_entries = current
                self._clear_requested = False
        except (OSError, ValueError, TypeError):
            # Failed optional history storage must not lose a document.
            pass
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _backup_once(self, original: bytes) -> None:
        """Publish only a complete backup, before replacing existing history."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=self.directory,
                                             prefix=".recents-backup-", delete=False) as handle:
                temporary = handle.name
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, self.file.with_name("recents.json.before-lumaui"))
            except FileExistsError:
                pass
            directory = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    # ── The list ─────────────────────────────────────────────────────────

    def record(self, path: str, *, kind: str = "") -> RecentFile:
        resolved = str(Path(path).expanduser())
        try:
            size = Path(resolved).stat().st_size
        except OSError:
            size = 0
        existing = self.find(resolved)
        if existing is not None:
            existing.opened_at = datetime.now().timestamp()
            existing.size = size or existing.size
            if kind:
                existing.kind = kind
            self.entries.remove(existing)
            self.entries.insert(0, existing)
            self.save()
            return existing
        entry = RecentFile(path=resolved, kind=kind, size=size,
                           opened_at=datetime.now().timestamp())
        self.entries.insert(0, entry)
        del self.entries[MAX_ENTRIES:]
        self.save()
        return entry

    def find(self, path: str) -> RecentFile | None:
        resolved = str(Path(path).expanduser())
        for entry in self.entries:
            if entry.path == resolved:
                return entry
        return None

    def remember_position(self, path: str, *, page: int = 0, zoom: float = 1.0,
                          scroll: float = 0.0) -> None:
        entry = self.find(path)
        if entry is None:
            return
        entry.page = page
        entry.zoom = zoom
        entry.scroll = scroll
        self.save()

    def forget(self, path: str) -> None:
        entry = self.find(path)
        if entry is None:
            return
        self.entries.remove(entry)
        self._drop_thumbnail(entry.path)
        self.save()

    def clear(self) -> None:
        """Everything: the list, the thumbnails, the remembered positions."""
        self.entries = []
        self._clear_requested = True
        self.save()
        if self.cache.is_dir():
            for item in self.cache.iterdir():
                if item.is_file():
                    try:
                        item.unlink()
                    except OSError:
                        pass

    def search(self, text: str) -> list[RecentFile]:
        needle = text.strip().casefold()
        if not needle:
            return list(self.entries)
        return [entry for entry in self.entries
                if needle in entry.name.casefold() or needle in entry.kind.casefold()]

    def grouped(self, text: str = "") -> list[tuple[str, list[RecentFile]]]:
        """The list as the sidebar shows it: a heading, then that day's files."""
        groups: list[tuple[str, list[RecentFile]]] = []
        for entry in self.search(text):
            heading = day_heading(entry.opened_day)
            if groups and groups[-1][0] == heading:
                groups[-1][1].append(entry)
            else:
                groups.append((heading, [entry]))
        return groups

    # ── Thumbnails ───────────────────────────────────────────────────────

    def thumbnail_path(self, path: str) -> Path:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:32]
        return self.cache / f"{digest}.png"

    def _drop_thumbnail(self, path: str) -> None:
        try:
            self.thumbnail_path(path).unlink()
        except OSError:
            pass

    def cached_thumbnail(self, path: str) -> Path | None:
        candidate = self.thumbnail_path(path)
        if not candidate.is_file():
            return None
        try:
            if candidate.stat().st_mtime < Path(path).stat().st_mtime:
                return None
        except OSError:
            return None
        return candidate

    def store_thumbnail(self, path: str, pixbuf) -> Path | None:
        target = self.thumbnail_path(path)
        try:
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            pixbuf.savev(str(target), "png", [], [])
        except Exception:
            return None
        return target
