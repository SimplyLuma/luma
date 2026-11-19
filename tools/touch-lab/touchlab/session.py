# SPDX-License-Identifier: Apache-2.0
"""Reading recorded sessions.

A session directory holds session.json and stream-NNNNNN.jsonl.gz chunks.
Every line is a JSON array [t_ns, tag, ...] with t_ns on CLOCK_MONOTONIC:

    H  header (also session.json)        [t, "H", {...}]
    h  hidraw report, hex                [t, "h", "03..."]
    e  touchpad evdev frame              [t, "e", kernel_us, [[type, code, value], ...]]
    l  libinput debug-events line        [t, "l", "text"]
    p  libinput with shadow plugins      [t, "p", "text"]
    k  key press on keyboard N           [t, "k", N]    (t rounded to 10 ms, nothing else)
    s  recorder status                   [t, "s", {"event": ...}]

A chunk cut short by a crash is read up to its last complete line.
"""

from __future__ import annotations

import gzip
import json
import os
import zlib
from typing import Iterator

from .labels import read_labels


def chunks(session_dir: str) -> list[str]:
    return sorted(os.path.join(session_dir, f) for f in os.listdir(session_dir)
                  if f.startswith("stream-") and f.endswith(".jsonl.gz"))


def _lines(path: str) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        raw = fh.read()
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pending = b""
    try:
        data = d.decompress(raw)
    except zlib.error:
        data = b""
        # Fall back to gzip for multi-member files.
        try:
            data = gzip.decompress(raw)
        except (OSError, EOFError, zlib.error):
            pass
    pending = data
    *complete, _tail = pending.split(b"\n")
    yield from complete


def records(session_dir: str) -> Iterator[list]:
    for path in chunks(session_dir):
        for line in _lines(path):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec and rec[1] == "H":
                continue
            yield rec


class Session:
    def __init__(self, path: str, root: str | None = None) -> None:
        self.path = path
        self.root = root or os.path.dirname(os.path.dirname(path))
        with open(os.path.join(path, "session.json"), encoding="utf-8") as fh:
            self.meta = json.load(fh)
        self._records: list[list] | None = None

    @property
    def id(self) -> str:
        return self.meta.get("session") or os.path.basename(self.path)

    @property
    def boot_id(self) -> str:
        return self.meta.get("boot_id", "")

    @property
    def descriptor(self) -> bytes:
        return bytes.fromhex(self.meta.get("devices", {}).get("touchpad", {}).get("report_descriptor", ""))

    @property
    def touchpad_sysname(self) -> str:
        return self.meta.get("devices", {}).get("touchpad", {}).get("sysname", "")

    def keyboards(self) -> list[dict]:
        return self.meta.get("devices", {}).get("keyboards", [])

    def records(self) -> list[list]:
        if self._records is None:
            self._records = sorted(records(self.path), key=lambda r: r[0])
        return self._records

    def span(self) -> tuple[int, int]:
        recs = self.records()
        return (recs[0][0], recs[-1][0]) if recs else (0, 0)

    def labels(self) -> list[dict]:
        start, end = self.span()
        return read_labels(self.root, self.boot_id, start, end)

    def by_tag(self, tag: str) -> list[list]:
        return [r for r in self.records() if r[1] == tag]


def find_sessions(root: str) -> list[str]:
    base = os.path.join(root, "sessions")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    return [os.path.join(base, n) for n in names
            if os.path.exists(os.path.join(base, n, "session.json"))]
