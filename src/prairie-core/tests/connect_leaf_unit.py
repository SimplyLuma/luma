# SPDX-License-Identifier: Apache-2.0
"""Leaf reading records: read from Leaf's library, merged back without losing reading."""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from prairie_apps import connect_leaf  # noqa: E402

SCHEMA = """
CREATE TABLE books (id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '[]', language TEXT, identifier TEXT,
  path TEXT, source TEXT, remote_url TEXT, cover TEXT, stats TEXT, shelf TEXT NOT NULL DEFAULT 'new', added_at REAL NOT NULL,
  position TEXT, position_at REAL, position_device TEXT, missing INTEGER NOT NULL DEFAULT 0, shelf_at REAL NOT NULL DEFAULT 0);
CREATE TABLE highlights (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, range TEXT NOT NULL, colour TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', spine INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE bookmarks (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, position TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', spine INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE synced_books (book_id TEXT PRIMARY KEY, data TEXT NOT NULL);
"""


class LeafRecords(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.directory.name) / "leaf" / "library.sqlite3"
        self.path.parent.mkdir()
        db = sqlite3.connect(self.path)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO books (id, title, authors, shelf, added_at, position, position_at, position_device, shelf_at) "
                   "VALUES ('b1', 'Emma', '[\"Jane Austen\"]', 'reading', 1, 'epubcfi(/6/4!/4/2/1:0)', 300, 'desk', 50)")
        db.execute("INSERT INTO books (id, title, shelf, added_at) VALUES ('b2', 'Unread', 'new', 1)")
        db.execute("INSERT INTO highlights VALUES ('h1', 'b1', 'epubcfi(/6/4!/4/2,/1:0,/1:5)', 'y', 'Emma.', '', 1, 10, 10, 0)")
        db.commit()
        db.close()

    def tearDown(self):
        self.directory.cleanup()

    def test_only_books_with_reading_are_shared(self):
        items, _raw = connect_leaf.read(self.path)
        self.assertEqual([uid for uid, _ in items], ["b1"])
        record = items[0][1]
        self.assertEqual(record["position"], {"cfi": "epubcfi(/6/4!/4/2/1:0)", "at": 300.0, "device": "desk"})
        self.assertEqual(record["author"], "Jane Austen")

    def test_merge_never_moves_back_and_combines_marks(self):
        items, raw = connect_leaf.read(self.path)
        record = dict(items[0][1])
        record["position"] = {"cfi": "epubcfi(/6/2!/4/2/1:0)", "at": 100.0, "device": "phone"}   # older
        record["highlights"] = [dict(record["highlights"][0], updated=20.0, deleted=True),
                                {"id": "h2", "updated": 30.0, "deleted": False, "range": "epubcfi(/6/6!/4/2,/1:0,/1:3)",
                                 "colour": "b", "text": "Hi.", "note": "n"}]
        self.assertTrue(connect_leaf.write(self.path, [("b1", record)], raw))
        db = sqlite3.connect(self.path)
        self.assertEqual(db.execute("SELECT position FROM books WHERE id='b1'").fetchone()[0], "epubcfi(/6/4!/4/2/1:0)")
        self.assertEqual(db.execute("SELECT deleted FROM highlights WHERE id='h1'").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT spine FROM highlights WHERE id='h2'").fetchone()[0], 2)
        db.close()

    def test_a_record_for_a_missing_book_waits(self):
        items, raw = connect_leaf.read(self.path)
        other = {"book": "b9", "title": "Persuasion", "author": "", "shelf": "reading", "shelf_at": 5.0,
                 "position": {"cfi": "epubcfi(/6/8!/4/2/1:0)", "at": 900.0, "device": "phone"}, "highlights": [], "bookmarks": []}
        self.assertTrue(connect_leaf.write(self.path, items + [("b9", other)], raw))
        items, _ = connect_leaf.read(self.path)
        self.assertEqual([uid for uid, _ in items], ["b1", "b9"], "a waiting record is not mistaken for a removal")
        db = sqlite3.connect(self.path)
        db.execute("INSERT INTO books (id, title, shelf, added_at) VALUES ('b9', 'Persuasion', 'new', 1)")
        connect_leaf.apply_record(db, "b9", json.loads(db.execute("SELECT data FROM synced_books").fetchone()[0]))
        self.assertEqual(db.execute("SELECT position, shelf FROM books WHERE id='b9'").fetchone(), ("epubcfi(/6/8!/4/2/1:0)", "reading"))
        db.close()

    def test_a_change_during_sync_is_not_overwritten(self):
        items, raw = connect_leaf.read(self.path)
        db = sqlite3.connect(self.path)
        db.execute("UPDATE books SET position='epubcfi(/6/10!/4/2/1:0)', position_at=400 WHERE id='b1'")
        db.commit()
        db.close()
        self.assertFalse(connect_leaf.write(self.path, items, raw))


if __name__ == "__main__":
    unittest.main()
