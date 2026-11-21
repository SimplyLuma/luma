# SPDX-License-Identifier: Apache-2.0
"""Destinations: where Go live sends, and the recording, as rows.

Pure Python. A destination is a platform (or a recording), the canvas it takes, whether it is on, and
for a stream its server; a stream key never sits here (libsecret, keyed by the destination's id).
Go live starts every enabled stream row; outputs that share a canvas and a quality share one encode.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .model import FORMATS, atomic_write

#: key: (label, ingest (empty: the platform shows its own; it is pasted with the key), how it connects,
#: icon, video kbps, audio kbps)
PLATFORMS: dict[str, tuple[str, str, str, str, int, int]] = {
    "twitch": ("Twitch", "rtmps://live.twitch.tv/app", "key", "twitch", 6000, 160),
    "youtube": ("YouTube", "rtmps://a.rtmps.youtube.com/live2", "key", "youtube", 8000, 160),
    "kick": ("Kick", "", "key", "kick", 6000, 160),
    "tiktok": ("TikTok", "", "key", "tiktok", 6000, 128),
    "custom": ("Custom", "", "key", "server", 6000, 160),
    "record": ("Recording", "", "file", "circle", 12000, 192),
}

#: Platforms that sign in (OAuth) instead of taking a pasted key; the key then comes from the platform.
SIGN_IN = ("twitch", "youtube")

#: Server schemes a custom destination may use.
SCHEMES = ("rtmp://", "rtmps://", "srt://", "http://", "https://")


@dataclass
class Destination:
    id: str
    platform: str
    name: str = ""
    canvas: str = "landscape"
    enabled: bool = True
    server: str = ""
    #: A recording's container ("mkv" | "mp4").
    container: str = "mkv"
    #: "" = the default: H.264 for a stream; for a recording, output_graph.RECORD_ORDER.
    codec: str = ""
    #: Per-row overrides (0 = the platform's default).
    video_kbps: int = 0
    audio_kbps: int = 0
    #: The signed-in account's display name (OAuth platforms), shown on the row.
    account: str = ""
    #: A recording's folder ("" = Videos/Streamy).
    folder: str = ""

    @property
    def label(self) -> str:
        return self.name or PLATFORMS[self.platform][0]

    @property
    def is_stream(self) -> bool:
        return self.platform != "record"

    @property
    def ingest(self) -> str:
        return (self.server or PLATFORMS[self.platform][1]).rstrip("/")

    def url(self, key: str) -> str:
        """The address a stream connects to: its server and key (RTMP), or the server alone (SRT/WHIP
        carry the key in it)."""
        server = self.ingest
        if not server.startswith(SCHEMES):
            raise ValueError("A server address starts with rtmp://, rtmps://, srt:// or https://")
        if server.startswith(("rtmp://", "rtmps://")) and key:
            return f"{server}/{key}"
        return server

    @property
    def signs_in(self) -> bool:
        return self.platform in SIGN_IN

    @property
    def icon(self) -> str:
        return PLATFORMS[self.platform][3]

    def preset(self) -> tuple[str, str, int, int]:
        """What an encode for this row is: (canvas, codec, video kbps, audio kbps). Rows with one preset
        share it. A recording's codec "" is resolved when its encoder is chosen."""
        _label, _ingest, _how, _icon, video, audio = PLATFORMS[self.platform]
        codec = self.codec or ("h264" if self.is_stream else "")
        return self.canvas, codec, self.video_kbps or video, self.audio_kbps or audio


def new(platform: str, canvas: str = "landscape") -> Destination:
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    return Destination(uuid.uuid4().hex[:8], platform, canvas=canvas if canvas in FORMATS else "landscape",
                       enabled=platform != "record")


def shared_encodes(rows: list[Destination], *, record: bool = False) -> dict[tuple, list[Destination]]:
    """The enabled stream rows (and the recording, with ``record``) grouped by preset: one encode
    each, teed to its rows."""
    groups: dict[tuple, list[Destination]] = {}
    for row in rows:
        if row.enabled and (row.is_stream or record):
            groups.setdefault(row.preset(), []).append(row)
    return groups


def canvases_in_use(rows: list[Destination]) -> set[str]:
    """The canvases enabled rows take (their chips carry a red dot while live)."""
    return {row.canvas for row in rows if row.enabled}


@dataclass
class DestinationStore:
    path: Path
    rows: list[Destination] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "DestinationStore":
        store = cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = None
        names = set(Destination.__dataclass_fields__)
        for raw in (data or {}).get("rows", []) if isinstance(data, dict) else []:
            if isinstance(raw, dict) and raw.get("platform") in PLATFORMS and raw.get("id"):
                store.rows.append(Destination(**{k: v for k, v in raw.items() if k in names}))
        if not any(r.platform == "record" for r in store.rows):
            store.rows.append(new("record"))
        return store

    def save(self) -> None:
        atomic_write(self.path, json.dumps({"rows": [asdict(r) for r in self.rows]}, indent=1))

    def get(self, row_id: str) -> Destination | None:
        return next((r for r in self.rows if r.id == row_id), None)

    def add(self, platform: str, canvas: str = "landscape") -> Destination:
        row = new(platform, canvas)
        # Streams above the recording, newest last among them.
        at = next((i for i, r in enumerate(self.rows) if not r.is_stream), len(self.rows))
        self.rows.insert(at, row)
        self.save()
        return row

    def update(self, row: Destination) -> None:
        for i, old in enumerate(self.rows):
            if old.id == row.id:
                self.rows[i] = row
        self.save()

    def move(self, row_id: str, to: int) -> None:
        row = self.get(row_id)
        if row is None or not row.is_stream:
            return
        self.rows.remove(row)
        streams = [r for r in self.rows if r.is_stream]
        to = max(0, min(to, len(streams)))
        at = self.rows.index(streams[to]) if to < len(streams) else \
            next((i for i, r in enumerate(self.rows) if not r.is_stream), len(self.rows))
        self.rows.insert(at, row)
        self.save()

    def remove(self, row_id: str) -> None:
        self.rows = [r for r in self.rows if r.id != row_id or not r.is_stream]
        self.save()
