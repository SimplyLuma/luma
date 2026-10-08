# SPDX-License-Identifier: Apache-2.0
"""Leaf's library: one SQLite file, one record per book.

What belongs to the book — its position, highlights, notes and bookmarks —
lives here and is what syncs. What belongs to this device — the page theme,
size, typeface — lives in `prefs` and never leaves it.

A book's position is stored once, as a CFI, with when and where it was
reached. Chapter, percent and time are derived (position.py); they have no
column, so nothing can store a figure that disagrees with another.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

SHELVES = ("new", "want", "reading", "finished")
COLOURS = ("y", "g", "b", "r")

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS books (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        authors TEXT NOT NULL DEFAULT '[]',
        language TEXT NOT NULL DEFAULT 'en',
        identifier TEXT NOT NULL DEFAULT '',
        path TEXT,
        source TEXT NOT NULL DEFAULT 'device',
        remote_url TEXT,
        cover TEXT,
        stats TEXT,
        shelf TEXT NOT NULL DEFAULT 'new',
        added_at REAL NOT NULL,
        position TEXT,
        position_at REAL,
        position_device TEXT,
        missing INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS highlights (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        range TEXT NOT NULL,
        colour TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        spine INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS bookmarks (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        position TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        spine INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS collections (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS collection_books (
        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        PRIMARY KEY (collection_id, book_id)
    );
    CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        reachable INTEGER NOT NULL DEFAULT 1,
        checked_at REAL
    );
    CREATE TABLE IF NOT EXISTS prefs (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS highlights_book ON highlights(book_id, deleted);
    CREATE INDEX IF NOT EXISTS bookmarks_book ON bookmarks(book_id, deleted);
    """,
    # 2: when a book changed shelf (so a shelf change syncs by recency), and
    # reading records from other devices for books not on this one yet.
    """
    ALTER TABLE books ADD COLUMN shelf_at REAL NOT NULL DEFAULT 0;
    CREATE TABLE IF NOT EXISTS synced_books (
        book_id TEXT PRIMARY KEY,
        data TEXT NOT NULL
    );
    """,
]


def data_directory() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "leaf"


def cache_directory() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "leaf"


# A name with one comma is usually the file-as form, "Isaacson, Walter", which
# is for sorting. These after the comma are part of the name instead.
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "esq", "inc", "ltd", "llc", "co"}
_ALTERNATIVE_TITLE = re.compile(r"\s*(?:[;:]|,\s*or\b,?)\s*", re.IGNORECASE)


def display_name(name: str) -> str:
    """"Isaacson, Walter" → "Walter Isaacson"; any other form is kept as written."""
    name = " ".join(name.split())
    if name.count(",") != 1:
        return name
    last, first = (part.strip() for part in name.split(","))
    if not last or not first or first.lower().rstrip(".").replace(".", "") in _NAME_SUFFIXES:
        return name
    return f"{first} {last}"


def surname(name: str) -> str:
    """The family name an author sorts under, from either form."""
    name = " ".join(name.split())
    if name.count(",") == 1 and display_name(name) != name:
        return name.split(",")[0].strip().lower()
    words = [w for w in name.replace(",", " ").split() if w.lower().rstrip(".") not in _NAME_SUFFIXES]
    return words[-1].lower() if words else ""


def main_title(title: str) -> str:
    """The title before its subtitle or alternative title.

    "Moby Dick; Or, The Whale" → "Moby Dick"; "Frankenstein; or, the modern
    prometheus" → "Frankenstein"; "Twelfth Night, or What You Will" → "Twelfth Night".
    """
    title = " ".join(title.split())
    match = _ALTERNATIVE_TITLE.search(title)
    if match is None:
        return title
    head = title[:match.start()].strip()
    return head if len(head) >= 2 else title


@dataclass
class Book:
    id: str
    title: str
    authors: list[str]
    language: str
    identifier: str
    path: str | None
    source: str
    remote_url: str | None
    cover: str | None
    stats: str | None
    shelf: str
    added_at: float
    position: str | None
    position_at: float | None
    position_device: str | None
    missing: bool
    shelf_at: float = 0.0
    collections: list[str] = field(default_factory=list)

    @property
    def author(self) -> str:
        """The authors as a person writes them: "Walter Isaacson", not "Isaacson, Walter"."""
        names = [display_name(name) for name in self.authors if name.strip()]
        return ", ".join(names) if names else "Unknown author"

    @property
    def surname(self) -> str:
        return surname(self.authors[0] if self.authors else "")

    @property
    def short_title(self) -> str:
        """The title the shelf shows; the stored title is kept whole for details and search."""
        return main_title(self.title)

    @property
    def sort_title(self) -> str:
        title = self.title.strip()
        for article in ("the ", "a ", "an "):
            if title.lower().startswith(article):
                return title[len(article):].lower()
        return title.lower()

    @property
    def on_device(self) -> bool:
        return self.path is not None and not self.missing

    @property
    def last_read(self) -> float:
        return self.position_at or 0.0


@dataclass
class Highlight:
    id: str
    book_id: str
    range: str
    colour: str
    text: str
    note: str
    spine: int
    created_at: float
    updated_at: float


@dataclass
class Bookmark:
    id: str
    book_id: str
    position: str
    text: str
    spine: int
    created_at: float


@dataclass
class Collection:
    id: str
    name: str
    books: list[str]


@dataclass
class Source:
    id: str
    kind: str          # folder | calibre | opds
    name: str
    url: str
    username: str
    reachable: bool


class Library:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_directory() / "library.sqlite3"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for index, statement in enumerate(_SCHEMA[version:], start=version):
            with self.db:
                self.db.executescript(statement)
                self.db.execute(f"PRAGMA user_version={index + 1}")
        self._listeners: list = []

    # change notification — the window re-renders from the record
    def subscribe(self, callback) -> None:
        self._listeners.append(callback)

    def _changed(self, what: str, book_id: str | None = None) -> None:
        for callback in list(self._listeners):
            callback(what, book_id)

    # books
    def books(self) -> list[Book]:
        rows = self.db.execute("SELECT * FROM books").fetchall()
        members: dict[str, list[str]] = {}
        for row in self.db.execute("SELECT collection_id, book_id FROM collection_books"):
            members.setdefault(row["book_id"], []).append(row["collection_id"])
        return [self._book(row, members.get(row["id"], [])) for row in rows]

    def book(self, book_id: str) -> Book | None:
        row = self.db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if row is None:
            return None
        members = [r[0] for r in self.db.execute("SELECT collection_id FROM collection_books WHERE book_id=?", (book_id,))]
        return self._book(row, members)

    @staticmethod
    def _book(row: sqlite3.Row, collections: list[str]) -> Book:
        data = dict(row)
        data["authors"] = json.loads(data["authors"] or "[]")
        data["missing"] = bool(data["missing"])
        return Book(**data, collections=collections)

    def upsert_book(self, *, id: str, title: str, authors: list[str], language: str, identifier: str,
                    path: str | None, source: str = "device", remote_url: str | None = None,
                    cover: str | None, stats: str | None) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO books (id, title, authors, language, identifier, path, source, remote_url, cover, stats, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET title=excluded.title, authors=excluded.authors,
                     language=excluded.language, identifier=excluded.identifier,
                     path=COALESCE(excluded.path, books.path), source=CASE WHEN excluded.path IS NOT NULL THEN excluded.source ELSE books.source END,
                     remote_url=COALESCE(excluded.remote_url, books.remote_url),
                     cover=COALESCE(excluded.cover, books.cover), stats=COALESCE(excluded.stats, books.stats), missing=0""",
                (id, title, json.dumps(authors), language, identifier, path, source, remote_url, cover, stats, time.time()))
        self._changed("books", id)

    def mark_missing(self, book_ids: set[str]) -> None:
        with self.db:
            for book_id in book_ids:
                self.db.execute("UPDATE books SET missing=1 WHERE id=?", (book_id,))
        if book_ids:
            self._changed("books")

    def set_shelf(self, book_id: str, shelf: str) -> None:
        if shelf not in SHELVES:
            raise ValueError(shelf)
        with self.db:
            self.db.execute("UPDATE books SET shelf=?, shelf_at=? WHERE id=?", (shelf, time.time(), book_id))
        self._changed("books", book_id)

    def open_book(self, book_id: str) -> None:
        """Opening a New or Want-to-read book moves it to Reading now."""
        with self.db:
            self.db.execute("UPDATE books SET shelf='reading', shelf_at=? WHERE id=? AND shelf IN ('new','want')",
                            (time.time(), book_id))
        self._changed("books", book_id)

    def set_position(self, book_id: str, position: str, *, device: str, at: float | None = None) -> bool:
        """Save where the reader is. An older reading never moves a newer one back."""
        at = at or time.time()
        with self.db:
            changed = self.db.execute(
                "UPDATE books SET position=?, position_at=?, position_device=? WHERE id=? AND (position_at IS NULL OR position_at <= ?)",
                (position, at, device, book_id, at)).rowcount
        if changed:
            self._changed("position", book_id)
        return bool(changed)

    # highlights
    def highlights(self, book_id: str) -> list[Highlight]:
        rows = self.db.execute(
            "SELECT id, book_id, range, colour, text, note, spine, created_at, updated_at FROM highlights "
            "WHERE book_id=? AND deleted=0", (book_id,)).fetchall()
        return [Highlight(**dict(row)) for row in rows]

    def all_highlights(self) -> list[Highlight]:
        """Every saved highlight for the library's Highlights and notes view."""
        rows = self.db.execute(
            "SELECT id, book_id, range, colour, text, note, spine, created_at, updated_at "
            "FROM highlights WHERE deleted=0 ORDER BY created_at DESC").fetchall()
        return [Highlight(**dict(row)) for row in rows]

    def add_highlight(self, book_id: str, range_: str, colour: str, text: str, spine: int, note: str = "") -> Highlight:
        if colour not in COLOURS:
            raise ValueError(colour)
        now = time.time()
        highlight = Highlight(uuid.uuid4().hex, book_id, range_, colour, text, note, spine, now, now)
        with self.db:
            self.db.execute("INSERT INTO highlights (id, book_id, range, colour, text, note, spine, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (highlight.id, book_id, range_, colour, text, note, spine, now, now))
        self._changed("highlights", book_id)
        return highlight

    def update_highlight(self, highlight_id: str, *, colour: str | None = None, note: str | None = None) -> None:
        book = self.db.execute("SELECT book_id FROM highlights WHERE id=?", (highlight_id,)).fetchone()
        if book is None:
            return
        with self.db:
            if colour is not None:
                self.db.execute("UPDATE highlights SET colour=?, updated_at=? WHERE id=?", (colour, time.time(), highlight_id))
            if note is not None:
                self.db.execute("UPDATE highlights SET note=?, updated_at=? WHERE id=?", (note, time.time(), highlight_id))
        self._changed("highlights", book[0])

    def remove_highlight(self, highlight_id: str) -> None:
        book = self.db.execute("SELECT book_id FROM highlights WHERE id=?", (highlight_id,)).fetchone()
        with self.db:
            self.db.execute("UPDATE highlights SET deleted=1, updated_at=? WHERE id=?", (time.time(), highlight_id))
        if book:
            self._changed("highlights", book[0])

    # bookmarks
    def bookmarks(self, book_id: str) -> list[Bookmark]:
        rows = self.db.execute("SELECT id, book_id, position, text, spine, created_at FROM bookmarks "
                               "WHERE book_id=? AND deleted=0", (book_id,)).fetchall()
        return [Bookmark(**dict(row)) for row in rows]

    def add_bookmark(self, book_id: str, position: str, text: str, spine: int) -> Bookmark:
        now = time.time()
        mark = Bookmark(uuid.uuid4().hex, book_id, position, text, spine, now)
        with self.db:
            self.db.execute("INSERT INTO bookmarks (id, book_id, position, text, spine, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                            (mark.id, book_id, position, text, spine, now, now))
        self._changed("bookmarks", book_id)
        return mark

    def remove_bookmark(self, bookmark_id: str) -> None:
        book = self.db.execute("SELECT book_id FROM bookmarks WHERE id=?", (bookmark_id,)).fetchone()
        with self.db:
            self.db.execute("UPDATE bookmarks SET deleted=1, updated_at=? WHERE id=?", (time.time(), bookmark_id))
        if book:
            self._changed("bookmarks", book[0])

    # collections
    def collections(self) -> list[Collection]:
        out = []
        for row in self.db.execute("SELECT id, name FROM collections ORDER BY name COLLATE NOCASE"):
            books = [r[0] for r in self.db.execute("SELECT book_id FROM collection_books WHERE collection_id=?", (row["id"],))]
            out.append(Collection(row["id"], row["name"], books))
        return out

    def add_collection(self, name: str) -> Collection:
        collection = Collection(uuid.uuid4().hex, name.strip() or "Untitled collection", [])
        with self.db:
            self.db.execute("INSERT INTO collections (id, name, created_at) VALUES (?,?,?)",
                            (collection.id, collection.name, time.time()))
        self._changed("collections")
        return collection

    def rename_collection(self, collection_id: str, name: str) -> None:
        with self.db:
            self.db.execute("UPDATE collections SET name=? WHERE id=?", (name.strip() or "Untitled collection", collection_id))
        self._changed("collections")

    def delete_collection(self, collection_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        self._changed("collections")

    def set_in_collection(self, collection_id: str, book_id: str, member: bool) -> None:
        with self.db:
            if member:
                self.db.execute("INSERT OR IGNORE INTO collection_books VALUES (?,?)", (collection_id, book_id))
            else:
                self.db.execute("DELETE FROM collection_books WHERE collection_id=? AND book_id=?", (collection_id, book_id))
        self._changed("collections", book_id)

    # sources
    def sources(self) -> list[Source]:
        return [Source(r["id"], r["kind"], r["name"], r["url"], r["username"], bool(r["reachable"]))
                for r in self.db.execute("SELECT * FROM sources ORDER BY name COLLATE NOCASE")]

    def add_source(self, kind: str, name: str, url: str, username: str = "") -> Source:
        source = Source(uuid.uuid4().hex, kind, name, url, username, True)
        with self.db:
            self.db.execute("INSERT INTO sources (id, kind, name, url, username) VALUES (?,?,?,?,?)",
                            (source.id, kind, name, url, username))
        self._changed("sources")
        return source

    def set_reachable(self, source_id: str, reachable: bool) -> None:
        with self.db:
            changed = self.db.execute("UPDATE sources SET reachable=?, checked_at=? WHERE id=? AND reachable<>?",
                                      (int(reachable), time.time(), source_id, int(reachable))).rowcount
        if changed:
            self._changed("sources")

    def remove_source(self, source_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM books WHERE source=? AND path IS NULL", (source_id,))
            self.db.execute("DELETE FROM sources WHERE id=?", (source_id,))
        self._changed("sources")

    # per-device preferences
    def pref(self, key: str, default):
        row = self.db.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_pref(self, key: str, value) -> None:
        with self.db:
            self.db.execute("INSERT INTO prefs (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))
