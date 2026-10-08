# SPDX-License-Identifier: Apache-2.0
"""Durable sticky-note storage: private, transactional, forward-only.

A sticky note is a small amount of text a person expects to survive a crash,
an update and a rollback, so it is kept the way Tide keeps a library and
Notes keeps a page: one SQLite file under the user's own data directory,
opened with foreign keys on and synchronous writes, migrated forward by
``PRAGMA user_version`` and never by guessing at a table's shape.

Note text never reaches a log. This module does not import ``logging`` and
raises errors that name the note's identifier, never its contents.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

# The five papers the design offers. The names are the document's data; what
# each one actually looks like belongs to the style sheet, so a change of
# palette never has to rewrite anybody's notes.
COLOURS: tuple[str, ...] = ("yellow", "coral", "green", "blue", "purple")
DEFAULT_COLOUR = "yellow"

# A deleted note is recoverable for this long. Long enough that "I deleted the
# wrong one" is always fixable; short enough that the trash is not a second,
# invisible pile of notes.
TRASH_RETENTION_DAYS = 30

_TITLE_LIMIT = 120
_BODY_LIMIT = 64 * 1024


@dataclass(frozen=True, slots=True)
class Note:
    id: str
    title: str
    body: str
    colour: str
    pinned: bool
    completed_at: str | None
    deleted_at: str | None
    created_at: str
    modified_at: str
    sort_order: int

    @property
    def completed(self) -> bool:
        return self.completed_at is not None

    @property
    def display_title(self) -> str:
        """What the note calls itself in a window title or a screen reader."""
        return self.title or "Untitled note"

    @property
    def tab_title(self) -> str:
        """The dock's spine text: the note's own words, set as a tab.

        The tab is a few characters of a rotated word, so it is upper-cased
        here rather than by the sheet — GTK's `text-transform` is not
        available to a Pango layout drawn by hand.
        """
        return (self.title or "NOTE").upper()


class StickyNotesStore:
    """A serialised SQLite store with atomic, forward-only migrations."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else data_directory() / "notes.sqlite3"
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir() only applies its mode when it creates the directory. A
        # directory that already existed — from an older release, or from a
        # restored backup — is made private here, so "notes are private" is a
        # property of every launch rather than of the first one.
        with contextlib.suppress(OSError):
            os.chmod(directory, 0o700)
        self._connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        # SQLite copies the main database's permissions onto its -wal and -shm
        # companions, so narrowing the database narrows all three.
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "StickyNotesStore":
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def _migrate(self) -> None:
        # executescript() owns its own transaction boundary, so the script
        # states BEGIN and COMMIT and lands as one atomic migration.
        database = self._connection
        version = int(database.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Sticky Notes schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        if version == 0:
            database.executescript(
                f"""
                BEGIN IMMEDIATE;
                CREATE TABLE notes (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL DEFAULT '',
                  body TEXT NOT NULL DEFAULT '',
                  colour TEXT NOT NULL DEFAULT '{DEFAULT_COLOUR}'
                    CHECK (colour IN ({", ".join(f"'{name}'" for name in COLOURS)})),
                  pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                  completed_at TEXT,
                  deleted_at TEXT,
                  created_at TEXT NOT NULL,
                  modified_at TEXT NOT NULL,
                  sort_order INTEGER NOT NULL
                );
                CREATE INDEX notes_dock_order
                  ON notes(deleted_at, pinned DESC, sort_order, id);
                PRAGMA user_version = {SCHEMA_VERSION};
                COMMIT;
                """
            )

    # ── Reading ──────────────────────────────────────────────────────────

    def get_note(self, note_id: str) -> Note:
        row = self._connection.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if row is None:
            raise KeyError(note_id)
        return _note(row)

    def list_notes(
        self, *, include_completed: bool = True, deleted: bool = False
    ) -> tuple[Note, ...]:
        """The dock's order: pinned notes first, then the order they were made.

        Editing a note must not move its tab — a stack whose tabs reshuffle
        as you type cannot be aimed at — so this never orders by modification
        time.
        """
        clauses = ["deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"]
        if not include_completed:
            clauses.append("completed_at IS NULL")
        rows = self._connection.execute(
            "SELECT * FROM notes WHERE "
            + " AND ".join(clauses)
            + " ORDER BY pinned DESC, sort_order ASC, id ASC"
        )
        return tuple(_note(row) for row in rows)

    # ── Writing ──────────────────────────────────────────────────────────

    def create_note(self, *, title: str = "", colour: str = DEFAULT_COLOUR) -> Note:
        note_id, now = str(uuid.uuid4()), _now()
        with self.transaction() as database:
            order = int(
                database.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM notes"
                ).fetchone()[0]
            )
            database.execute(
                """INSERT INTO notes
                   (id, title, body, colour, pinned, created_at, modified_at, sort_order)
                   VALUES (?, ?, '', ?, 0, ?, ?, ?)""",
                (note_id, _clean_title(title), _clean_colour(colour), now, now, order),
            )
        return self.get_note(note_id)

    def update_note(self, note_id: str, *, title: str, body: str) -> Note:
        return self._write(
            note_id,
            "UPDATE notes SET title = ?, body = ?, modified_at = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (_clean_title(title), _clean_body(body), _now(), note_id),
        )

    def rename_note(self, note_id: str, title: str) -> Note:
        return self._write(
            note_id,
            "UPDATE notes SET title = ?, modified_at = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (_clean_title(title), _now(), note_id),
        )

    def set_colour(self, note_id: str, colour: str) -> Note:
        return self._write(
            note_id,
            "UPDATE notes SET colour = ?, modified_at = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (_clean_colour(colour), _now(), note_id),
        )

    def set_pinned(self, note_id: str, pinned: bool) -> Note:
        return self._write(
            note_id,
            "UPDATE notes SET pinned = ?, modified_at = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (int(bool(pinned)), _now(), note_id),
        )

    def set_complete(self, note_id: str, complete: bool) -> Note:
        """Completion is a state a note carries, not a quiet way to delete it.

        A finished note keeps its text, its colour and its place in the stack;
        it simply says it is done. Nothing here removes a row.
        """
        return self._write(
            note_id,
            "UPDATE notes SET completed_at = ?, modified_at = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (_now() if complete else None, _now(), note_id),
        )

    def delete_note(self, note_id: str) -> Note:
        """Move a note to the trash. Recoverable; see restore_note()."""
        return self._write(
            note_id,
            "UPDATE notes SET deleted_at = ?, modified_at = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (_now(), _now(), note_id),
        )

    def restore_note(self, note_id: str) -> Note:
        return self._write(
            note_id,
            "UPDATE notes SET deleted_at = NULL, modified_at = ?"
            " WHERE id = ? AND deleted_at IS NOT NULL",
            (_now(), note_id),
        )

    def purge_note(self, note_id: str) -> None:
        """Remove a trashed note for good. Only the trash can be purged."""
        with self.transaction() as database:
            cursor = database.execute(
                "DELETE FROM notes WHERE id = ? AND deleted_at IS NOT NULL", (note_id,)
            )
        if cursor.rowcount != 1:
            raise KeyError(note_id)

    def purge_expired(self, *, days: int = TRASH_RETENTION_DAYS) -> int:
        """Empty the part of the trash old enough that nobody is coming back.

        Called once at start-up so the retention window is a property of the
        store rather than of a timer that has to keep running.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0, days))).isoformat(
            timespec="microseconds"
        )
        with self.transaction() as database:
            cursor = database.execute(
                "DELETE FROM notes WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff,),
            )
        return int(cursor.rowcount)

    def _write(self, note_id: str, statement: str, values: tuple[object, ...]) -> Note:
        """Apply one guarded update, or say the note was not there to update.

        Every statement carries its own precondition — a live note, or a
        trashed one — so a window that has been open across a deletion gets a
        KeyError rather than quietly writing nothing.
        """
        with self.transaction() as database:
            cursor = database.execute(statement, values)
        if cursor.rowcount != 1:
            raise KeyError(note_id)
        return self.get_note(note_id)


def data_directory(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    default = Path(env.get("HOME", str(Path.home()))) / ".local" / "share"
    return Path(env.get("XDG_DATA_HOME") or default) / "luma" / "sticky-notes"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# Control characters become a space rather than nothing. Deleting them welds
# the words on either side together — "lines\x00here" silently becomes
# "lineshere" — which is a quiet corruption of somebody's text, not a clean-up.
_TITLE_CONTROLS = {code: " " for code in range(0x20)} | {0x7F: " "}
# A body keeps its own line breaks and tabs; everything else below space goes.
_BODY_CONTROLS = {
    code: " " for code in range(0x20) if code not in (0x09, 0x0A, 0x0D)
} | {0x7F: " "}


def _clean_title(title: str) -> str:
    """A title is one line of plain text, and it is the tab's whole vocabulary."""
    return " ".join(title.translate(_TITLE_CONTROLS).split()).strip()[:_TITLE_LIMIT]


def _clean_body(body: str) -> str:
    if len(body) > _BODY_LIMIT:
        raise ValueError("a sticky note is longer than this store accepts")
    return body.translate(_BODY_CONTROLS)


def _clean_colour(colour: str) -> str:
    if colour not in COLOURS:
        raise ValueError(f"unknown sticky-note colour: {colour!r}")
    return colour


def _note(row: sqlite3.Row) -> Note:
    return Note(
        id=row["id"],
        title=row["title"],
        body=row["body"],
        colour=row["colour"],
        pinned=bool(row["pinned"]),
        completed_at=row["completed_at"],
        deleted_at=row["deleted_at"],
        created_at=row["created_at"],
        modified_at=row["modified_at"],
        sort_order=int(row["sort_order"]),
    )
