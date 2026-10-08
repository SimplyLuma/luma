# SPDX-License-Identifier: Apache-2.0
"""Capture state shared by the desktop and handheld presentations."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionShot:
    path: Path
    width: int
    height: int
    record: object | None = None
    duration: float | None = None


@dataclass
class CameraState:
    mode: str = "photo"
    manual: dict[str, float] = field(default_factory=dict)
    source: int = 0
    zoom: float = 1.0
    flash: bool = False
    timer: int = 0
    grid: bool = True
    level: bool = False
    keep_raw: bool = False
    mirror: bool = True
    audio: bool = True
    recording: bool = False
    paused: bool = False
    elapsed: float = 0.0
    depth: float = 2.0
    reticle: tuple[float, float] | None = None
    roll: list[SessionShot] = field(default_factory=list)
    screen: str = "capture"

    def select_source(self, index: int) -> None:
        self.source = index
        self.zoom = 1.0
        self.manual.clear()
        self.reticle = None


ASPECT_RATIOS = {"4:3": 4 / 3, "3:2": 3 / 2, "16:9": 16 / 9, "1:1": 1.0}


def aspect_ratio(aspect: str | float) -> float:
    """Width over height for a shape name ("4:3") or a ratio already worked out (a portrait 0.75)."""
    return ASPECT_RATIOS[aspect] if isinstance(aspect, str) else float(aspect)


def portrait(aspect: str) -> float:
    """The same shape held upright, as a phone camera shows it (v71: 4:3 is a 3:4 frame)."""
    return 1 / ASPECT_RATIOS[aspect]


def aspect_frame(width: float, height: float, aspect: str | float | None) -> tuple[float, float, float, float]:
    """The active photo rectangle within a viewfinder or output canvas."""
    if not aspect or width <= 0 or height <= 0:
        return 0.0, 0.0, width, height
    ratio = aspect_ratio(aspect)
    if width / height > ratio:
        frame_width, frame_height = height * ratio, height
    else:
        frame_width, frame_height = width, width / ratio
    return (width - frame_width) / 2, (height - frame_height) / 2, frame_width, frame_height


def output_dimensions(width: int, height: int, aspect: str | float) -> tuple[int, int]:
    _, _, frame_width, frame_height = aspect_frame(width, height, aspect)
    return max(2, int(frame_width) // 2 * 2), max(2, int(frame_height) // 2 * 2)


def crop_edges(width: int, height: int, zoom: float, aspect: str | float | None = None) -> tuple[int, int]:
    """Symmetric even crops keep chroma planes aligned for common YUV formats."""
    zoom = min(4.0, max(1.0, zoom))
    horizontal = int(width * (1 - 1 / zoom) / 4) * 2
    vertical = int(height * (1 - 1 / zoom) / 4) * 2
    if aspect:
        cropped_width, cropped_height = width - 2 * horizontal, height - 2 * vertical
        offset_x, offset_y, _, _ = aspect_frame(cropped_width, cropped_height, aspect)
        horizontal += int(offset_x / 2) * 2
        vertical += int(offset_y / 2) * 2
    return horizontal, vertical
