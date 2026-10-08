#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Contacts' Together card reads Messages' stores read-only and never fails."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))

from prairie_apps.contacts_together import phone_key, together  # noqa: E402

SCHEMA = """
CREATE TABLE contacts (address TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '');
CREATE TABLE messages (uid TEXT PRIMARY KEY, address TEXT, body TEXT, timestamp INTEGER, direction TEXT);
CREATE TABLE message_attachments (uid TEXT PRIMARY KEY, message_uid TEXT, name TEXT, content_type TEXT,
                                  size INTEGER, storage_key TEXT);
"""
KEY = "a" * 64


class TogetherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="together-"))
        sms = sqlite3.connect(self.root / "messages.db")
        sms.executescript(SCHEMA)
        sms.executemany("INSERT INTO messages VALUES (?,?,?,?,?)", [
            ("s1", "+14155550199", "See you at six", 1_700_000_000, "incoming"),
            ("s2", "+15105550133", "Someone else", 1_700_000_500, "incoming")])
        sms.commit()
        sms.close()
        account = self.root / "accounts/g1"
        (account / "attachments").mkdir(parents=True)
        (account / "attachments" / KEY).write_bytes(b"deck")
        store = sqlite3.connect(account / "messages.db")
        store.executescript(SCHEMA + "CREATE TABLE conversations (id TEXT PRIMARY KEY, kind TEXT, participants TEXT);")
        store.execute("INSERT INTO conversations VALUES ('7', 'group', ?)",
                      (json.dumps([{"id": "1", "name": "Priya", "phone": "(415) 555-0199"},
                                   {"id": "2", "name": "Nora", "phone": "+1 510 555 0133"}]),))
        store.execute("INSERT INTO contacts VALUES ('7', 'Launch crew')")
        store.execute("INSERT INTO messages VALUES ('g1', '7', '', 1700000900000, 'incoming')")
        store.execute("INSERT INTO message_attachments VALUES ('a1', 'g1', 'launch-deck.stage', 'application/zip', 4, ?)",
                      (KEY,))
        store.commit()
        store.close()

    def test_conversations_newest_first_then_files(self):
        items = together("Priya Raman", "+1 (415) 555-0199", root=self.root)
        self.assertEqual([(i.kind, i.title) for i in items],
                         [("conversation", "Launch crew"), ("conversation", "Priya Raman"),
                          ("file", "launch-deck.stage")])
        self.assertEqual(items[0].timestamp, 1_700_000_900)          # milliseconds read as seconds
        self.assertEqual(items[0].subtitle, "launch-deck.stage")     # a message with only a file
        self.assertTrue(items[2].path.endswith(KEY))

    def test_nobody_and_no_stores_are_empty_not_errors(self):
        self.assertEqual(together("Sam", "+1 212 555 0107", root=self.root), ())
        self.assertEqual(together("Sam", "", root=self.root), ())
        self.assertEqual(together("Priya", "4155550199", root=self.root / "missing"), ())
        (self.root / "accounts/broken").mkdir()
        (self.root / "accounts/broken/messages.db").write_bytes(b"not a database")
        self.assertEqual(len(together("Priya", "4155550199", root=self.root)), 3)

    def test_read_only(self):
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob("*.db")}
        together("Priya", "4155550199", root=self.root)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.root.rglob("*.db")})

    def test_the_account_holder_shares_nothing_with_themselves(self):
        store = sqlite3.connect(self.root / "accounts/g1/messages.db")
        me = {"id": "0", "name": "Me", "phone": "+1 913 555 0100"}
        for n, other in enumerate(("+1 212 555 0101", "+1 212 555 0102", "+1 212 555 0103"), start=10):
            store.execute("INSERT INTO conversations VALUES (?, 'direct', ?)",
                          (str(n), json.dumps([me, {"id": str(n), "name": "", "phone": other}])))
            store.execute("INSERT INTO messages VALUES (?, ?, 'hi', 1700001000, 'incoming')", (f"m{n}", str(n)))
        store.commit()
        store.close()
        self.assertEqual(together("Me", "913-555-0100", root=self.root), ())
        self.assertEqual([i.title for i in together("Sam", "+12125550102", root=self.root)], ["Sam"])

    def test_phone_key(self):
        self.assertEqual(phone_key("+1 (415) 555-0199"), phone_key("4155550199"))
        self.assertEqual(phone_key("#225"), "225")


if __name__ == "__main__":
    unittest.main()
