# SPDX-License-Identifier: Apache-2.0
"""Inset layouts: the built-in presets, the person's saved ones, and the last used.

A layout is LumaUI's Dock arrangement: a tree of ``{"split": "h"|"v",
"ratio", "start", "end"}`` with the tile keys (preview, audio) at
its leaves (preview, audio). Pure Python; the window hands them to ``Dock.set_layout``.
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import atomic_write

TILES = ("preview", "audio")


def split(orientation: str, ratio: float, start, end) -> dict:
    return {"split": orientation, "ratio": ratio, "start": start, "end": end}


PRESETS: dict[str, tuple[str, object]] = {
    "default": ("Default", split("v", 0.76, "preview", "audio")),
    "side": ("Audio beside", split("h", 0.76, "preview", "audio")),
    "compact": ("Compact", split("v", 0.88, "preview", "audio")),
}


def valid(layout):
    """A dock layout holding every tile once: unknown or repeated keys dropped, missing tiles added."""
    seen: set[str] = set()

    def clean(node):
        if isinstance(node, str):
            if node in TILES and node not in seen:
                seen.add(node)
                return node
            return None
        if not isinstance(node, dict) or node.get("split") not in ("h", "v"):
            return None
        start, end = clean(node.get("start")), clean(node.get("end"))
        if start is None or end is None:
            return start if end is None else end
        ratio = node.get("ratio")
        ratio = min(0.95, max(0.05, float(ratio))) if isinstance(ratio, (int, float)) else 0.5
        return split(node["split"], ratio, start, end)

    tree = clean(layout)
    if tree is None:
        return PRESETS["default"][1]
    for key in TILES:
        if key not in seen:
            tree = split("h", 0.75, tree, key)
    return tree


class LayoutStore:
    """layouts.json: {"current": [...], "saved": {name: [...]}}."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.current = valid(None)
        self.saved: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self.current = valid(data.get("current"))
        saved = data.get("saved")
        if isinstance(saved, dict):
            self.saved = {str(name): valid(layout) for name, layout in saved.items() if str(name).strip()}

    def _write(self) -> None:
        atomic_write(self.path, json.dumps({"current": self.current, "saved": self.saved}, indent=1))

    def names(self) -> list[tuple[str, str]]:
        """(key, label) for the presets, then the saved layouts by name."""
        return [(key, label) for key, (label, _l) in PRESETS.items()] + \
            [(f"saved:{name}", name) for name in sorted(self.saved, key=str.casefold)]

    def get(self, key: str):
        import copy

        if key in PRESETS:
            return copy.deepcopy(PRESETS[key][1])
        if key.startswith("saved:") and key[6:] in self.saved:
            return copy.deepcopy(self.saved[key[6:]])
        raise KeyError(key)

    def remember(self, layout) -> None:
        self.current = valid(layout)
        self._write()

    def save_as(self, name: str, layout) -> str:
        name = name.strip()
        if not name:
            raise ValueError("a layout has a name")
        if name.casefold() in {label.casefold() for label, _l in PRESETS.values()}:
            raise ValueError(f"{name} is a built-in layout")
        self.saved[name] = valid(layout)
        self._write()
        return f"saved:{name}"

    def forget(self, name: str) -> None:
        if self.saved.pop(name, None) is not None:
            self._write()
