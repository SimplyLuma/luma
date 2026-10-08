# SPDX-License-Identifier: Apache-2.0
"""Atomic permission and privacy-preserving audit persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterable

from .model import AuditEvent, Grant
from .validation import APP_ID


STORE_SCHEMA = "org.projectluma.semantic-grants/v0.1"
MAX_AUDIT_BYTES = 1024 * 1024


class StoreError(RuntimeError):
    pass


def default_state_directory() -> Path:
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    return (Path(state) if state else Path.home() / ".local/state") / "luma/semantic-broker"


def _secure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise StoreError("semantic broker state directory is not user-owned")
    path.chmod(0o700)


class GrantStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_state_directory() / "grants.json"

    def load(self) -> tuple[Grant, ...]:
        if not self.path.exists():
            return ()
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise StoreError("semantic grant store is not a user-owned regular file")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise StoreError("semantic grant store permissions are too broad")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError("semantic grant store is unreadable") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "grants"}
            or value["schema"] != STORE_SCHEMA
            or not isinstance(value["grants"], list)
        ):
            raise StoreError("semantic grant store has an invalid contract")
        try:
            grants = tuple(Grant.from_dict(item) for item in value["grants"])
            for grant in grants:
                prefix, separator, identity_app = grant.client_key.partition(":")
                if (
                    separator != ":"
                    or prefix != "flatpak"
                    or not APP_ID.fullmatch(identity_app)
                    or not APP_ID.fullmatch(grant.application_id)
                ):
                    raise ValueError("invalid persisted semantic application identity")
            return grants
        except (TypeError, ValueError) as error:
            raise StoreError("semantic grant store contains an invalid grant") from error

    def save(self, grants: Iterable[Grant]) -> None:
        _secure_directory(self.path.parent)
        payload = {
            "schema": STORE_SCHEMA,
            "grants": [grant.to_dict() for grant in grants],
        }
        descriptor, name = tempfile.mkstemp(prefix=".grants-", dir=self.path.parent)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


class AuditStore:
    def __init__(self, path: Path | None = None, max_bytes: int = MAX_AUDIT_BYTES) -> None:
        self.path = path or default_state_directory() / "audit.jsonl"
        self.max_bytes = max_bytes

    def append(self, event: AuditEvent) -> None:
        _secure_directory(self.path.parent)
        if self.path.exists():
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise StoreError("semantic audit is not a user-owned regular file")
            if info.st_size >= self.max_bytes:
                rotated = self.path.with_suffix(".jsonl.1")
                rotated.unlink(missing_ok=True)
                self.path.replace(rotated)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            line = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def tail(self, limit: int = 100) -> tuple[dict, ...]:
        if limit < 0 or limit > 500:
            raise StoreError("invalid semantic audit limit")
        if not self.path.exists() or limit == 0:
            return ()
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise StoreError("semantic audit is not a user-owned regular file")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise StoreError("semantic audit permissions are too broad")
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        try:
            return tuple(json.loads(line) for line in lines)
        except json.JSONDecodeError as error:
            raise StoreError("semantic audit contains an invalid record") from error
