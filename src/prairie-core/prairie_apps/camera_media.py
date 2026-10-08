# SPDX-License-Identifier: Apache-2.0
"""Camera-owned safety boundary before writing an existing Photos catalog."""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from .camera_backend import publish_capture


def ensure_catalog_backup(database: Path, directory: Path) -> Path | None:
    """Keep one consistent private snapshot before Camera's first catalog write.

    SQLite's backup API includes committed WAL data. A missing catalog is an
    additive first use; existing catalogs are opened read-only before backup.
    """
    database = database.resolve()
    if not database.exists():
        return None
    identity = hashlib.sha256(os.fsencode(database)).hexdigest()[:16]
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / f"photos-before-camera-{identity}-v1.sqlite3"
    descriptor = os.open(directory / ".catalog-backup.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            with destination.open("rb") as saved:
                if saved.read(16) != b"SQLite format 3\x00":
                    raise OSError("The existing Camera catalog backup is invalid")
            return destination
        descriptor, name = tempfile.mkstemp(prefix=".catalog-", suffix=".partial", dir=directory)
        temporary = Path(name)
        os.close(descriptor)
        deadline = time.monotonic() + 10

        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError("The Photos catalog backup timed out")

        try:
            source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
            try:
                target = sqlite3.connect(temporary)
                try:
                    source.backup(target, pages=256, progress=progress, sleep=.1)
                finally:
                    target.close()
            finally:
                source.close()
            with temporary.open("rb") as saved:
                os.fsync(saved.fileno())
            return publish_capture(temporary, destination)
        except sqlite3.Error as error:
            raise OSError("The Photos catalog backup could not be created") from error
        finally:
            temporary.unlink(missing_ok=True)
