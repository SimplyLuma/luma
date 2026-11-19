# SPDX-License-Identifier: Apache-2.0
"""Label vocabulary and the user-owned label log.

Labels live apart from recordings (labels/<date>.jsonl) and carry the same
CLOCK_MONOTONIC time and boot ID, so they join to any session offline.
"""

from __future__ import annotations

import json
import os
import time

LABEL_VERSION = 1

# Contact classes. "intended" contacts should move the pointer, scroll,
# click or gesture; "accidental" ones should be ignored.
INTENDED = {
    "pointer": "One finger moving the pointer",
    "precision": "One finger placing the pointer on a small target",
    "scroll": "A finger of a two-finger scroll",
    "pinch": "A finger of a pinch",
    "tap": "A one-finger tap",
    "tap-2": "A finger of a two-finger tap",
    "click": "A finger pressing the pad to click",
    "click-thumb": "A thumb pressing the pad to click",
    "drag-thumb": "A thumb holding a click while another finger drags",
    "drag-finger": "The finger moving during a click-drag",
    "swipe-3": "A finger of a three-finger gesture",
    "swipe-4": "A finger of a four-finger gesture",
}
ACCIDENTAL = {
    "thumb-rest": "A thumb resting on the pad while other fingers work",
    "palm-typing": "A palm resting on the pad while typing",
    "thumb-drop": "A thumb landing on the pad during a drag",
    "graze": "A light brush of the pad while typing",
    "palm-edge": "The edge of a palm touching while reaching for keys",
    "hand-rest": "A hand put down on the pad between typing bursts",
}
CLASSES = {**{k: "intended" for k in INTENDED}, **{k: "accidental" for k in ACCIDENTAL}}
MARKS = {"wrong": "That was wrong", "almost": "That was right, but almost wrong"}
MARK_WINDOW_NS = 3_000_000_000


def data_root() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "luma-touch-lab")


def boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def now_ns() -> int:
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


class LabelLog:
    def __init__(self, root: str | None = None) -> None:
        self.dir = os.path.join(root or data_root(), "labels")
        self.boot = boot_id()

    def append(self, kind: str, t: int | None = None, **fields) -> dict:
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        record = {"v": LABEL_VERSION, "t": now_ns() if t is None else t, "boot_id": self.boot,
                  "kind": kind, **fields}
        path = os.path.join(self.dir, time.strftime("%Y%m%d") + ".jsonl")
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())             # one write: safe with concurrent writers
        finally:
            os.close(fd)
        return record

    def mark(self, which: str, t: int | None = None, source: str = "shortcut") -> dict:
        if which not in MARKS:
            raise ValueError(which)
        return self.append("mark", t, mark=which, window_ns=MARK_WINDOW_NS, source=source)


def read_labels(root: str, boot: str | None = None, start: int | None = None,
                end: int | None = None) -> list[dict]:
    out = []
    path = os.path.join(root, "labels")
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(path, name), encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if boot and rec.get("boot_id") != boot:
                    continue
                t = rec.get("t", 0)
                if start is not None and t < start - MARK_WINDOW_NS:
                    continue
                if end is not None and t > end + MARK_WINDOW_NS:
                    continue
                out.append(rec)
    out.sort(key=lambda r: r.get("t", 0))
    return out
