# SPDX-License-Identifier: Apache-2.0
"""Small Darkroom-only metadata store for library decisions.

The source photograph and the Photos library are never written here.  Each
update reloads the current JSON under an advisory lock and changes only fields
the user edited.  A permanent baseline copy is made before the first write.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


FIELDS = frozenset({"stars", "flag", "label", "keywords", "shoot", "albums"})
EMPTY = {"version": 1, "photos": {}}


def default_path(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    data = Path(env.get("XDG_DATA_HOME") or Path(env.get("HOME", str(Path.home()))) / ".local/share")
    return data / "luma-darkroom" / "library-metadata.json"


def _validate(changes: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(changes) - FIELDS
    if unknown:
        raise ValueError(f"unsupported library metadata fields: {sorted(unknown)}")
    result = dict(changes)
    if "stars" in result and (type(result["stars"]) is not int or not 0 <= result["stars"] <= 5):
        raise ValueError("stars must be an integer from 0 to 5")
    if "flag" in result and (type(result["flag"]) is not int or result["flag"] not in (-1, 0, 1)):
        raise ValueError("flag must be reject, unmarked or pick")
    if "label" in result and result["label"] not in (None, "red", "amber", "green", "blue"):
        raise ValueError("unknown colour label")
    for name in ("keywords", "albums"):
        if name in result:
            value = result[name]
            if not isinstance(value, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in value):
                raise ValueError(f"{name} must be a list of names")
            result[name] = list(dict.fromkeys(x.strip() for x in value))
    if "shoot" in result and not isinstance(result["shoot"], str):
        raise ValueError("shoot must be a name")
    return result


class MetadataStore:
    """Atomic per-photo JSON changes, with an immutable pre-write backup."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.backup = self.path.with_name(self.path.stem + ".before-first-write.json")
        self.lock = self.path.with_suffix(self.path.suffix + ".lock")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "photos": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("photos"), dict):
            raise ValueError("unsupported Darkroom metadata format; no migration was attempted")
        return payload

    def all(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._read()["photos"].items()}

    def get(self, photo_id: str) -> dict[str, Any]:
        return dict(self._read()["photos"].get(photo_id, {}))

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".darkroom-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def update(self, photo_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        if not photo_id:
            raise ValueError("photo identity is required")
        clean = _validate(changes)
        if not clean:
            return self.get(photo_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            payload = self._read()
            if not self.backup.exists():
                # Even a new store gets a concrete empty baseline.  Never
                # replace it: the first real write remains recoverable.
                with self.backup.open("xb") as output:
                    output.write((json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
                    output.flush()
                    os.fsync(output.fileno())
            photo = dict(payload["photos"].get(photo_id, {}))
            photo.update(clean)
            payload["photos"][photo_id] = photo
            self._atomic_write(self.path, payload)
            return dict(photo)


class MemoryMetadataStore:
    """The fixture's edits stay in memory and cannot reach the real store."""

    def __init__(self) -> None:
        self._photos: dict[str, dict[str, Any]] = {}

    def all(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._photos.items()}

    def get(self, photo_id: str) -> dict[str, Any]:
        return dict(self._photos.get(photo_id, {}))

    def update(self, photo_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        if not photo_id:
            raise ValueError("photo identity is required")
        photo = dict(self._photos.get(photo_id, {}))
        photo.update(_validate(changes))
        self._photos[photo_id] = photo
        return dict(photo)
