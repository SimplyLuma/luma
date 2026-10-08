# SPDX-License-Identifier: Apache-2.0

"""Durable Notes model plus portable Markdown compatibility functions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

_ROOT_UNCHANGED = object()


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    body: str
    runs: tuple[dict[str, object], ...]
    created_at: str
    modified_at: str
    folder_id: str | None
    favorite: bool
    sort_order: int
    deleted_at: str | None

    @property
    def preview(self) -> str:
        lines = [text for line in self.plain_text.splitlines()
                 if (text := line.replace("\ufffc", "").strip())]
        if lines:
            return lines[0]
        return "Picture" if "\ufffc" in self.body else "No additional text"

    @property
    def plain_text(self) -> str:
        """Readable attachment labels without changing stored offsets."""
        files = {int(run['start']): str(run['name']) for run in self.runs
                 if run.get('style') == 'file'}
        return ''.join(files.get(index, char) for index, char in enumerate(self.body))

    @property
    def display_title(self) -> str:
        return self.title or "Untitled page"


@dataclass(frozen=True)
class Folder:
    id: str
    name: str
    favorite: bool
    sort_order: int
    expanded: bool


class NotesStore:
    """Private transactional note/folder storage with explicit stable order."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or notes_data_directory() / "notes.sqlite3"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._create_schema()
        if path is None:
            self._import_legacy_markdown_once()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS folders (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
                    sort_order INTEGER NOT NULL,
                    expanded INTEGER NOT NULL DEFAULT 1 CHECK(expanded IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    runs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
                    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
                    sort_order INTEGER NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS notes_visible_order
                    ON notes(deleted_at, favorite DESC, folder_id, sort_order, modified_at DESC);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                -- "<note id>|<etag>" of every version of a page kept here as an
                -- other copy, by Notes or by sync: the same version is never
                -- kept twice, whichever of them sees the conflict.
                CREATE TABLE IF NOT EXISTS kept_versions (version TEXT PRIMARY KEY, kept_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS note_hierarchy (
                    child_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
                    parent_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    CHECK(child_id != parent_id)
                );
                """
            )

    def data_version(self) -> int:
        """Moves only when another connection (another process: sync, or a
        second Notes) commits to the library; never for this one's own writes."""
        return int(self.connection.execute("PRAGMA data_version").fetchone()[0])

    def has_kept_version(self, version: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM kept_versions WHERE version=?", (version,)).fetchone() is not None

    def remember_kept_version(self, version: str) -> bool:
        """False when this version was already kept."""
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO kept_versions(version,kept_at) VALUES (?,?)", (version, _now()))
        return cursor.rowcount == 1

    def keep_other_copy(self, version: str, *, title: str, body: str,
                        runs: tuple[dict[str, object], ...], folder_id: str | None) -> Note | None:
        """Keep one version of a page as a page of its own, once: the guard
        and the copy are one transaction, so a retried save never makes a
        second copy and a crash never records a copy that was not made."""
        _validate_runs(body, runs)
        if any(run.get("style") == "file" for run in runs):
            self._backup_before_v70_write("files")
        if any(run.get("style") in V70_RUN_STYLES for run in runs):
            self._backup_before_v70_write()
        if folder_id is not None and self.connection.execute(
                "SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone() is None:
            folder_id = None
        note_id, now = str(uuid.uuid4()), _now()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO kept_versions(version,kept_at) VALUES (?,?)", (version, now))
            if cursor.rowcount != 1:
                return None
            self.connection.execute(
                """INSERT INTO notes
                   (id,title,body,runs_json,created_at,modified_at,folder_id,favorite,sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (note_id, _clean_note_title(title), body, json.dumps(runs, separators=(",", ":")),
                 now, now, folder_id, 0, self._next_order("notes", folder_id=folder_id)),
            )
        return self.get_note(note_id)

    def create_note(self, *, folder_id: str | None = None, title: str = "",
                    parent_id: str | None = None, first: bool = False,
                    body: str = "", runs: tuple[dict[str, object], ...] = ()) -> Note:
        _validate_runs(body, runs)
        if folder_id is not None:
            self.get_folder(folder_id)
        if parent_id is not None:
            parent = self.get_note(parent_id)
            if parent.deleted_at is not None or parent.folder_id != folder_id:
                raise ValueError("The parent must be a visible note in the destination folder")
        if any(run.get('style') == 'file' for run in runs):
            self._backup_before_v70_write('files')
        if any(run.get('style') in V70_RUN_STYLES for run in runs):
            self._backup_before_v70_write()
        note_id, now = str(uuid.uuid4()), _now()
        with self.connection:
            order = self._next_order("notes", folder_id=folder_id)
            if first:
                # Creation inserts at the start of its scope without rewriting
                # existing rows or changing drag/favorite partition semantics.
                row = self.connection.execute(
                    """SELECT MIN(n.sort_order) FROM notes n
                       LEFT JOIN note_hierarchy h ON h.child_id=n.id
                       WHERE n.deleted_at IS NULL AND n.folder_id IS ? AND h.parent_id IS ?""",
                    (folder_id, parent_id),
                ).fetchone()
                order = min(0, row[0] if row[0] is not None else 0) - 1
            self.connection.execute(
                """INSERT INTO notes
                   (id,title,body,runs_json,created_at,modified_at,folder_id,favorite,sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (note_id, _clean_note_title(title), body, json.dumps(runs, separators=(",", ":")), now, now, folder_id, 0, order),
            )
            if parent_id is not None:
                self.connection.execute("INSERT INTO note_hierarchy VALUES (?,?)", (note_id, parent_id))
        return self.get_note(note_id)

    def get_note(self, note_id: str) -> Note:
        row = self.connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            raise KeyError(note_id)
        return _note(row)

    def list_notes(self, *, folder_id: str | None = None, search: str = "", deleted: bool = False) -> tuple[Note, ...]:
        clauses = ["deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"]
        values: list[object] = []
        if folder_id == "__root__":
            clauses.append("folder_id IS NULL")
        elif folder_id is not None:
            clauses.append("folder_id=?"); values.append(folder_id)
        needle = search.strip()
        if needle:
            clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
            escaped = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            values.extend((escaped, escaped))
        rows = self.connection.execute(
            "SELECT * FROM notes WHERE " + " AND ".join(clauses)
            + " ORDER BY favorite DESC, sort_order ASC, modified_at DESC, id ASC", values,
        )
        return tuple(_note(row) for row in rows)

    def update_note(self, note_id: str, *, title: str, body: str,
                    runs: tuple[dict[str, object], ...] = (), expected: object = ...) -> Note:
        _validate_runs(body, runs)
        if any(run.get("style") == "file" for run in runs):
            self._backup_before_v70_write("files")
        if any(run.get("style") in V70_RUN_STYLES for run in runs):
            self._backup_before_v70_write()
        with self.connection:
            if expected is not ...:
                self.connection.execute('BEGIN IMMEDIATE')
                row = self.connection.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
                if _content(_note(row) if row is not None else None) != _content(expected):
                    raise NoteChangedError(note_id)
            cursor = self.connection.execute(
                """UPDATE notes SET title=?,body=?,runs_json=?,modified_at=?
                   WHERE id=? AND deleted_at IS NULL""",
                (_clean_note_title(title), body, json.dumps(runs, separators=(",", ":")), _now(), note_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(note_id)
        return self.get_note(note_id)

    def put_synced_note(self, note_id: str, *, title: str, body: str, runs: tuple[dict[str, object], ...],
                        folder_id: str | None, folder_name: str, favorite: bool,
                        created_at: str, modified_at: str, expected: object = ...) -> Note:
        """Write a page as another device left it, keeping its id and times.

        A page in Recently Deleted comes back. A folder this device does not
        have yet is made, with the same id, so the page's version matches.
        With ``expected`` (the page as it was read here, or None for no page),
        nothing is written if the page changed here since: NoteChangedError.
        """
        _validate_runs(body, runs)
        if any(run.get("style") == "file" for run in runs):
            self._backup_before_v70_write("files")
        if any(run.get("style") in V70_RUN_STYLES for run in runs):
            self._backup_before_v70_write()
        with self.connection:
            if expected is not ...:
                row = self.connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
                if _content(_note(row) if row is not None else None) != _content(expected):
                    raise NoteChangedError(note_id)
            if folder_id is not None and self.connection.execute(
                    "SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone() is None:
                name = " ".join(folder_name.split()).strip()[:240] or "Notes"
                self.connection.execute(
                    "INSERT INTO folders(id,name,favorite,sort_order,expanded) VALUES (?,?,0,?,1)",
                    (folder_id, name, self._next_order("folders")))
            row = self.connection.execute("SELECT folder_id,deleted_at FROM notes WHERE id=?",
                                          (note_id,)).fetchone()
            values = (_clean_note_title(title), body, json.dumps(runs, separators=(",", ":")),
                      created_at, modified_at, folder_id, int(bool(favorite)))
            if row is None:
                self.connection.execute(
                    """INSERT INTO notes
                       (title,body,runs_json,created_at,modified_at,folder_id,favorite,id,sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (*values, note_id, self._next_order("notes", folder_id=folder_id)))
            else:
                moved = row["folder_id"] != folder_id or row["deleted_at"] is not None
                order = self._next_order("notes", folder_id=folder_id) if moved else None
                self.connection.execute(
                    """UPDATE notes SET title=?,body=?,runs_json=?,created_at=?,modified_at=?,folder_id=?,
                       favorite=?,deleted_at=NULL,sort_order=COALESCE(?,sort_order) WHERE id=?""",
                    (*values, order, note_id))
        return self.get_note(note_id)

    def _backup_before_v70_write(self, kind: str = "v70") -> None:
        """Take a consistent SQLite snapshot once, before new format/mark writes."""
        if getattr(self, "fixture", False):
            return
        if kind not in ("v70", "files"):
            raise ValueError("unknown backup kind")
        key = kind + "-write-backup"
        if self.connection.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone():
            return
        target = self.path.with_name(self.path.name + '.before-' + kind + '-' + str(uuid.uuid4()))
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            backup = sqlite3.connect(target)
            try:
                self.connection.backup(backup)
                backup.execute('PRAGMA journal_mode=DELETE')
            finally:
                backup.close()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        with self.connection:
            self.connection.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (key, str(target)))

    def get_mark(self, kind: str, item_id: str) -> dict | None:
        if kind not in ('note', 'folder'):
            raise ValueError('mark subject must be a note or folder')
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (f'mark:{kind}:{item_id}',)).fetchone()
        return json.loads(row[0]) if row else None

    def folder_parents(self) -> dict[str, str]:
        folders = {folder.id for folder in self.list_folders()}
        return {row[0].removeprefix('folder-parent:'): row[1]
                for row in self.connection.execute("SELECT key,value FROM metadata WHERE key LIKE 'folder-parent:%'")
                if row[0].removeprefix('folder-parent:') in folders and row[1] in folders}

    def set_folder_parent(self, folder_id: str, parent_id: str | None) -> Folder:
        return self.move_folder(folder_id, parent_id)

    def move_folder(self, folder_id: str, parent_id: str | None, *,
                    root_before: object = _ROOT_UNCHANGED,
                    expand_parent: bool = False) -> Folder:
        """Persist a folder's parent and sidebar position together."""
        folder = self.get_folder(folder_id)
        if parent_id is not None:
            self.get_folder(parent_id)
        parents = self.folder_parents()
        ancestor, visited = parent_id, set()
        while ancestor is not None:
            if ancestor == folder_id or ancestor in visited:
                raise ValueError('a folder cannot be inside itself or its descendants')
            visited.add(ancestor)
            ancestor = parents.get(ancestor)
        if (parents.get(folder_id) == parent_id and root_before is _ROOT_UNCHANGED
                and not expand_parent):
            return folder
        self._backup_before_v70_write()
        with self.connection:
            key = 'folder-parent:' + folder_id
            if parent_id is None:
                self.connection.execute('DELETE FROM metadata WHERE key=?', (key,))
            else:
                self.connection.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)', (key, parent_id))
            if root_before is not _ROOT_UNCHANGED:
                self._place_root('folder', folder_id, root_before)
            if expand_parent and parent_id is not None:
                self.connection.execute('UPDATE folders SET expanded=1 WHERE id=?', (parent_id,))
        return folder

    def set_mark(self, kind: str, item_id: str, mark: dict | None) -> None:
        if kind not in ('note', 'folder'):
            raise ValueError('mark subject must be a note or folder')
        (self.get_note if kind == 'note' else self.get_folder)(item_id)
        _validate_mark(mark)
        self._backup_before_v70_write()
        key = f'mark:{kind}:{item_id}'
        with self.connection:
            if mark is None:
                self.connection.execute('DELETE FROM metadata WHERE key=?', (key,))
            else:
                self.connection.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)', (key, json.dumps(mark)))

    def rename_note(self, note_id: str, title: str) -> Note:
        note = self.get_note(note_id)
        return self.update_note(note_id, title=title, body=note.body, runs=note.runs)

    def set_note_favorite(self, note_id: str, favorite: bool) -> Note:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE notes SET favorite=?,modified_at=? WHERE id=? AND deleted_at IS NULL",
                (int(favorite), _now(), note_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(note_id)
        self._normalize_note_order(self.get_note(note_id).folder_id)
        return self.get_note(note_id)

    def soft_delete_note(self, note_id: str) -> Note:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE notes SET deleted_at=?,modified_at=? WHERE id=? AND deleted_at IS NULL",
                (_now(), _now(), note_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(note_id)
        return self.get_note(note_id)

    def restore_note(self, note_id: str) -> Note:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE notes SET deleted_at=NULL,modified_at=? WHERE id=? AND deleted_at IS NOT NULL",
                (_now(), note_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(note_id)
        return self.get_note(note_id)

    def note_parents(self) -> dict[str, str]:
        """Local sidebar relationships whose endpoints are still in one scope.

        Sync can move or trash a parent independently. Its children remain
        visible at the scope root instead of disappearing with that parent.
        """
        return dict(self.connection.execute(
            """SELECT h.child_id,h.parent_id FROM note_hierarchy h
               JOIN notes c ON c.id=h.child_id JOIN notes p ON p.id=h.parent_id
               WHERE c.deleted_at IS NULL AND p.deleted_at IS NULL
                 AND c.folder_id IS p.folder_id"""))

    def parent_of(self, note_id: str) -> str | None:
        return self.note_parents().get(note_id)

    @staticmethod
    def root_key(item: Note | Folder) -> str:
        return ("note:" if isinstance(item, Note) else "folder:") + item.id

    def root_items(self) -> tuple[Note | Folder, ...]:
        """Notes and folders share one durable, local sidebar order."""
        parents = self.note_parents()
        items = [n for n in self.list_notes(folder_id="__root__") if n.id not in parents]
        items.extend(self.list_folders())
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key='sidebar-root-order'").fetchone()
        keys = json.loads(row[0]) if row else []
        rank = {key: index for index, key in enumerate(keys)}
        return tuple(sorted(items, key=lambda item: rank.get(self.root_key(item), len(rank))))

    def _place_root(self, kind: str, item_id: str, before: str | None) -> None:
        key = kind + ":" + item_id
        keys = [self.root_key(item) for item in self.root_items()]
        if key not in keys:
            raise KeyError(key)
        if before == key:
            return
        keys.remove(key)
        if before is not None and before not in keys:
            raise ValueError("The insertion target is no longer at the top level")
        keys.insert(keys.index(before) if before is not None else len(keys), key)
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('sidebar-root-order',?)",
            (json.dumps(keys),))

    def place_root(self, kind: str, item_id: str, before: str | None) -> None:
        if kind == "note":
            self.move_note(item_id, None, root_before=before)
        elif kind == "folder":
            with self.connection:
                self._place_root(kind, item_id, before)
        else:
            raise ValueError("Unknown sidebar item")

    def descendants(self, note_id: str, *, include_hidden: bool = False) -> set[str]:
        parents = (dict(self.connection.execute("SELECT child_id,parent_id FROM note_hierarchy"))
                   if include_hidden else self.note_parents())
        found, pending = set(), [note_id]
        while pending:
            parent = pending.pop()
            for child, owner in parents.items():
                if owner == parent and child not in found:
                    found.add(child)
                    pending.append(child)
        return found

    def reorder_note(self, note_id: str, before_id: str | None) -> None:
        with self.connection:
            self._reorder_note(note_id, before_id)

    def _reorder_note(self, note_id: str, before_id: str | None) -> None:
        moving = self.get_note(note_id)
        parents = self.note_parents()
        siblings = [
            note for note in self.list_notes(folder_id=moving.folder_id or "__root__")
            if note.id != note_id and note.favorite == moving.favorite
            and parents.get(note.id) == parents.get(note_id)
        ]
        index = len(siblings)
        if before_id is not None:
            index = next((i for i, item in enumerate(siblings) if item.id == before_id), index)
        siblings.insert(index, moving)
        for order, note in enumerate(siblings):
            self.connection.execute("UPDATE notes SET sort_order=? WHERE id=?", (order, note.id))

    def move_note(
        self,
        note_id: str,
        folder_id: str | None,
        *,
        before_id: str | None = None,
        parent_id: str | None = None,
        root_before: object = _ROOT_UNCHANGED,
    ) -> Note:
        """Move a note between scopes while preserving its favorite partition."""
        moving = self.get_note(note_id)
        if moving.deleted_at is not None:
            raise ValueError("Cannot move a deleted note")
        children = self.descendants(note_id)
        if parent_id == note_id or parent_id in self.descendants(note_id, include_hidden=True):
            raise ValueError("A note cannot contain itself or its ancestor")
        if folder_id is not None:
            self.get_folder(folder_id)
        if parent_id is not None:
            parent = self.get_note(parent_id)
            if parent.deleted_at is not None or parent.folder_id != folder_id:
                raise ValueError("The parent must be a visible note in the destination folder")
        with self.connection:
            self.connection.execute("DELETE FROM note_hierarchy WHERE child_id=?", (note_id,))
            if parent_id is not None:
                self.connection.execute("INSERT INTO note_hierarchy VALUES (?,?)", (note_id, parent_id))
            if moving.folder_id != folder_id:
                self.connection.executemany(
                    "UPDATE notes SET folder_id=?,modified_at=? WHERE id=?",
                    [(folder_id, _now(), child) for child in children])
            self.connection.execute(
                "UPDATE notes SET folder_id=?,sort_order=?,modified_at=? WHERE id=?",
                (folder_id, self._next_order("notes", folder_id=folder_id), _now(), note_id),
            )
            self._reorder_note(note_id, before_id)
            if root_before is not _ROOT_UNCHANGED:
                if folder_id is not None or parent_id is not None:
                    raise ValueError("Root insertion requires a root note")
                self._place_root("note", note_id, root_before)
        return self.get_note(note_id)

    def create_folder(self, name: str = "New folder", *, parent_id: str | None = None,
                      mark: dict | None = None) -> Folder:
        """Create a folder and its optional v70 metadata in one transaction."""
        name = _clean_required_name(name)
        if parent_id is not None:
            self.get_folder(parent_id)
        _validate_mark(mark)
        if parent_id is not None or mark is not None:
            self._backup_before_v70_write()
        folder_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                "INSERT INTO folders(id,name,favorite,sort_order,expanded) VALUES(?,?,?,?,1)",
                (folder_id, name, 0, self._next_order("folders")),
            )
            if mark is not None:
                self.connection.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                                        ('mark:folder:' + folder_id, json.dumps(mark)))
            if parent_id is not None:
                self.connection.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                                        ('folder-parent:' + folder_id, parent_id))
        return self.get_folder(folder_id)

    def get_folder(self, folder_id: str) -> Folder:
        row = self.connection.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
        if row is None:
            raise KeyError(folder_id)
        return _folder(row)

    def list_folders(self) -> tuple[Folder, ...]:
        rows = self.connection.execute(
            "SELECT * FROM folders ORDER BY favorite DESC,sort_order ASC,name COLLATE NOCASE,id"
        )
        return tuple(_folder(row) for row in rows)

    def rename_folder(self, folder_id: str, name: str) -> Folder:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE folders SET name=? WHERE id=?",
                (_clean_required_name(name), folder_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(folder_id)
        return self.get_folder(folder_id)

    def set_folder_favorite(self, folder_id: str, favorite: bool) -> Folder:
        with self.connection:
            cursor = self.connection.execute("UPDATE folders SET favorite=? WHERE id=?", (int(favorite), folder_id))
        if cursor.rowcount != 1:
            raise KeyError(folder_id)
        return self.get_folder(folder_id)

    def set_folder_expanded(self, folder_id: str, expanded: bool) -> Folder:
        with self.connection:
            cursor = self.connection.execute("UPDATE folders SET expanded=? WHERE id=?", (int(expanded), folder_id))
        if cursor.rowcount != 1:
            raise KeyError(folder_id)
        return self.get_folder(folder_id)

    def reorder_folder(self, folder_id: str, before_id: str | None) -> None:
        moving = self.get_folder(folder_id)
        siblings = [
            folder for folder in self.list_folders()
            if folder.id != folder_id and folder.favorite == moving.favorite
        ]
        index = len(siblings)
        if before_id is not None:
            index = next((i for i, item in enumerate(siblings) if item.id == before_id), index)
        siblings.insert(index, moving)
        with self.connection:
            for order, folder in enumerate(siblings):
                self.connection.execute(
                    "UPDATE folders SET sort_order=? WHERE id=?", (order, folder.id)
                )

    def delete_folder(self, folder_id: str) -> None:
        self.get_folder(folder_id)
        with self.connection:
            cursor = self.connection.execute("DELETE FROM folders WHERE id=?", (folder_id,))
            self.connection.execute("DELETE FROM metadata WHERE key=? OR (key LIKE 'folder-parent:%' AND value=?)",
                                    ('folder-parent:' + folder_id, folder_id))
        if cursor.rowcount != 1:
            raise KeyError(folder_id)
        self._normalize_note_order(None)

    def _next_order(self, table: str, *, folder_id: str | None = None) -> int:
        if table == "notes":
            row = self.connection.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM notes WHERE folder_id IS ? AND deleted_at IS NULL",
                (folder_id,),
            ).fetchone()
        elif table == "folders":
            row = self.connection.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM folders").fetchone()
        else:
            raise ValueError(table)
        return int(row[0])

    def _normalize_note_order(self, folder_id: str | None) -> None:
        with self.connection:
            notes = self.list_notes(folder_id=folder_id or "__root__")
            for favorite in (True, False):
                for order, note in enumerate(item for item in notes if item.favorite is favorite):
                    self.connection.execute(
                        "UPDATE notes SET sort_order=? WHERE id=?", (order, note.id)
                    )

    def _import_legacy_markdown_once(self) -> None:
        if self.connection.execute("SELECT 1 FROM metadata WHERE key='legacy-markdown-import-v1'").fetchone():
            return
        for record in list_notes():
            note = self.create_note(title=record.title)
            self.update_note(note.id, title=record.title,
                             body=record.path.read_text(encoding="utf-8", errors="replace"))
        with self.connection:
            self.connection.execute("INSERT INTO metadata(key,value) VALUES('legacy-markdown-import-v1',?)", (_now(),))


def notes_data_directory(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    default = Path(env.get("HOME", str(Path.home()))) / ".local" / "share"
    return Path(env.get("XDG_DATA_HOME", default)) / "luma" / "notes"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clean_note_title(title: str) -> str:
    value = " ".join(title.replace("\x00", "").split()).strip()
    return value[:240]


def _validate_mark(mark: dict | None) -> None:
    if mark is not None:
        if mark.get('kind') not in ('dot', 'icon', 'emoji'):
            raise ValueError('unsupported mark kind')
        hue = mark.get('hue', 0)
        if (not isinstance(hue, (int, float)) or isinstance(hue, bool)) and hue != 'grey':
            raise ValueError('mark hue must be numeric or grey')
        if hue != 'grey' and not math.isfinite(hue):
            raise ValueError('mark hue must be finite')
        if 'folder_hue' in mark:
            retained = mark['folder_hue']
            if (not isinstance(retained, (int, float)) or isinstance(retained, bool)
                    or not math.isfinite(retained)):
                raise ValueError('retained folder hue must be finite and numeric')
        if mark.get('kind') in ('icon', 'emoji') and not isinstance(mark.get('value'), str):
            raise ValueError('icon and emoji marks require a value')


def _clean_required_name(name: str) -> str:
    value = " ".join(name.replace("\x00", "").split()).strip()
    if not value:
        raise ValueError("a folder name cannot be empty")
    return value[:240]


V70_RUN_STYLES = frozenset({'strike', 'highlight', 'checklist', 'checked', 'divider', 'file'})
RUN_STYLES = V70_RUN_STYLES | frozenset({
    "bold", "italic", "underline", "heading", "heading-1", "quote", "bulleted", "numbered", "link", "image",
    "indent-1", "indent-2", "indent-3", "indent-4", "align-center", "align-right",
})
PICTURE_NAME = re.compile(r"^[0-9a-f]{64}\.(png|jpg|gif|webp)$")


def _validate_runs(body: str, runs: tuple[dict[str, object], ...]) -> None:
    for run in runs:
        start, end, style = run.get("start"), run.get("end"), run.get("style")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(body):
            raise ValueError("formatting run is outside the note body")
        if style not in RUN_STYLES:
            raise ValueError(f"unsupported formatting style: {style}")
        if style == "link" and not isinstance(run.get("href"), str):
            raise ValueError("link formatting requires href")
        if style == "image":
            width = run.get("width", 0)
            if (end != start + 1 or body[start] != "\ufffc"
                    or not isinstance(run.get("src"), str) or not PICTURE_NAME.match(run["src"])
                    or not isinstance(width, int) or not 0 <= width <= 20000):
                raise ValueError("a picture must be one object character naming a stored picture")
        if style == 'file':
            from .notes_files import validate_file
            if end != start + 1 or body[start] != '\ufffc':
                raise ValueError('an attachment must occupy one object character')
            validate_file(run)


class NoteChangedError(RuntimeError):
    """The page changed here after it was read; nothing was written."""


def _content(note: Note | None) -> tuple | None:
    """What a person wrote on a page, and whether it is in Recently Deleted."""
    return None if note is None else (note.title, note.body, note.runs, note.deleted_at is not None)


def _note(row: sqlite3.Row) -> Note:
    try:
        runs = json.loads(row["runs_json"])
    except json.JSONDecodeError:
        runs = []
    return Note(row["id"], row["title"], row["body"], tuple(runs), row["created_at"],
                row["modified_at"], row["folder_id"], bool(row["favorite"]),
                int(row["sort_order"]), row["deleted_at"])


def _folder(row: sqlite3.Row) -> Folder:
    return Folder(row["id"], row["name"], bool(row["favorite"]), int(row["sort_order"]), bool(row["expanded"]))


# Markdown compatibility API retained for portable import/export and callers.
@dataclass(frozen=True)
class NoteRecord:
    path: Path
    title: str
    preview: str
    modified: datetime


def notes_directory(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    documents = env.get("XDG_DOCUMENTS_DIR", "").strip()
    root = Path(documents.replace("$HOME", env.get("HOME", str(Path.home())))) if documents else Path(env.get("HOME", str(Path.home()))) / "Documents"
    return root / "Notes"


def _record(path: Path) -> NoteRecord:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip().lstrip("# ") for line in text.splitlines() if line.strip()]
    return NoteRecord(path, lines[0] if lines else "Untitled note", lines[1] if len(lines) > 1 else "No additional text", datetime.fromtimestamp(path.stat().st_mtime))


def list_notes(root: Path | None = None, search: str = "") -> tuple[NoteRecord, ...]:
    directory = notes_directory() if root is None else root
    if not directory.is_dir():
        return ()
    records = tuple(_record(path) for path in directory.glob("*.md") if path.is_file() and not path.is_symlink())
    needle = search.strip().casefold()
    if needle:
        records = tuple(record for record in records if needle in record.path.read_text(encoding="utf-8", errors="replace").casefold())
    return tuple(sorted(records, key=lambda item: item.modified, reverse=True))


def create_note(root: Path | None = None) -> NoteRecord:
    directory = notes_directory() if root is None else root
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4()}.md"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("# Untitled note\n\n")
    return _record(path)


def save_note(path: Path, text: str, root: Path | None = None) -> NoteRecord:
    directory = (notes_directory() if root is None else root).resolve(); target = path.resolve()
    if target.parent != directory or target.suffix != ".md" or target.is_symlink():
        raise ValueError("note path is outside the Notes directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".note-", dir=directory); temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True); raise
    return _record(target)


def delete_note(path: Path, root: Path | None = None) -> Path:
    directory = (notes_directory() if root is None else root).resolve(); target = path.resolve()
    if target.parent != directory or target.suffix != ".md" or target.is_symlink():
        raise ValueError("note path is outside the Notes directory")
    trash = directory / ".Trash"; trash.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = trash / target.name
    if destination.exists():
        destination = trash / f"{target.stem}-{uuid.uuid4().hex}{target.suffix}"
    target.replace(destination)
    return destination
