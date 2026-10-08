# SPDX-License-Identifier: Apache-2.0
"""The activity log (§7.2 rule 11): local, append-only, never uploaded."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import paths

MAX_BYTES = 8 * 1024 * 1024
_lock = threading.Lock()


def record(entry: dict, path: Path | None = None) -> None:
    path = path or paths.audit_path()
    line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}, ensure_ascii=False)
    with _lock:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_suffix(".1.jsonl"))
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (line + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def recent(limit: int = 200, path: Path | None = None) -> list[dict]:
    path = path or paths.audit_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
