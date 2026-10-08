# SPDX-License-Identifier: Apache-2.0
"""Small, scoped conversation and message preferences for Messages.

The message database remains untouched. Each update reloads the JSON, changes
one Boolean, and atomically replaces the file. A snapshot is made before the
first update, including the empty initial state for a new store.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile


class ConversationPreferences:
    def __init__(self, path: Path | None = None) -> None:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        self.path = Path(path) if path is not None else data_home / "prairie/messages/conversation-settings.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("invalid conversation settings")
        return document

    def flags(self, key: str) -> dict[str, bool]:
        row = self._read().get(key, {})
        if not isinstance(row, dict):
            raise ValueError("invalid conversation settings row")
        return {name: bool(row.get(name, False)) for name in ("pinned", "muted")}

    def all_flags(self) -> dict[str, dict[str, bool]]:
        return {key: {name: bool(row.get(name, False)) for name in ("pinned", "muted")}
                for key, row in self._read().items() if isinstance(row, dict)}

    def pinned_messages(self, key: str) -> set[str]:
        row = self._read().get(key, {})
        if not isinstance(row, dict):
            raise ValueError("invalid conversation settings row")
        messages = row.get("messages", {})
        if not isinstance(messages, dict):
            raise ValueError("invalid message settings")
        return {uid for uid, pinned in messages.items() if pinned is True}

    @staticmethod
    def _write(path: Path, document: dict) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".conversation-settings-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def set_flag(self, key: str, name: str, value: bool) -> None:
        if name not in {"pinned", "muted"}:
            raise ValueError(name)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            document = self._read()
            backup = self.path.with_name(self.path.name + ".before-pin-mute")
            if not backup.exists():
                self._write(backup, document)
            row = document.get(key, {})
            if not isinstance(row, dict):
                raise ValueError("invalid conversation settings row")
            row[name] = bool(value)
            document[key] = row
            self._write(self.path, document)

    def set_message_pinned(self, key: str, uid: str, value: bool) -> None:
        if not key or not uid:
            raise ValueError("message pin needs a conversation and message")
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            document = self._read()
            row = document.get(key, {})
            if not isinstance(row, dict):
                raise ValueError("invalid conversation settings row")
            messages = row.get("messages", {})
            if not isinstance(messages, dict):
                raise ValueError("invalid message settings")
            backup = self.path.with_name(self.path.name + ".before-message-pin")
            if not backup.exists():
                self._write(backup, document)
            if value:
                messages[uid] = True
            else:
                messages.pop(uid, None)
            if messages:
                row["messages"] = messages
            else:
                row.pop("messages", None)
            document[key] = row
            self._write(self.path, document)
