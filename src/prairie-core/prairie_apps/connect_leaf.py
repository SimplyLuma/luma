# SPDX-License-Identifier: Apache-2.0

"""Leaf's reading records as an account-wide list: where each book is up to.

Only what belongs to a book syncs — its position, shelf, highlights, notes and
bookmarks — never the book itself and never the page preferences. Items are
keyed by the book's own identifier (the id Leaf derives from the EPUB), so the
same book picks up where another device left off.

Merging never loses reading: the newest position wins and an older one never
moves anyone back; highlights and bookmarks merge by id, the latest edit or
removal of each kept. The source of truth applies the same rules
(sync-collections.mjs, `leaf-books`), so a race between devices still merges.

A record for a book this device does not have waits in `synced_books` and is
applied when the book arrives, rather than being mistaken for a removal.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

SHELVES = ("new", "want", "reading", "finished")


def database(data_home: Path) -> Path:
    return data_home / "leaf" / "library.sqlite3"


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _has(connection: sqlite3.Connection, table: str, column: str | None = None) -> bool:
    if column is None:
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def _records(connection: sqlite3.Connection) -> list[tuple[str, dict]]:
    shelf_at = _has(connection, "books", "shelf_at")
    items: dict[str, dict] = {}
    for row in connection.execute("SELECT * FROM books ORDER BY id"):
        highlights = [
            {"id": h["id"], "updated": float(h["updated_at"]), "deleted": bool(h["deleted"]), "range": h["range"],
             "colour": h["colour"], "text": h["text"][:4000], "note": h["note"][:8000]}
            for h in connection.execute("SELECT * FROM highlights WHERE book_id=? ORDER BY id", (row["id"],))]
        bookmarks = [
            {"id": b["id"], "updated": float(b["updated_at"]), "deleted": bool(b["deleted"]),
             "position": b["position"], "text": b["text"][:1000]}
            for b in connection.execute("SELECT * FROM bookmarks WHERE book_id=? ORDER BY id", (row["id"],))]
        position = ({"cfi": row["position"], "at": float(row["position_at"] or 0), "device": row["position_device"] or ""}
                    if row["position"] else None)
        if position is None and not highlights and not bookmarks and row["shelf"] == "new":
            continue   # nothing read, nothing marked: nothing to share
        authors = json.loads(row["authors"] or "[]")
        items[row["id"]] = {
            "book": row["id"], "title": row["title"][:400], "author": ", ".join(authors)[:400],
            "shelf": row["shelf"] if row["shelf"] in SHELVES else "new",
            "shelf_at": float(row["shelf_at"]) if shelf_at else 0.0,
            "position": position, "highlights": highlights, "bookmarks": bookmarks,
        }
    if _has(connection, "synced_books"):
        for row in connection.execute("SELECT book_id, data FROM synced_books"):
            if row["book_id"] not in items:
                try:
                    items[row["book_id"]] = json.loads(row["data"])
                except ValueError:
                    continue
    return sorted(items.items())


def _fingerprint(items) -> bytes:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest().encode()


def read(path: Path):
    connection = _connect(path)
    if connection is None:
        return [], _fingerprint([])
    try:
        items = _records(connection)
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return items, _fingerprint(items)


def write(path: Path, items, expected: bytes) -> bool:
    connection = _connect(path)
    if connection is None:
        return not items
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if _fingerprint(_records(connection)) != expected:
                return False   # Leaf changed something meanwhile: next round
            for book_id, data in items:
                apply_record(connection, book_id, data)
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def apply_record(connection: sqlite3.Connection, book_id: str, data: dict) -> None:
    """Merge one reading record into Leaf's library. Leaf's importer calls this
    too, for a record that arrived before its book."""
    factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        _apply(connection, book_id, data)
    finally:
        connection.row_factory = factory


def _apply(connection: sqlite3.Connection, book_id: str, data: dict) -> None:
    row = connection.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if row is None:
        if _has(connection, "synced_books"):
            connection.execute("INSERT INTO synced_books (book_id, data) VALUES (?, ?) "
                               "ON CONFLICT(book_id) DO UPDATE SET data=excluded.data", (book_id, json.dumps(data)))
        return
    position = data.get("position")
    if position and position.get("cfi") and float(position.get("at", 0)) > float(row["position_at"] or 0):
        connection.execute("UPDATE books SET position=?, position_at=?, position_device=? WHERE id=?",
                           (position["cfi"], float(position["at"]), position.get("device", ""), book_id))
    if _has(connection, "books", "shelf_at") and data.get("shelf") in SHELVES \
            and float(data.get("shelf_at", 0)) > float(row["shelf_at"] or 0):
        connection.execute("UPDATE books SET shelf=?, shelf_at=? WHERE id=?", (data["shelf"], float(data["shelf_at"]), book_id))
    for highlight in data.get("highlights") or []:
        local = connection.execute("SELECT updated_at FROM highlights WHERE id=?", (highlight["id"],)).fetchone()
        values = (highlight["range"], highlight["colour"], highlight.get("text", ""), highlight.get("note", ""),
                  float(highlight["updated"]), int(bool(highlight.get("deleted"))))
        if local is None:
            connection.execute("INSERT INTO highlights (id, book_id, range, colour, text, note, spine, created_at, updated_at, deleted) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (highlight["id"], book_id, values[0], values[1], values[2], values[3], _spine(values[0]),
                                values[4], values[4], values[5]))
        elif float(highlight["updated"]) > float(local["updated_at"]):
            connection.execute("UPDATE highlights SET range=?, colour=?, text=?, note=?, updated_at=?, deleted=? WHERE id=?",
                               (*values, highlight["id"]))
    for mark in data.get("bookmarks") or []:
        local = connection.execute("SELECT updated_at FROM bookmarks WHERE id=?", (mark["id"],)).fetchone()
        if local is None:
            connection.execute("INSERT INTO bookmarks (id, book_id, position, text, spine, created_at, updated_at, deleted) "
                               "VALUES (?,?,?,?,?,?,?,?)",
                               (mark["id"], book_id, mark["position"], mark.get("text", ""), _spine(mark["position"]),
                                float(mark["updated"]), float(mark["updated"]), int(bool(mark.get("deleted")))))
        elif float(mark["updated"]) > float(local["updated_at"]):
            connection.execute("UPDATE bookmarks SET position=?, text=?, updated_at=?, deleted=? WHERE id=?",
                               (mark["position"], mark.get("text", ""), float(mark["updated"]), int(bool(mark.get("deleted"))), mark["id"]))
    if _has(connection, "synced_books"):
        connection.execute("DELETE FROM synced_books WHERE book_id=?", (book_id,))


def _spine(cfi: str) -> int:
    try:
        return int(cfi.split("!", 1)[0].rstrip(")").split("/")[-1]) // 2 - 1
    except (ValueError, IndexError):
        return 0
