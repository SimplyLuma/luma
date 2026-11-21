# SPDX-License-Identifier: Apache-2.0
"""Inset layouts: the built-in presets, the person's saved ones, and the last used.

A layout is LumaUI's Dock arrangement: a tree of ``{"split": "h"|"v",
"ratio", "start", "end"}`` with the tile keys (landscape, vertical,
widgets, audio) at its leaves. Pure Python; the window hands them to ``Dock.set_layout``.
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import atomic_write

TILES = ("landscape", "vertical", "widgets", "audio", "destinations", "stats", "chat", "inspector")
#: Tiles a person may close (they then sit in the layout's "closed" record, LumaUI Dock).
CLOSABLE = ("landscape", "vertical", "destinations", "stats", "chat", "inspector")
#: The previews: one of them always stays open.
PREVIEWS = ("landscape", "vertical")
#: Panels closed until opened from the app menu (a layout from before them has them closed).
CLOSED_AT_FIRST = ("stats", "chat")


def split(orientation: str, ratio: float, start, end) -> dict:
    return {"split": orientation, "ratio": ratio, "start": start, "end": end}


def _insets() -> dict:
    return {"tabs": ["widgets", "destinations"], "current": "widgets"}


#: The Inspector: a full-height column at the right (as wide as it asks, LumaUI Dock fit).
INSPECTOR_SHARE = 0.76

PRESETS: dict[str, tuple[str, object]] = {
    # Both canvases side by side, Vertical at about 9:16 of the preview's height.
    "default": ("Default", split("h", INSPECTOR_SHARE, split("v", 0.72, split("h", 0.7, "landscape", "vertical"),
                                                             split("h", 0.5, _insets(), "audio")), "inspector")),
    "side": ("Insets beside", split("h", INSPECTOR_SHARE, split("h", 0.72, split("h", 0.7, "landscape", "vertical"),
                                                                split("v", 0.5, _insets(), "audio")), "inspector")),
    "compact": ("Compact", split("h", INSPECTOR_SHARE, split("v", 0.88, split("h", 0.7, "landscape", "vertical"),
                                                             split("h", 0.5, _insets(), "audio")), "inspector")),
}


def valid(layout):
    """A dock layout holding every open tile once: unknown or repeated keys dropped, missing tiles added.
    A layout with closed tiles is ``{"layout": tree, "closed": {key: place}}`` (LumaUI Dock); kept so."""
    closed = {}
    if isinstance(layout, dict) and "layout" in layout and "split" not in layout and "tabs" not in layout:
        closed = layout.get("closed")
        closed = {k: v for k, v in closed.items() if k in CLOSABLE} if isinstance(closed, dict) else {}
        layout = layout.get("layout")
    for key in [k for k in closed if _names(layout, k)]:
        closed.pop(key)  # an open tile is never also closed
    for key in CLOSED_AT_FIRST:
        if not _names(layout, key):
            closed.setdefault(key, {})
    if all(k in closed for k in PREVIEWS):
        closed.pop(PREVIEWS[0], None)  # a preview stays open
    tree = _valid_tree(layout, set(closed))
    return {"layout": tree, "closed": closed} if closed else tree


def _names(node, key: str) -> bool:
    if isinstance(node, str):
        return node == key
    if isinstance(node, dict) and "tabs" in node:
        return key in node["tabs"]
    return isinstance(node, dict) and (_names(node.get("start"), key) or _names(node.get("end"), key))


def _valid_tree(layout, closed: set[str]):
    seen: set[str] = set()

    def clean(node):
        if isinstance(node, str):
            node = "landscape" if node == "preview" else node  # the one preview before two canvases
            if node in TILES and node not in seen and node not in closed:
                seen.add(node)
                return node
            return None
        if isinstance(node, dict) and isinstance(node.get("tabs"), list):
            # A tab group (LumaUI Dock): one place showing one of its tiles at a time.
            keys = [k for k in (clean(k) for k in node["tabs"] if isinstance(k, str)) if k is not None]
            if len(keys) < 2:
                return keys[0] if keys else None
            current = node.get("current") if node.get("current") in keys else keys[0]
            return {"tabs": keys, "current": current}
        if not isinstance(node, dict) or node.get("split") not in ("h", "v"):
            return None
        start, end = clean(node.get("start")), clean(node.get("end"))
        if start is None or end is None:
            return start if end is None else end
        ratio = node.get("ratio")
        ratio = min(0.95, max(0.05, float(ratio))) if isinstance(ratio, (int, float)) else 0.5
        tidy = split(node["split"], ratio, start, end)
        if isinstance(node.get("size"), (int, float)) and node["size"] > 0:
            tidy["size"] = int(node["size"])  # a seam a person moved (LumaUI Dock)
        return tidy

    tree = clean(layout)
    if tree is None:
        return PRESETS["default"][1]
    if "vertical" not in seen and "vertical" not in closed and "landscape" in seen:
        # A layout from before two canvases: Vertical opens to the right of Landscape.
        def beside(node):
            if node == "landscape":
                return split("h", 0.7, "landscape", "vertical")
            if isinstance(node, dict) and "split" in node:
                return {**node, "start": beside(node["start"]), "end": beside(node["end"])}
            return node
        tree = beside(tree)
        seen.add("vertical")
    if seen >= {"landscape", "audio"} and isinstance(tree, dict) and "widgets" not in seen:
        # A layout from before the Widgets inset: Widgets joins Audio, side by side.
        def join(node):
            if node == "audio":
                return split("h", 0.5, "widgets", "audio")
            if isinstance(node, dict):
                return {**node, "start": join(node["start"]), "end": join(node["end"])}
            return node
        tree = join(tree)
        seen.add("widgets")
    if "destinations" not in seen and "destinations" not in closed and "widgets" in seen:
        # A layout from before Destinations: it joins Widgets as a tab.
        def join(node):
            if node == "widgets":  # a tab beside Widgets: a third column would not fit a laptop
                return {"tabs": ["widgets", "destinations"], "current": "widgets"}
            if isinstance(node, dict) and "split" in node:
                return {**node, "start": join(node["start"]), "end": join(node["end"])}
            return node
        tree = join(tree)
        seen.add("destinations")
    for key in TILES:
        if key not in seen and key not in closed:
            # A layout from before this tile (the Inspector was a column at the right): at the right edge.
            tree = split("h", INSPECTOR_SHARE if key == "inspector" else 0.75, tree, key)
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
