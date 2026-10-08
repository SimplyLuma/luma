# SPDX-License-Identifier: Apache-2.0
"""Contained, local-only Tasks flags/assignments and one-time EDS backups.

Never initialized in fixture mode. Reads do not create files. A private lock
serializes read-modify-write across app instances; replacements are atomic.
Unknown content is preserved, and invalid files fail closed instead of reset.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone


def data_root():
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "luma/tasks"


def atomic_json(path, value, *, once=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".tasks-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        if once:
            try: os.link(temporary, path)
            except FileExistsError: pass
        else: os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def backup_source(source_uid, components):
    """Keep the original collection once, before its first app write."""
    digest = hashlib.sha256(source_uid.encode()).hexdigest()
    path = data_root() / "backups" / (digest + ".json")
    if not path.exists():
        atomic_json(path, {"source": source_uid, "components": list(components)}, once=True)


class LocalTasks:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else data_root() / "local.json"

    def read(self):
        if not self.path.exists(): return {"version": 1, "tasks": {}}
        with self.path.open(encoding="utf-8") as stream: value = json.load(stream)
        if value.get("version") != 1 or not isinstance(value.get("tasks"), dict):
            raise ValueError("The local Tasks file is invalid. Nothing was changed.")
        for fields in value["tasks"].values():
            if not isinstance(fields, dict) or ("flag" in fields and not isinstance(fields["flag"], bool)) or (
                    "who" in fields and (not isinstance(fields["who"], list) or not all(isinstance(k, str) for k in fields["who"]))):
                raise ValueError("The local Tasks file is invalid. Nothing was changed.")
            comments = fields.get("comments", [])
            if not isinstance(comments, list) or any(not isinstance(c, dict) or
                    any(not isinstance(c.get(k), str) for k in ("id", "author", "text", "created"))
                    for c in comments):
                raise ValueError("The local Tasks comments are invalid. Nothing was changed.")
        return value

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def edit(self, task_id, changes):
        if set(changes) - {"flag", "who"}: raise ValueError("Unsupported local task field")
        if "flag" in changes and not isinstance(changes["flag"], bool): raise ValueError("Flag must be a boolean")
        if "who" in changes and (not isinstance(changes["who"], list) or not all(isinstance(k, str) for k in changes["who"])):
            raise ValueError("Assignees must be a list of identities")
        with self.locked():
            value = self.read()
            backup = self.path.parent / "backups" / "local-original.json"
            if not backup.exists():
                atomic_json(backup, value, once=True)
            before = deepcopy(value["tasks"].get(task_id, {}))
            after = {**before, **deepcopy(changes)}
            value["tasks"][task_id] = after
            atomic_json(self.path, value)
        def undo():
            with self.locked():
                value = self.read(); current = value["tasks"].get(task_id, {})
                if any(current.get(k) != after[k] for k in changes):
                    raise RuntimeError("This local task changed. Undo would overwrite newer work.")
                for key in changes:
                    if key in before: current[key] = before[key]
                    else: current.pop(key, None)
                value["tasks"][task_id] = current
                atomic_json(self.path, value)
        return undo

    def comment(self, task_id, text, author):
        """Append a provider-unsupported comment atomically; undo only this comment."""
        if not text.strip(): raise ValueError("Write a comment first.")
        item = {"id": str(uuid.uuid4()), "text": text.strip(), "author": author,
                "created": datetime.now(timezone.utc).isoformat()}
        with self.locked():
            value = self.read()
            atomic_json(self.path.parent / "backups" / "local-original.json", value, once=True)
            value["tasks"].setdefault(task_id, {}).setdefault("comments", []).append(item)
            atomic_json(self.path, value)
        def undo():
            with self.locked():
                value = self.read()
                comments = value["tasks"].get(task_id, {}).get("comments", [])
                current = next((c for c in comments if c["id"] == item["id"]), None)
                if current is not None:
                    if current != item: raise RuntimeError("This comment changed. Undo would overwrite newer work.")
                    comments.remove(current)
                    atomic_json(self.path, value)
        return undo
