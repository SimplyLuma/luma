# SPDX-License-Identifier: Apache-2.0
"""Where Depot's own pictures live: the source tree, the Flatpak (/app/share/luma/depot) or the system."""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["data_dir", "data_file", "wallpaper"]

_SOURCE = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    for candidate in (os.environ.get("LUMA_DEPOT_DATA", ""), str(_SOURCE), "/app/share/luma/depot",
                      "/usr/share/luma-depot"):
        if candidate and Path(candidate, "rooms").is_dir():
            return Path(candidate)
    return _SOURCE


def data_file(*parts: str) -> str | None:
    path = data_dir().joinpath(*parts)
    return str(path) if path.is_file() else None


def wallpaper(name: str, art: Path | None = None) -> str | None:
    """A Luma wallpaper by name (prism, meadow, ember…): the world's own copy first, then the system's."""
    candidates = []
    if art is not None:
        candidates += [art / f"{name}.jpg", art / f"{name}.png"]
    candidates += [data_dir() / "wallpapers" / f"{name}.jpg",
                   Path("/usr/share/backgrounds/luma") / f"{name}.png",
                   Path("/usr/share/backgrounds/luma") / f"{name}.jpg"]
    return next((str(path) for path in candidates if path.is_file()), None)
