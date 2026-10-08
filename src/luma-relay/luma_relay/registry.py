from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import data_home, windows_apps_root
from .errors import RelayError


APP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{7,80}$")


def validate_app_id(app_id: str) -> str:
    if not APP_ID.fullmatch(app_id):
        raise RelayError("The Relay application identifier is invalid.")
    return app_id


def capsule_root(app_id: str) -> Path:
    return windows_apps_root() / validate_app_id(app_id)


def manifest_path(app_id: str) -> Path:
    return capsule_root(app_id) / "manifest.json"


def read_manifest(app_id: str) -> dict[str, Any]:
    path = manifest_path(app_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RelayError(f"Relay could not read the application record: {error}.") from error
    if payload.get("schema") != 1 or payload.get("app_id") != app_id:
        raise RelayError("The Relay application record is not valid.")
    return payload


def write_manifest(app_id: str, payload: dict[str, Any]) -> Path:
    root = capsule_root(app_id)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = dict(payload)
    payload["schema"] = 1
    payload["app_id"] = app_id
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=root, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        destination = root / "manifest.json"
        os.replace(temporary, destination)
        return destination
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def list_manifests() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted(windows_apps_root().glob("*/manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema") == 1 and APP_ID.fullmatch(str(payload.get("app_id", ""))):
            results.append(payload)
    return results


def applications_directory() -> Path:
    root = data_home() / "applications"
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    return root
