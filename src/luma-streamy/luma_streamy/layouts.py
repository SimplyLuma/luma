# SPDX-License-Identifier: Apache-2.0
"""Inset layouts: the built-in presets, the person's saved ones, and the last used.

A layout is LumaUI's docked-panel arrangement: a list of
``(key, "left" | "right" | "bottom", folded)``. Pure Python; the window
applies them with ``luma_appkit.creative_workspace.apply_layout``.
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import atomic_write

PANELS = ("widgets", "inspector", "audio")
SIDES = ("left", "right", "bottom")

Layout = list[tuple[str, str, bool]]

PRESETS: dict[str, tuple[str, Layout]] = {
    # The inspector opens from the bar's Effects, so the canvas keeps its room until it's needed.
    "default": ("Default", [("widgets", "left", False), ("audio", "left", False), ("inspector", "right", True)]),
    "compact": ("Compact", [("widgets", "left", True), ("audio", "left", True), ("inspector", "right", True)]),
    "studio": ("Studio", [("widgets", "left", False), ("inspector", "right", False), ("audio", "bottom", False)]),
}


def valid(layout) -> Layout:
    """A layout with every panel exactly once; unknown entries dropped, missing panels added."""
    seen: set[str] = set()
    out: Layout = []
    for entry in layout or ():
        try:
            key, side, folded = entry
        except (TypeError, ValueError):
            continue
        if key in PANELS and key not in seen and side in SIDES:
            out.append((str(key), str(side), bool(folded)))
            seen.add(key)
    for key, side, folded in PRESETS["default"][1]:
        if key not in seen:
            out.append((key, side, folded))
    return out


class LayoutStore:
    """layouts.json: {"current": [...], "saved": {name: [...]}}."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.current: Layout = list(PRESETS["default"][1])
        self.saved: dict[str, Layout] = {}
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

    def get(self, key: str) -> Layout:
        if key in PRESETS:
            return list(PRESETS[key][1])
        if key.startswith("saved:") and key[6:] in self.saved:
            return list(self.saved[key[6:]])
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
