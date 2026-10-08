# SPDX-License-Identifier: Apache-2.0
"""Conversations, messages and steps, in SQLite on this machine."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import paths
from .persistence import backup_database_once

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY, conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  message TEXT NOT NULL, tool TEXT NOT NULL, summary TEXT NOT NULL, undo TEXT,
  state TEXT NOT NULL DEFAULT 'done', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation, created);
CREATE INDEX IF NOT EXISTS steps_by_conversation ON steps(conversation, created);
"""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        database = path or paths.database_path()
        existing = str(database) != ":memory:" and Path(database).is_file() and Path(database).stat().st_size > 0
        self.db = sqlite3.connect(database, check_same_thread=False)
        if existing:
            backup_database_once(self.db, database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def new_conversation(self, title: str = "New conversation", model: str = "") -> str:
        identity = uuid.uuid4().hex
        now = time.time()
        with self.lock, self.db:
            self.db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?)", (identity, title, model, now, now))
        return identity

    def conversations(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, title, model, updated FROM conversations ORDER BY updated DESC").fetchall()
        return [dict(row) for row in rows]

    def exists(self, conversation: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation,)).fetchone() is not None

    def rename_if_new(self, conversation: str, text: str) -> None:
        title = " ".join(text.split())[:48] or "New conversation"
        with self.lock, self.db:
            self.db.execute("UPDATE conversations SET title = ? WHERE id = ? AND title = 'New conversation'",
                            (title, conversation))

    def add_message(self, conversation: str, role: str, content: str, meta: dict | None = None) -> str:
        identity = uuid.uuid4().hex
        now = time.time()
        with self.lock, self.db:
            self.db.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                            (identity, conversation, role, content, json.dumps(meta or {}), now))
            self.db.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, conversation))
        return identity

    def messages(self, conversation: str, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM (SELECT * FROM messages WHERE conversation = ? "
                                   "ORDER BY created DESC LIMIT ?) ORDER BY created", (conversation, limit)).fetchall()
        return [{**dict(row), "meta": json.loads(row["meta"])} for row in rows]

    def add_step(self, conversation: str, message: str, tool: str, summary: str, undo: dict | None) -> str:
        identity = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?, 'done', ?)",
                            (identity, conversation, message, tool, summary,
                             json.dumps(undo) if undo else None, time.time()))
        return identity

    def steps(self, conversation: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM steps WHERE conversation = ? ORDER BY created", (conversation,)).fetchall()
        return [{**dict(row), "undo": json.loads(row["undo"]) if row["undo"] else None} for row in rows]

    def recent_steps(self, since: float, limit: int = 8) -> list[dict]:
        """Changes in every conversation since a time, newest last."""
        with self.lock:
            rows = self.db.execute("SELECT tool, summary, state, created FROM steps WHERE created >= ? "
                                   "ORDER BY created DESC LIMIT ?", (since, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def step(self, identity: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM steps WHERE id = ?", (identity,)).fetchone()
        return {**dict(row), "undo": json.loads(row["undo"]) if row["undo"] else None} if row else None

    def mark_step(self, identity: str, state: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE steps SET state = ? WHERE id = ?", (state, identity))

    def delete_conversation(self, conversation: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM conversations WHERE id = ?", (conversation,))

    def clear(self) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM conversations")
