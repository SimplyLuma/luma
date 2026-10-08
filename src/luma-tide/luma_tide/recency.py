# SPDX-License-Identifier: Apache-2.0
"""Contained album playback history; no library database or source changes."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
from pathlib import Path


class AlbumRecency:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._times = {}
        self._ordered_input = None
        self._ordered = ()

    def _read(self):
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"albums": {}}
        if not isinstance(data, dict) or not isinstance(data.get("albums", {}), dict):
            raise ValueError("Invalid Tide album history")
        data.setdefault("albums", {})
        return data

    def load(self):
        with self._lock:
            data = self._read()
            times = {k: v for k, v in data["albums"].items()
                     if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
            if times != self._times:
                self._ordered_input = None
            self._times = times

    def order(self, albums):
        with self._lock:
            if albums is not self._ordered_input:
                self._ordered = tuple(sorted(albums, key=lambda a: -self._times.get(a.id, 0)))
                # Accept both the original tuple and our result without sorting
                # on subsequent position updates from the player.
                self._ordered_input = self._ordered if self._ordered != albums else albums
            return self._ordered

    def record(self, identifier, timestamp=None):
        """Worker-only RMW, with a permanent first-edit backup and atomic replace."""
        stamp = time.time_ns() if timestamp is None else timestamp
        if not isinstance(stamp, int) or isinstance(stamp, bool) or stamp < 0:
            raise ValueError("Invalid playback timestamp")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.path.with_suffix(".lock").open("a+b") as lock:
                os.fchmod(lock.fileno(), 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                data = self._read()  # A malformed file is never overwritten.
                if self.path.exists():
                    backup = self.path.with_suffix(self.path.suffix + ".before-first-write")
                    try:
                        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        try:
                            with os.fdopen(fd, "wb") as output:
                                output.write(self.path.read_bytes())
                                output.flush()
                                os.fsync(output.fileno())
                        except BaseException:
                            backup.unlink()
                            raise
                data["albums"][identifier] = stamp
                fd, temporary = tempfile.mkstemp(prefix=".album-history-", dir=self.path.parent)
                try:
                    with os.fdopen(fd, "w") as output:
                        json.dump(data, output, ensure_ascii=False)
                        output.write("\n")
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, self.path)
                    directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                self._times = {k: v for k, v in data["albums"].items()
                               if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
                self._ordered_input = None
