# SPDX-License-Identifier: Apache-2.0
"""LumaUI dock: an island tiled with insets, Premiere/Reel style.

    Dock([DockTile("preview", "Preview", "monitor", canvas, main=True),
          DockTile("audio", "Audio", "volume-2", mixer),
          DockTile("inspector", "Inspector", "sliders-horizontal", inspector)],
         layout=Dock.split("h", 0.74, Dock.split("v", 0.7, "preview", "audio"), "inspector"),
         on_layout=save)

Rules the dock owns, so every app gets the same answers:

- **Tiles fill the dock.** Every tile is an inset (the recessed well) with a
  header naming it; together they cover the whole area, with one gap
  between tiles and the same gap as the outer padding. Nothing floats.
- **Seams resize.** Where two tiles meet, the gap is a seam: drag it (or
  focus it with F8 and use the arrow keys) and the two sides trade space.
  Each side keeps at least what its tile needs.
- **Headers dock tiles anywhere.** Pick a tile up by its header (or its
  tab; a long press on a touch screen) and drag it: over another tile, the
  edge bands (a quarter of it each way) split that tile and put yours on that
  side, the middle stacks the two as tabs; within ``OUTER_BAND`` (24 px) of
  the dock's own edge it becomes a full-height column or full-width row
  (``EDGE_SHARE`` of the dock). A preview shows exactly where and how big it
  will land, gliding between places; nothing moves until release, and Esc
  cancels. The emptied place goes; the other tiles keep their shares. The
  header's More menu moves from the keyboard (Move left, right, up or down
  swaps with the nearest neighbour that way). No docking on a phone.
- **Tabs.** Tiles stacked in one place show a strip of their tabs (the
  current tile's actions and More at its end) over the current tile;
  ``show_tile(key)`` brings one forward.
- **Layouts are data.** ``layout()`` is a tree of ``{"split": "h"|"v",
  "ratio": start share, "start": …, "end": …}`` and tab groups
  ``{"tabs": [keys], "current": key}``, with tile keys at the leaves, plain
  JSON for settings (one app-wide layout, not one per document);
  ``set_layout()`` restores one (unknown keys are skipped, missing tiles
  join the end). ``on_layout`` follows a person's moves and resizes;
  ``on_rearrange(previous, new)`` follows each dock by drag, for undo.
  ``tree_dock()``, ``tree_dock_edge()`` and ``tree_remove()`` are the same
  operations on plain data.
- **The action bar stays clear.** ``under_bar=True`` ends the tiles above
  the island's floating bar, so it never covers a tile's edge.
- **A tile can fit its content.** ``DockTile(fit=True)`` (a mixer's strips)
  is as large as its content asks for along its split: the seam stops there
  and the neighbour takes the rest, so no inset is padded with empty well.
  A side whose tiles all fit (``split("h", 0.5, "widgets", "audio")`` under
  the preview) is as tall as the taller of them (two stacked add up). Either
  is capped at ``fit_max`` of the dock along that split (the
  ``--lumaui-dock-fit-max-pct`` token, 40%); content past the cap scrolls in
  its tile and the main tile never gives up more. Call ``refit()`` after
  changing such a tile's content. Fitting is the default size only: a
  person drags the seam anywhere between the tiles' minimums, that size is
  kept (``"size"`` on the split in ``layout()``), and a double click on the
  seam fits the side to its content again. A seam with no room to move
  (both sides at their minimum) shows no mark and no resize cursor.
- **Headers are a band.** A tile's header (or tab strip) is one step toward
  the island from the well (``@luma_dock_header``), with a hairline under it;
  ``set_status("Live", tone="live")`` puts a dot and a word after the title.
- **Radii stay concentric.** Every tile is LumaUI's card radius
  (``--lumaui-dock-tile-radius``, Settings' grouped cards), never sharpened
  to fit the island: the island holding a dock grows to that radius plus the
  gap instead.
- **A phone shows the main tile.** Under 560 px the main tile fills the
  dock and the others wait as raised keys above it; each opens as a bottom
  drawer (the kit's drawer, from the layer host). A tile that fits its
  content stays in view under the main tile instead, at its own height (up
  to ``fit_max`` of the dock, then it scrolls); ``DockTile(phone="drawer")``
  makes it a key and a drawer like the others.
"""
from __future__ import annotations

from typing import Callable, Iterable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gtk  # noqa: E402

from . import lumaui

__all__ = ["Dock", "DockTile", "EDGES", "PHONE", "ZONES", "tree_keys", "tree_remove", "tree_dock", "tree_dock_edge"]

EDGES = ("left", "right", "top", "bottom", "center")
#: Where a dragged tile lands on another: an edge band splits that tile, the middle stacks them as tabs.
ZONES = ("left", "right", "top", "bottom", "tabs")
#: The share of the dock a tile dropped on the dock's own edge takes (a full-height column, a full-width row).
EDGE_SHARE = 0.25
#: How close to the dock's outer edge a drop is a full-span drop (px), and an edge band's share of a tile.
OUTER_BAND = 24
EDGE_BAND = 0.25
#: The drop preview's move from one place to the next (ms).
PREVIEW_MS = 120
#: A drag starts once the pointer has moved this far (px), so a click on a header stays a click.
DRAG_THRESHOLD = 8
#: Where a tile other than the main one goes on a phone.
#: set_status tones: None (muted) or "live" (the recording red dot and word).
STATUS_TONES = (None, "live")
PHONE = ("inline", "drawer")
_SAVE_DELAY_MS = 300


def _tokens() -> dict:
    from . import lumaui_tokens

    return getattr(lumaui_tokens, "DOCK", {"gap": 8, "min_size": 96, "header_height": 36, "fit_max_pct": 40})


# ── Layout trees (pure data) ──
#
# A layout is a tile key, a split {"split": "h"|"v", "ratio", "start", "end"[, "size"]}, or a tab
# group {"tabs": [keys], "current": key} (one place showing one of its tiles at a time).


def tree_keys(node) -> list[str]:
    """Every tile key in a layout, in order."""
    if node is None:
        return []
    if isinstance(node, str):
        return [node]
    if "tabs" in node:
        return list(node["tabs"])
    return tree_keys(node["start"]) + tree_keys(node["end"])


def _tabs(keys: list[str], current: str | None):
    """A tab group of `keys`, or the one key itself, or None."""
    if not keys:
        return None
    if len(keys) == 1:
        return keys[0]
    return {"tabs": list(keys), "current": current if current in keys else keys[0]}


def tree_remove(node, key: str):
    """The layout without `key`; a tab group left with one tile becomes that tile, and a split left
    with one side becomes that side (no empty places stay)."""
    if node is None or node == key:
        return None
    if isinstance(node, str):
        return node
    if "tabs" in node:
        keys = [k for k in node["tabs"] if k != key]
        current = node.get("current")
        return _tabs(keys, current if current != key else (keys[0] if keys else None))
    start, end = tree_remove(node["start"], key), tree_remove(node["end"], key)
    if start is None or end is None:
        return start if end is None else end
    if start is node["start"] and end is node["end"]:
        return node
    return {**node, "start": start, "end": end}


def _slot_of(node, key: str):
    """The place `key` is shown in: the key itself, or the tab group holding it."""
    if isinstance(node, str):
        return node if node == key else None
    if "tabs" in node:
        return node if key in node["tabs"] else None
    return _slot_of(node["start"], key) or _slot_of(node["end"], key)


def _replace_slot(node, slot, new):
    if node is slot or node == slot and isinstance(slot, str):
        return new
    if isinstance(node, str) or "tabs" in node:
        return node
    return {**node, "start": _replace_slot(node["start"], slot, new), "end": _replace_slot(node["end"], slot, new)}


def tree_dock(node, key: str, target: str, zone: str):
    """Move tile `key` onto `target`'s place: an edge zone splits that place in half with `key` on that
    side; "tabs" stacks them as tabs, `key` showing. `target` may be in a tab group (the group splits)."""
    if zone not in ZONES:
        raise ValueError(f"a drop zone is one of {ZONES}, not {zone!r}")
    if key == target:
        return node
    rest = tree_remove(node, key)
    slot = _slot_of(rest, target)
    if slot is None:
        raise ValueError(f"no tile {target!r} in the layout")
    if zone == "tabs":
        keys = list(slot["tabs"]) if isinstance(slot, dict) else [slot]
        new = _tabs(keys + [key], key)
    else:
        first = zone in ("left", "top")
        new = {"split": "h" if zone in ("left", "right") else "v", "ratio": 0.5,
               "start": key if first else slot, "end": slot if first else key}
    return _replace_slot(rest, slot, new)


def _covering(node, keys: set):
    """The smallest part of a layout holding every key in ``keys``."""
    if isinstance(node, dict) and "split" in node:
        for side in ("start", "end"):
            if keys <= set(tree_keys(node[side])):
                return _covering(node[side], keys)
    return node


def _replace_node(node, old, new):
    """The layout with the part ``old`` (by identity; a tile by its key) replaced by ``new``."""
    if node is old or (isinstance(old, str) and node == old):
        return new
    if isinstance(node, str) or "tabs" in node:
        return node
    return {**node, "start": _replace_node(node["start"], old, new), "end": _replace_node(node["end"], old, new)}


def _split_holding(node, key: str):
    """The split that has `key` itself as one side, if any."""
    if isinstance(node, str) or node is None or "tabs" in node:
        return None
    if key in (node["start"], node["end"]):
        return node
    return _split_holding(node["start"], key) or _split_holding(node["end"], key)


def tree_dock_edge(node, key: str, edge: str, share: float = EDGE_SHARE):
    """Move tile `key` to the dock's own `edge`: a full-height column (left, right) or a full-width row
    (top, bottom) taking `share` of the dock; everything else keeps its arrangement in the rest."""
    if edge not in ("left", "right", "top", "bottom"):
        raise ValueError(f"a dock edge is left, right, top or bottom, not {edge!r}")
    rest = tree_remove(node, key)
    if rest is None:
        return key
    first = edge in ("left", "top")
    return {"split": "h" if edge in ("left", "right") else "v", "ratio": share if first else 1 - share,
            "start": key if first else rest, "end": rest if first else key}


class DockTile(Gtk.Box):
    """One inset of a dock: a header (glyph, title, its own actions, More) over the tile's content."""

    __gtype_name__ = "LumaUIDockTile"

    def __init__(self, key: str, title: str, icon: str, child: Gtk.Widget, *, main: bool = False,
                 bare: bool = False, fit: bool = False, phone: str | None = None,
                 actions: Iterable[Gtk.Widget] = (), more: Callable[[], list] | None = None,
                 closable: bool = False) -> None:
        """``bare``: the content alone, without the well or the header (the stage an app's work is
        shown on, such as a preview); it still takes its place in the layout and receives drops.
        ``fit``: as large as the content asks for along its split, up to the dock's ``fit_max``;
        past that the content scrolls (see the module's docs).
        ``phone``: "inline" (under the main tile; the default for a tile that fits) or "drawer" (a
        raised key over the main tile that opens a bottom drawer; the default otherwise)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        if not key:
            raise ValueError("a dock tile has a key")
        if phone is None:
            phone = "inline" if fit else "drawer"
        if phone not in PHONE:
            raise ValueError(f"a tile on a phone is one of {PHONE}, not {phone!r}")
        if phone == "inline" and not fit:
            raise ValueError('only a tile that fits its content stays inline on a phone (fit=True)')
        self.key, self.title, self.icon, self.main, self.fit, self.phone = key, title, icon, main, fit, phone
        self.more = more
        #: Its More menu has "Close panel" (``Dock.on_close`` may refuse); ``Dock.open_tile`` brings it back.
        self.closable = closable
        self.dock: Dock | None = None
        self.add_css_class("lumaui-dock-tile")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.header = Gtk.Box()
        self.header.add_css_class("lumaui-dock-tile-header")
        from .icons import image

        glyph = image(icon)
        glyph.add_css_class("lumaui-dock-tile-icon")
        self.header.append(glyph)
        self.title_label = Gtk.Label(label=title, xalign=0, ellipsize=3)
        self.title_label.add_css_class("lumaui-dock-tile-title")
        self.header.append(self.title_label)
        # set_status: a dot and a word after the title; the box takes the header's spare width either way.
        self.status = Gtk.Box(hexpand=True, halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        self.status.add_css_class("lumaui-dock-tile-status")
        self.status_dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.status_dot.add_css_class("lumaui-dock-tile-status-dot")
        self.status_label = Gtk.Label(visible=False)
        self.status.append(self.status_dot)
        self.status.append(self.status_label)
        self.header.append(self.status)
        self.actions = Gtk.Box()
        self.actions.add_css_class("lumaui-dock-tile-actions")
        for action in actions:
            self.actions.append(action)
        self.header.append(self.actions)
        from .content_controls import IconOnlyButton

        self.more_button = IconOnlyButton("ellipsis", f"More for {title.lower()}")
        self.more_button.add_css_class("lumaui-dock-tile-more")
        self.more_button.connect("clicked", self._more)
        self.header.append(self.more_button)
        self.append(self.header)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.body.add_css_class("lumaui-dock-tile-body")
        #: A fitted tile's content scrolls past the dock's cap; its natural size is still the content's.
        self.scroller: Gtk.ScrolledWindow | None = None
        if fit:
            self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                               propagate_natural_width=True, hexpand=True, vexpand=True)
            self.scroller.add_css_class("lumaui-dock-tile-scroll")
            self.scroller.set_child(child)
            child = self.scroller
        self.body.append(child)
        self.append(self.body)
        self.bare = bare
        if bare:
            self.add_css_class("bare")
            self.header.set_visible(False)

    def set_title(self, title: str) -> None:
        self.title = title
        self.title_label.set_label(title)
        self._describe()

    def set_status(self, text: str | None, tone: str | None = None) -> None:
        """A short state after the title in the header ("Live"), with a dot; ``tone="live"`` is the
        recording red. None clears it. The tile's accessible label carries it ("Preview, Live")."""
        if tone not in STATUS_TONES:
            raise ValueError(f"a dock tile's status tone is one of {STATUS_TONES}, not {tone!r}")
        shown = bool(text)
        self.status_label.set_label(text or "")
        self.status_label.set_visible(shown)
        self.status_dot.set_visible(shown)
        lumaui.set_css_class(self.status, "live", shown and tone == "live")
        self._describe()

    def _describe(self) -> None:
        status = self.status_label.get_label() if self.status_label.get_visible() else ""
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{self.title}, {status}" if status else self.title])

    def _restore_header(self) -> None:
        """Its header back as it was (a tab group borrows its actions and More, and hides it)."""
        if self.actions.get_parent() is not self.header:
            if self.actions.get_parent() is not None:
                self.actions.get_parent().remove(self.actions)
            self.header.insert_child_after(self.actions, self.status)
        if self.more_button.get_parent() is not self.header:
            if self.more_button.get_parent() is not None:
                self.more_button.get_parent().remove(self.more_button)
            self.header.insert_child_after(self.more_button, self.actions)
        self.header.set_visible(not self.bare)

    def _more(self, button: Gtk.Button) -> None:
        from .action_bubble import FloatingMenu, MenuItem

        rows = list(self.more()) if self.more is not None else []
        if self.dock is not None and not self.dock.phone:
            moves = [MenuItem(label, icon=icon, on_activate=lambda d=direction: self.dock.move_toward(self.key, d))
                     for direction, label, icon in (("left", "Move left", "arrow-left"),
                                                    ("right", "Move right", "arrow-right"),
                                                    ("up", "Move up", "arrow-up"),
                                                    ("down", "Move down", "arrow-down"))
                     if self.dock.neighbour(self.key, direction) is not None]
            if rows and moves:
                rows.append(None)
            rows += moves
        if self.dock is not None and self.closable and self.dock.can_close(self.key):
            if rows:
                rows.append(None)
            rows.append(MenuItem("Close panel", icon="x", on_activate=lambda: self.dock.request_close(self.key)))
        if rows:
            FloatingMenu(rows, label=f"More for {self.title.lower()}").popup(button)


class Dock(Gtk.Overlay):
    """Tiles that fill an island, with seams, drag-to-rearrange and saved layouts."""

    __gtype_name__ = "LumaUIDock"
    __gsignals__ = {"layout-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    @staticmethod
    def split(orientation: str, ratio: float, start, end) -> dict:
        """A layout node: ``orientation`` "h" (side by side) or "v" (stacked), the start's share."""
        if orientation not in ("h", "v"):
            raise ValueError(f"a split is h or v, not {orientation!r}")
        return {"split": orientation, "ratio": float(ratio), "start": start, "end": end}

    def __init__(self, tiles: Iterable[DockTile], *, layout=None, under_bar: bool = False,
                 fit_max: float | None = None, on_layout: Callable[[dict | str], None] | None = None) -> None:
        """``under_bar``: the island has a floating ActionCenter; the tiles stop above it.
        ``fit_max``: the most of the dock (0–1, along a split) that tiles fitting their content take;
        None is the ``fit_max_pct`` token."""
        super().__init__(hexpand=True, vexpand=True)
        if fit_max is None:
            fit_max = float(_tokens().get("fit_max_pct", 40)) / 100
        if not 0 < fit_max <= 1:
            raise ValueError(f"fit_max is a share of the dock, 0 < fit_max <= 1, not {fit_max!r}")
        self.fit_max = float(fit_max)
        self._island: Gtk.Widget | None = None
        lumaui.install()
        self.add_css_class("lumaui-dock")
        if under_bar:
            self.add_css_class("under-bar")
        self.tiles: dict[str, DockTile] = {}
        for tile in tiles:
            if tile.key in self.tiles:
                raise ValueError(f"tile keys are unique: {tile.key!r}")
            tile.dock = self
            self.tiles[tile.key] = tile
        if not self.tiles:
            raise ValueError("a dock has at least one tile")
        #: Closed tiles and the place each last had: key -> {"beside", "edge", "share"}.
        self._closed: dict[str, dict] = {}
        #: Called with a tile's key before "Close panel" closes it; returning False keeps it open.
        self.on_close: Callable[[str], bool | None] | None = None
        self.phone = False
        self._tree = None
        self._panes: list[tuple[Gtk.Paned, dict]] = []
        self._applying = 0
        self._save_id = 0
        self._drawer = None
        self.on_layout = on_layout
        self._refit_id = 0
        self._allocated = (0, 0)
        #: Called with (previous layout, new layout) after a person docks a tile somewhere else, so an app
        #: can put the previous one on its undo stack (``on_layout`` also follows).
        self.on_rearrange: Callable[[object, object], None] | None = None
        self.target = Gtk.Box(can_target=False, visible=False)
        self.target.add_css_class("lumaui-dock-target")
        self._target_rect: Gdk.Rectangle | None = None
        self._target_motion: tuple | None = None  # (from, to, start µs) while the preview moves
        self._target_tick = 0
        self.add_overlay(self.target)
        self.connect("get-child-position", self._place_target)
        watch = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        watch.connect("event", self._pointer)
        self.add_controller(watch)
        self._slots: list[tuple[object, Gtk.Widget]] = []  # each place in the layout and its widget
        self._node_widgets: dict[int, Gtk.Widget] = {}  # id(layout node) -> its widget, as last built
        self._root_widget: Gtk.Widget | None = None
        self._grab: dict | None = None  # the header drag under way
        for tile in self.tiles.values():
            self._watch(tile.header, tile.key)
        self.set_layout(layout if layout is not None else self._default_layout())
        from .structure_adapt import WidthWatch

        self._width_watch = WidthWatch(self, on_tier=lambda tier: self._set_phone(tier == "phone"))

    @property
    def main_key(self) -> str:
        """The main tile: the first open tile made with ``main=True`` (several may be: whichever is open
        leads on a phone), else the first open tile."""
        keys = tree_keys(self._tree) if getattr(self, "_tree", None) is not None else list(self.tiles)
        mains = [k for k in keys if self.tiles[k].main]
        return mains[0] if mains else (keys[0] if keys else next(iter(self.tiles)))

    # ── The island ──

    def do_root(self) -> None:
        Gtk.Overlay.do_root(self)
        island = self.get_parent()
        while island is not None and not island.has_css_class("luma-island"):
            island = island.get_parent()
        self._island = island
        self._mark_island()

    def do_unroot(self) -> None:
        if self._island is not None:
            self._island.remove_css_class("lumaui-docked")
            self._island = None
        Gtk.Overlay.do_unroot(self)

    def _mark_island(self) -> None:
        """The island holding the dock is the tiles' radius and the gap round (not on a phone)."""
        if self._island is not None:
            lumaui.set_css_class(self._island, "lumaui-docked", not self.phone)
            self._island.queue_draw()

    # ── Layout data ──

    def _default_layout(self):
        keys = list(self.tiles)
        tree = keys[-1]
        for key in reversed(keys[:-1]):
            tree = self.split("h", 0.5, key, tree)
        return tree

    def layout(self):
        """The current arrangement as plain data (see the module's docs). With closed tiles it is
        ``{"layout": tree, "closed": {key: record}}``: each record brings that tile back where it
        was (its tab group and index, or the tiles it was split from, the split, side, share and size)."""
        tree = self._copy(self._tree)
        if not self._closed:
            return tree
        return {"layout": tree, "closed": {k: dict(v) for k, v in self._closed.items()}}

    @staticmethod
    def _copy(node):
        if isinstance(node, str):
            return node
        if "tabs" in node:
            return {"tabs": list(node["tabs"]), "current": node["current"]}
        copy = {"split": node["split"], "ratio": round(node["ratio"], 4),
                "start": Dock._copy(node["start"]), "end": Dock._copy(node["end"])}
        if node.get("size"):
            copy["size"] = int(node["size"])
        return copy

    def _clean(self, node, seen: set):
        """Drop unknown or repeated keys; a split that loses a side becomes the other side."""
        if isinstance(node, str):
            if node in self.tiles and node not in seen:
                seen.add(node)
                return node
            return None
        if isinstance(node, dict) and isinstance(node.get("tabs"), list):
            keys = []
            for key in node["tabs"]:
                if isinstance(key, str) and key in self.tiles and key not in seen and not self.tiles[key].bare:
                    seen.add(key)
                    keys.append(key)
            return _tabs(keys, node.get("current"))
        if not isinstance(node, dict) or node.get("split") not in ("h", "v"):
            return None
        start = self._clean(node.get("start"), seen)
        end = self._clean(node.get("end"), seen)
        if start is None or end is None:
            return start if end is None else end
        ratio = node.get("ratio", 0.5)
        ratio = min(0.95, max(0.05, float(ratio))) if isinstance(ratio, (int, float)) else 0.5
        clean = {"split": node["split"], "ratio": ratio, "start": start, "end": end}
        size = node.get("size")
        if isinstance(size, (int, float)) and size > 0:
            clean["size"] = int(size)
        return clean

    def set_layout(self, layout) -> None:
        closed: dict[str, dict] = {}
        if isinstance(layout, dict) and "layout" in layout:
            for key, place in (layout.get("closed") or {}).items():
                if key in self.tiles:
                    closed[key] = {k: place[k] for k in ("tabs", "index", "split", "side", "sibling", "ratio",
                                                         "px", "size", "beside", "edge", "share")
                                   if isinstance(place, dict) and k in place}
            layout = layout["layout"]
        seen: set[str] = set(closed)
        tree = self._clean(layout, seen)
        if tree is None:  # every tile closed: the main one opens
            first = next((k for k, t in self.tiles.items() if t.main), next(iter(self.tiles)))
            closed.pop(first, None)
            tree = first
        self._closed = closed
        for key in self.tiles:  # tiles the layout doesn't name (nor closes) join at the end
            if key not in seen:
                tree = key if tree is None else self.split("h", 0.75, tree, key)
        self._tree = tree
        self._rebuild()

    # ── Building ──

    def _detach_tiles(self) -> None:
        for tile in self.tiles.values():
            tile.remove_css_class("inline")
            tile._restore_header()
            parent = tile.get_parent()
            if isinstance(parent, Gtk.Stack):
                parent.remove(tile)
            elif isinstance(parent, Gtk.Paned):
                if parent.get_start_child() is tile:
                    parent.set_start_child(None)
                else:
                    parent.set_end_child(None)
            elif parent is not None:
                parent.remove(tile)

    def _rebuild(self) -> None:
        self._applying += 1
        try:
            self._detach_tiles()
            self._panes = []
            self._slots = []
            self._node_widgets = {}
            if self.phone:
                content = self._phone_content()
            else:
                content = self._build(self._tree)
            self._root_widget = None if self.phone else content
            frame = Gtk.Box(hexpand=True, vexpand=True)
            frame.add_css_class("lumaui-dock-frame")
            content.set_hexpand(True)
            content.set_vexpand(True)
            frame.append(content)
            self.set_child(frame)
        finally:
            self._applying -= 1

    def _build(self, node) -> Gtk.Widget:
        widget = self._build_node(node)
        self._node_widgets[id(node)] = widget
        return widget

    def _build_node(self, node) -> Gtk.Widget:
        if isinstance(node, str):
            self._slots.append((node, self.tiles[node]))
            return self.tiles[node]
        if "tabs" in node:
            group = self._build_tabs(node)
            self._slots.append((node, group))
            return group
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL if node["split"] == "h"
                          else Gtk.Orientation.VERTICAL, wide_handle=True,
                          resize_start_child=True, resize_end_child=True,
                          shrink_start_child=False, shrink_end_child=False)
        paned.add_css_class("lumaui-dock-paned")
        paned.set_start_child(self._build(node["start"]))
        paned.set_end_child(self._build(node["end"]))
        paned.update_property([Gtk.AccessibleProperty.LABEL], ["Resize"])
        self._panes.append((paned, node))
        paned.connect("notify::max-position", lambda p, _s, n=node: self._fit(p, n))
        for name in ("notify::min-position", "notify::max-position"):
            paned.connect(name, lambda p, _s: self._seam_state(p))
        self._seam_state(paned)
        paned.connect("notify::position", lambda p, _s, n=node: self._moved(p, n))
        # A double click on the seam gives a fitted side its content's size again.
        twice = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        twice.connect("pressed", lambda g, n, x, y, p=paned, nd=node: self._seam_pressed(g, n, x, y, p, nd))
        paned.add_controller(twice)
        # Only a person's move sizes a fitted side (GTK also moves the position as it lays out).
        paned.held = False
        paned.connect("move-handle", lambda p, *_a: (setattr(p, "held", True),
                                                     GLib.idle_add(lambda: setattr(p, "held", False))) and False)
        return paned

    @staticmethod
    def _separator(paned: Gtk.Paned) -> Gtk.Widget | None:
        child = paned.get_first_child()
        while child is not None and child.get_css_name() != "separator":
            child = child.get_next_sibling()
        return child

    def _seam_state(self, paned: Gtk.Paned) -> None:
        """A seam that can move shows the resize cursor and its mark; one whose sides are both at
        their fixed size (no room between the minimums) shows neither."""
        separator = self._separator(paned)
        if separator is None:
            return
        fixed = paned.get_property("max-position") <= paned.get_property("min-position")
        horizontal = paned.get_orientation() == Gtk.Orientation.HORIZONTAL
        separator.set_cursor_from_name(None if fixed else ("col-resize" if horizontal else "row-resize"))
        if fixed:
            paned.add_css_class("fixed")
        else:
            paned.remove_css_class("fixed")

    def _on_seam(self, paned: Gtk.Paned, x: float, y: float) -> bool:
        start = paned.get_start_child()
        if start is None:
            return False
        along = x if paned.get_orientation() == Gtk.Orientation.HORIZONTAL else y
        edge = start.get_width() if paned.get_orientation() == Gtk.Orientation.HORIZONTAL else start.get_height()
        return edge - 2 <= along <= edge + int(_tokens()["gap"]) + 2

    def _seam_pressed(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float, paned: Gtk.Paned,
                      node: dict) -> None:
        if n_press != 2 or not self._on_seam(paned, x, y) or "size" not in node:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        del node["size"]
        self._fit(paned, node)
        self._changed()

    def _fit(self, paned: Gtk.Paned, node: dict) -> None:
        """A size change keeps each split's share; a side that fits its content gets its content's size."""
        total = paned.get_property("max-position")
        if total <= 0:
            return
        self._applying += 1
        try:
            # The share is of the paned's own length (both tiles and the seam), not of max-position,
            # which already leaves out the end tile's minimum: 0.5 is two equal tiles.
            share = round(node["ratio"] * self._length(paned, node))
            lowest = paned.get_property("min-position")
            paned.set_position(self._clamp(paned, node, max(lowest, min(total, share)), total, exact=True))
        finally:
            self._applying -= 1

    @staticmethod
    def _length(paned: Gtk.Paned, node: dict) -> int:
        """What the two sides of a split share: the paned's length less its seam (as drawn, once laid out)."""
        horizontal = node["split"] == "h"
        start, end = paned.get_start_child(), paned.get_end_child()
        if start is not None and end is not None and start.get_width() > 0 and end.get_width() > 0:
            return (start.get_width() + end.get_width()) if horizontal else (start.get_height() + end.get_height())
        along = paned.get_width() if horizontal else paned.get_height()
        return along - int(_tokens()["gap"])

    def _fits(self, node) -> bool:
        """Every tile on this side of a split fits its content."""
        if isinstance(node, str):
            tile = self.tiles.get(node)
            return tile is not None and tile.fit
        if "tabs" in node:
            return all(self._fits(key) for key in node["tabs"])
        return self._fits(node["start"]) and self._fits(node["end"])

    def _natural(self, node, orientation: Gtk.Orientation) -> int:
        """What a side whose tiles fit asks for along ``orientation``: one tile's content; two side by
        side, the larger; two one after the other, both and the gap."""
        if isinstance(node, str):
            tile = self.tiles[node]
            across = tile.get_width() if orientation == Gtk.Orientation.VERTICAL else tile.get_height()
            return tile.measure(orientation, across if across > 0 else -1)[1]
        if "tabs" in node:
            # The current tile's content plus the strip of tabs over it (its header is hidden).
            current = self._natural(node["current"], orientation)
            return current + (int(_tokens()["header_height"]) if orientation == Gtk.Orientation.VERTICAL else 0)
        start, end = self._natural(node["start"], orientation), self._natural(node["end"], orientation)
        along = (node["split"] == "v") == (orientation == Gtk.Orientation.VERTICAL)
        return start + end + int(_tokens()["gap"]) if along else max(start, end)

    def _cap(self, orientation: Gtk.Orientation) -> int:
        """The most a fitted side takes: ``fit_max`` of the dock along ``orientation``."""
        length = self.get_height() if orientation == Gtk.Orientation.VERTICAL else self.get_width()
        return int(self.fit_max * length) if length > 0 else 0

    def _clamp(self, paned: Gtk.Paned, node: dict, position: int, total: int, *, exact: bool = False) -> int:
        """``position`` moved so a side that fits its content is no larger than it asks (``exact``: that
        size), up to the cap. When both sides fit, the share rules (a group's own split)."""
        horizontal = node["split"] == "h"
        orientation = Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL
        # The end tile's size is what the paned has past the position and its seam (max-position
        # already leaves out the end tile's minimum, so it is not the paned's length).
        length = (paned.get_width() if horizontal else paned.get_height()) - int(_tokens()["gap"])
        if length <= 0:
            return position
        fits = {side: self._fits(node[side]) for side in ("start", "end")}
        if fits["start"] == fits["end"]:
            return position
        side = "start" if fits["start"] else "end"
        if not exact:
            # A person's drag: anywhere between the tiles' own minimums (the paned keeps those).
            return position
        natural = node.get("size") or self._natural(node[side], orientation)
        if natural <= 0:
            return position
        cap = self._cap(orientation)
        if cap > 0 and not node.get("size"):
            natural = min(natural, cap)
        position = natural if side == "start" else length - natural
        return max(0, min(total, position))

    def refit(self) -> None:
        """Size the tiles that fit their content again (after that content changed)."""
        if self._refit_id or not any(tile.fit for tile in self.tiles.values()):
            return

        def again() -> bool:
            self._refit_id = 0
            for paned, node in self._panes:
                self._fit(paned, node)
            cap = self._cap(Gtk.Orientation.VERTICAL)
            for tile in self.tiles.values():  # on a phone, a fitted tile under the main one scrolls past the cap
                if tile.scroller is not None:
                    tile.scroller.set_max_content_height(cap if self.phone and cap > 0 else -1)
            return False

        # After this frame's layout: the content is measured at the width it is shown at.
        self._refit_id = GLib.idle_add(again)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.Overlay.do_size_allocate(self, width, height, baseline)
        if (width, height) != self._allocated:
            # Each size change: a content-fitting tile's height follows its width (a mixer's strips
            # wrap), and a paned does not always say its room changed.
            self._allocated = (width, height)
            self.refit()

    def _by_person(self, paned: Gtk.Paned) -> bool:
        """Whether the seam is moving because a person moves it (a pointer or touch held on it, or F8
        and the keys); GTK moving it while it lays the tiles out is not."""
        return getattr(paned, "held", False)

    def _pointer(self, controller, event) -> bool:
        """Watch every press and release in the dock before its children do (the paned's own drag
        claims the seam's press, so a gesture on the paned never sees it): a press on a seam holds
        that seam until the release."""
        event = event or controller.get_current_event()  # PyGObject may hand the signal's event as None
        if event is None:
            return False
        kind = event.get_event_type()
        if kind in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.TOUCH_BEGIN):
            native = self.get_native()
            ok, x, y = event.get_position()
            if not ok or native is None:
                return False
            tx, ty = native.get_surface_transform()
            ok, point = native.compute_point(self, Graphene.Point().init(x - tx, y - ty))
            target = self.pick(point.x, point.y, Gtk.PickFlags.DEFAULT) if ok else None
            for paned, _node in self._panes:
                if target is not None and target.get_parent() is paned and target.get_css_name() == "separator":
                    paned.held = True
        elif kind in (Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            for paned, _node in self._panes:
                paned.held = False
        return False

    def _moved(self, paned: Gtk.Paned, node: dict) -> None:
        if self._applying:
            return
        total = paned.get_property("max-position")
        if total <= 0:
            return
        if not self._by_person(paned):
            # GTK moving the seam as it lays out is not a person's choice: the share stays, and the
            # seam goes back to it once this layout is done.
            if not getattr(paned, "refit_pending", False):
                paned.refit_pending = True

                def back() -> bool:
                    paned.refit_pending = False
                    self._fit(paned, node)
                    return False

                GLib.idle_add(back)
            return
        position = paned.get_position()
        clamped = self._clamp(paned, node, position, total)
        if clamped != position:
            self._applying += 1
            try:
                paned.set_position(clamped)
            finally:
                self._applying -= 1
            position = clamped
        length = self._length(paned, node)
        node["ratio"] = min(0.95, max(0.05, position / length)) if length > 0 else node["ratio"]
        fits = {side: self._fits(node[side]) for side in ("start", "end")}
        if fits["start"] != fits["end"] and self._by_person(paned):
            # A fitted side the person sized keeps that size (saved with the layout) until a
            # double click on the seam fits it to its content again.
            horizontal = node["split"] == "h"
            length = (paned.get_width() if horizontal else paned.get_height()) - int(_tokens()["gap"])
            node["size"] = position if fits["start"] else max(1, length - position)
        self._changed()

    def _changed(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(_SAVE_DELAY_MS, self._emit_layout)

    def _emit_layout(self) -> bool:
        self._save_id = 0
        self.emit("layout-changed")
        if self.on_layout is not None:
            self.on_layout(self.layout())
        return False

    # ── Moving tiles ──

    @staticmethod
    def _swap(node, a: str, b: str):
        """The layout with tiles `a` and `b` in each other's places."""
        if isinstance(node, str):
            return b if node == a else a if node == b else node
        if "tabs" in node:
            keys = [b if k == a else a if k == b else k for k in node["tabs"]]
            current = node["current"]
            return {"tabs": keys, "current": b if current == a else a if current == b else current}
        return {**node, "start": Dock._swap(node["start"], a, b), "end": Dock._swap(node["end"], a, b)}

    def move_tile(self, key: str, target: str, edge: str) -> None:
        """Put tile ``key`` at ``edge`` of tile ``target``: an edge splits ``target``'s place, ``"tabs"``
        stacks them as tabs, ``"center"`` swaps the two (the keyboard's Move left/right/up/down)."""
        if edge not in EDGES and edge != "tabs":
            raise ValueError(f"an edge is one of {EDGES + ('tabs',)}, not {edge!r}")
        if key == target or key not in self.tiles or target not in self.tiles:
            return
        if edge == "center":
            self._apply(self._swap(self._tree, key, target))
        else:
            self._apply(tree_dock(self._tree, key, target, edge))

    def move_to_edge(self, key: str, edge: str) -> None:
        """Put tile ``key`` along the dock's own ``edge``: a full-height column or full-width row."""
        if key in self.tiles:
            self._apply(tree_dock_edge(self._tree, key, edge))

    def _apply(self, tree, *, rearranged: bool = False) -> None:
        previous = self.layout()
        self._tree = tree
        self._rebuild()
        self._changed()
        if rearranged and self.on_rearrange is not None:
            self.on_rearrange(previous, self.layout())

    # ── Closing and opening tiles ──

    def is_open(self, key: str) -> bool:
        return key in tree_keys(self._tree)

    def can_close(self, key: str) -> bool:
        """A tile can close while another stays open."""
        return self.is_open(key) and len(tree_keys(self._tree)) > 1

    def request_close(self, key: str) -> bool:
        """A person's "Close panel": ``on_close(key)`` first (False keeps it), then close it."""
        if not self.can_close(key):
            return False
        if self.on_close is not None and self.on_close(key) is False:
            return False
        self.close_tile(key)
        return True

    def close_tile(self, key: str) -> None:
        """Take tile ``key`` out of the layout; its place is remembered for ``open_tile``."""
        if not self.can_close(key):
            return
        self._closed[key] = self._place_of(key)
        self._apply(tree_remove(self._tree, key), rearranged=True)

    def open_tile(self, key: str, *, beside: str | None = None, edge: str = "right", size: int | None = None) -> None:
        """Bring tile ``key`` back.

        A tile that was closed goes back where it was: into its tab group at its index, or beside the
        part of the layout it was split from, on the same side and with the same share (the share,
        not pixels, so a window resized since keeps the proportion; a fitted side keeps its size).
        When that part has changed, beside the smallest part still holding what remains of it, at its
        old size. A tile with no place to go back to opens at ``edge`` of ``beside`` (``"tabs"`` stacks
        it), sized ``size`` px along the new split; by default right of the main tile."""
        if key not in self.tiles or self.is_open(key):
            return
        place = self._closed.pop(key, {})
        tree = self._restored(key, place)
        if tree is not None:  # a tile that was here goes back where it was, whatever the caller suggests
            self._apply(tree, rearranged=True)
            return
        if beside is None or not self.is_open(beside):
            beside, edge = self.main_key, "right"
        if edge not in ZONES:
            raise ValueError(f"a tile opens at one of {ZONES}, not {edge!r}")
        slot_widget = next((w for slot, w in self._slots if beside in (slot["tabs"] if isinstance(slot, dict)
                                                                       else [slot])), None)
        tree = tree_dock(self._tree, key, beside, edge)
        split = _split_holding(tree, key)
        if split is not None and edge != "tabs" and size is not None and slot_widget is not None:
            along = slot_widget.get_width() if split["split"] == "h" else slot_widget.get_height()
            if along > 0:
                share = min(0.95, max(0.05, size / along))
                split["ratio"] = share if split["start"] == key else 1 - share
        self._apply(tree, rearranged=True)

    def _restored(self, key: str, place: dict):
        """The layout with ``key`` back where ``place`` (its close record) says, or None."""
        tree = self._tree
        open_keys = set(tree_keys(tree))
        tabs = [k for k in place.get("tabs", []) if k in open_keys]
        if tabs:  # back into its tab group, at its index
            slot = _slot_of(tree, tabs[0])
            members = list(slot["tabs"]) if isinstance(slot, dict) else [slot]
            index = min(max(0, int(place.get("index", len(members)))), len(members))
            members.insert(index, key)
            return _replace_slot(tree, slot, {"tabs": members, "current": key})
        sibling = place.get("sibling")
        if sibling is None and place.get("beside") in open_keys:  # an older record: beside one tile
            edge = place.get("edge", "right")
            new = tree_dock(tree, key, place["beside"], edge if edge in ZONES else "right")
            split = _split_holding(new, key)
            if split is not None and place.get("share"):
                share = min(0.95, max(0.05, float(place["share"])))
                split["ratio"] = share if split["start"] == key else 1 - share
            return new
        if not sibling or place.get("split") not in ("h", "v"):
            return None
        alive = [k for k in sibling if k in open_keys]
        if not alive:
            return None
        node = _covering(tree, set(alive))
        first = place.get("side") == "start"
        if set(tree_keys(node)) == set(sibling):
            # The part it was split from is as it was: the same split, side and share.
            share = float(place.get("ratio", 0.5))
        else:
            # It changed: beside the smallest part holding what is left of it, at the old size.
            widget = self._node_widgets.get(id(node))
            along = (widget.get_width() if place["split"] == "h" else widget.get_height()) if widget else 0
            size = place.get("px") or 0
            # The new split takes that part's room; the tile gets its old size of it.
            share = size / along if along > 0 and size > 0 else float(place.get("ratio", 0.5))
        share = min(0.95, max(0.05, share))
        split = {"split": place["split"], "ratio": share if first else 1 - share,
                 "start": key if first else node, "end": node if first else key}
        if place.get("size"):
            split["size"] = int(place["size"])
        return _replace_node(tree, node, split)

    def _place_of(self, key: str) -> dict:
        """The record that brings tile ``key`` back where it is: its tab group and index, or the part it
        is split from (its tiles), the split's direction, its side, its share and size."""
        def find(node, parent=None):
            if node == key:
                return parent
            if isinstance(node, str):
                return None
            if "tabs" in node:
                return node if key in node["tabs"] else None
            return find(node["start"], node) or find(node["end"], node)

        parent = find(self._tree)
        if isinstance(parent, dict) and "tabs" in parent:
            return {"tabs": [k for k in parent["tabs"] if k != key], "index": parent["tabs"].index(key)}
        if not isinstance(parent, dict):
            return {}
        first = parent["start"] == key
        other = parent["end"] if first else parent["start"]
        tile = self.tiles[key]
        px = tile.get_width() if parent["split"] == "h" else tile.get_height()
        place = {"split": parent["split"], "side": "start" if first else "end", "sibling": tree_keys(other),
                 "ratio": round(parent["ratio"] if first else 1 - parent["ratio"], 4)}
        if px > 0:
            place["px"] = int(px)
        if parent.get("size") and self._fits(key):
            place["size"] = int(parent["size"])
        return place

    # ── Tab groups ──

    def _build_tabs(self, node: dict) -> Gtk.Widget:
        """A place holding several tiles: a strip of their tabs (the current one's actions and More at
        its end) over the current tile; the others wait in a stack."""
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        group.add_css_class("lumaui-dock-tile")
        group.add_css_class("lumaui-dock-tabs")
        group.set_overflow(Gtk.Overflow.HIDDEN)
        strip = Gtk.Box()
        strip.add_css_class("lumaui-dock-tile-header")
        strip.add_css_class("lumaui-dock-tab-strip")
        row = Gtk.Box(hexpand=True, accessible_role=Gtk.AccessibleRole.TAB_LIST)
        row.add_css_class("lumaui-dock-tab-row")
        strip.append(row)
        group.tools = Gtk.Box()
        group.tools.add_css_class("lumaui-dock-tab-tools")
        strip.append(group.tools)
        group.append(strip)
        group.stack = Gtk.Stack(hexpand=True, vexpand=True)
        group.append(group.stack)
        group.buttons = {}
        from .icons import image

        for key in node["tabs"]:
            tile = self.tiles[key]
            tile.header.set_visible(False)
            group.stack.add_named(tile, key)
            button = Gtk.Button(accessible_role=Gtk.AccessibleRole.TAB)
            button.add_css_class("lumaui-dock-tab")
            line = Gtk.Box(spacing=6)
            glyph = image(tile.icon)
            glyph.add_css_class("lumaui-dock-tile-icon")
            line.append(glyph)
            words = Gtk.Label(label=tile.title, ellipsize=3)
            words.add_css_class("lumaui-dock-tile-title")
            line.append(words)
            button.set_child(line)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tile.title])
            button.connect("clicked", lambda _b, k=key, n=node, g=group: self._select_tab(g, n, k, save=True))
            self._watch(button, key)
            row.append(button)
            group.buttons[key] = button
        self._select_tab(group, node, node["current"], save=False)
        return group

    def _select_tab(self, group: Gtk.Widget, node: dict, key: str, *, save: bool) -> None:
        for other in node["tabs"]:
            self.tiles[other]._restore_header()
            self.tiles[other].header.set_visible(False)
        node["current"] = key
        group.stack.set_visible_child_name(key)
        tile = self.tiles[key]
        for widget in (tile.actions, tile.more_button):
            widget.get_parent().remove(widget)
            group.tools.append(widget)
        for other, button in group.buttons.items():
            lumaui.set_css_class(button, "current", other == key)
            # SELECTED is a tristate (an int): a bool there is a GLib critical.
            button.update_state([Gtk.AccessibleState.SELECTED], [GObject.Value(GObject.TYPE_INT, int(other == key))])
        group.update_property([Gtk.AccessibleProperty.LABEL], [tile.title])
        if save:
            self._changed()

    def show_tile(self, key: str) -> None:
        """Bring tile ``key`` to the front of its tab group (nothing to do for a tile on its own)."""
        for slot, widget in self._slots:
            if isinstance(slot, dict) and key in slot["tabs"] and slot["current"] != key:
                self._select_tab(widget, slot, key, save=True)

    # ── Docking by dragging a header (or a tab) ──

    def _watch(self, widget: Gtk.Widget, key: str) -> None:
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._grab_begin, widget, key)
        drag.connect("drag-update", self._grab_update)
        drag.connect("drag-end", self._grab_end)
        widget.add_controller(drag)
        # On a touch screen a tile is picked up by a long press, so a swipe still scrolls.
        press = Gtk.GestureLongPress(touch_only=True, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda *_a: self._grab is not None and self._grab.update(armed=True))
        widget.add_controller(press)

    def _grab_begin(self, gesture: Gtk.GestureDrag, x: float, y: float, widget: Gtk.Widget, key: str) -> None:
        if self.phone or self.tiles[key].bare or len(self.tiles) < 2:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        device = gesture.get_device()
        touch = device is not None and device.get_source() == Gdk.InputSource.TOUCHSCREEN
        self._grab = {"key": key, "widget": widget, "x": x, "y": y, "started": False, "touch": touch,
                      "armed": False, "gesture": gesture, "zone": None, "keys": None}

    def _grab_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        grab = self._grab
        if grab is None or grab["gesture"] is not gesture:
            return
        if not grab["started"]:
            if (dx * dx + dy * dy) ** 0.5 < DRAG_THRESHOLD:
                return
            if grab["touch"] and not grab["armed"]:
                self._grab = None
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
            grab["started"] = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.tiles[grab["key"]].add_css_class("moving")
            root = self.get_root()
            if root is not None:
                keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
                keys.connect("key-pressed", self._grab_key)
                root.add_controller(keys)
                grab["keys"] = (root, keys)
        ok, point = grab["widget"].compute_point(self, Graphene.Point().init(grab["x"] + dx, grab["y"] + dy))
        if not ok:
            return
        grab["zone"] = self._zone(point.x, point.y, grab["key"])
        self._show_target(grab["zone"][2] if grab["zone"] else None)

    def _grab_end(self, gesture: Gtk.GestureDrag, _dx: float, _dy: float) -> None:
        grab = self._grab
        if grab is None or grab["gesture"] is not gesture:
            return
        zone = grab["zone"] if grab["started"] else None
        self._end_grab()
        if zone is None:
            return
        target, where, _rect = zone
        tree = tree_dock_edge(self._tree, grab["key"], where) if target is None else \
            tree_dock(self._tree, grab["key"], target, where)
        if tree != self._tree:
            self._apply(tree, rearranged=True)

    def _grab_key(self, _controller, keyval: int, _code: int, _state) -> bool:
        if keyval != Gdk.KEY_Escape or self._grab is None:
            return False
        gesture = self._grab["gesture"]
        self._end_grab()  # nothing moved: the layout is as it was
        gesture.set_state(Gtk.EventSequenceState.DENIED)
        return True

    def cancel_drag(self) -> None:
        """Stop a header drag without docking (Esc does this)."""
        if self._grab is not None:
            self._grab_key(None, Gdk.KEY_Escape, 0, 0)

    def _end_grab(self) -> None:
        grab, self._grab = self._grab, None
        if grab is None:
            return
        self.tiles[grab["key"]].remove_css_class("moving")
        if grab["keys"] is not None:
            root, keys = grab["keys"]
            root.remove_controller(keys)
        self._show_target(None)

    def _widget_rect(self, widget: Gtk.Widget) -> Gdk.Rectangle | None:
        if widget is None or not widget.get_mapped():
            return None
        ok, rect = widget.compute_bounds(self)
        if not ok:
            return None
        area = Gdk.Rectangle()
        area.x, area.y = round(rect.get_x()), round(rect.get_y())
        area.width, area.height = round(rect.get_width()), round(rect.get_height())
        return area

    def _zone(self, x: float, y: float, dragged: str) -> tuple[str | None, str, Gdk.Rectangle] | None:
        """Where a tile dragged to (`x`, `y`) would land: (target tile, zone, the rectangle it would take),
        target None for the dock's own edge; None where it can't go."""
        whole = self._widget_rect(self._root_widget)
        if whole is not None and whole.x <= x <= whole.x + whole.width and whole.y <= y <= whole.y + whole.height:
            near = min((("left", x - whole.x), ("right", whole.x + whole.width - x), ("top", y - whole.y),
                        ("bottom", whole.y + whole.height - y)), key=lambda e: e[1])
            if near[1] <= OUTER_BAND and tree_keys(self._tree) != [dragged]:
                return None, near[0], self._part(whole, near[0], EDGE_SHARE)
        for slot, widget in self._slots:
            if slot == dragged:
                continue
            r = self._widget_rect(widget)
            if r is None or not (r.x <= x <= r.x + r.width and r.y <= y <= r.y + r.height):
                continue
            fx, fy = (x - r.x) / max(1, r.width), (y - r.y) / max(1, r.height)
            edge, depth = min((("left", fx), ("right", 1 - fx), ("top", fy), ("bottom", 1 - fy)), key=lambda e: e[1])
            members = slot["tabs"] if isinstance(slot, dict) else [slot]
            target = next(k for k in members if k != dragged)
            if depth < EDGE_BAND:
                return target, edge, self._part(r, edge, 0.5)
            if dragged in members or self.tiles[target].bare:
                return None  # already there; a bare tile (a stage) takes no tabs
            return target, "tabs", r
        return None

    @staticmethod
    def _part(r: Gdk.Rectangle, edge: str, share: float) -> Gdk.Rectangle:
        area = Gdk.Rectangle()
        area.x, area.y, area.width, area.height = r.x, r.y, r.width, r.height
        if edge in ("left", "right"):
            area.width = round(r.width * share)
            if edge == "right":
                area.x = r.x + r.width - area.width
        else:
            area.height = round(r.height * share)
            if edge == "bottom":
                area.y = r.y + r.height - area.height
        return area

    def _bounds(self, key: str) -> Gdk.Rectangle | None:
        """A tile's place in the dock, or None while it isn't laid out (a phone's drawer)."""
        tile = self.tiles[key]
        if not tile.get_mapped():
            return None
        ok, rect = tile.compute_bounds(self)
        if not ok:
            return None
        area = Gdk.Rectangle()
        area.x, area.y = round(rect.get_x()), round(rect.get_y())
        area.width, area.height = round(rect.get_width()), round(rect.get_height())
        return area

    def neighbour(self, key: str, direction: str) -> str | None:
        """The nearest tile beside ``key`` in ``direction`` ("left", "right", "up", "down")."""
        here = self._bounds(key) if key in self.tiles else None
        if here is None:
            return None
        cx, cy = here.x + here.width / 2, here.y + here.height / 2
        best, best_d = None, None
        for other in self.tiles:
            if other == key:
                continue
            r = self._bounds(other)
            if r is None:
                continue
            ox, oy = r.x + r.width / 2, r.y + r.height / 2
            dx, dy = ox - cx, oy - cy
            ahead = {"left": -dx, "right": dx, "up": -dy, "down": dy}[direction]
            across = abs(dy) if direction in ("left", "right") else abs(dx)
            if ahead <= 1:
                continue
            d = ahead + 2 * across
            if best_d is None or d < best_d:
                best, best_d = other, d
        return best

    def move_toward(self, key: str, direction: str) -> None:
        """Swap ``key`` with its neighbour that way (the keyboard's way to rearrange)."""
        other = self.neighbour(key, direction)
        if other is not None:
            self.move_tile(key, other, "center")
            GLib.idle_add(lambda: self.tiles[key].more_button.grab_focus() and False)

    # ── Drag and drop ──

    def _show_target(self, rect: Gdk.Rectangle | None) -> None:
        """The drop preview: exactly where and how big the tile would land, gliding there in PREVIEW_MS."""
        if rect is None:
            self._target_motion = None
            self._target_rect = None
            self.target.set_visible(False)
            self.queue_allocate()
            return
        now = self._target_rect
        if now is not None and (now.x, now.y, now.width, now.height) == (rect.x, rect.y, rect.width, rect.height):
            return
        if now is None or not self.target.get_visible():
            self._target_rect = rect
            self.target.set_visible(True)
            self.queue_allocate()
            return
        clock = self.get_frame_clock()
        start = clock.get_frame_time() if clock is not None else GLib.get_monotonic_time()
        self._target_motion = (now, rect, start)
        if not self._target_tick:
            self._target_tick = self.add_tick_callback(self._glide)

    def _glide(self, _widget, clock) -> bool:
        if self._target_motion is None:
            self._target_tick = 0
            return GLib.SOURCE_REMOVE
        start_rect, goal, start = self._target_motion
        t = min(1.0, (clock.get_frame_time() - start) / (PREVIEW_MS * 1000))
        ease = 1 - (1 - t) ** 3
        area = Gdk.Rectangle()
        area.x = round(start_rect.x + (goal.x - start_rect.x) * ease)
        area.y = round(start_rect.y + (goal.y - start_rect.y) * ease)
        area.width = round(start_rect.width + (goal.width - start_rect.width) * ease)
        area.height = round(start_rect.height + (goal.height - start_rect.height) * ease)
        self._target_rect = area
        self.queue_allocate()
        if t >= 1:
            self._target_motion = None
            self._target_tick = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _place_target(self, _overlay, widget, allocation) -> bool:
        if widget is not self.target or self._target_rect is None:
            return False
        r = self._target_rect
        allocation.x, allocation.y, allocation.width, allocation.height = r.x, r.y, r.width, r.height
        return True

    # ── Phone ──

    def _set_phone(self, phone: bool) -> None:
        if phone == self.phone:
            return
        self.phone = phone
        lumaui.set_css_class(self, "phone", phone)
        self._mark_island()
        self._close_drawer()
        self._rebuild()

    def _phone_content(self) -> Gtk.Widget:
        from .content_controls import TextButton

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # The other tiles' keys wrap onto a second line rather than widen a narrow window.
        keys = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, max_children_per_line=6,
                           column_spacing=8, row_spacing=8)
        keys.add_css_class("lumaui-dock-keys")
        inline = []
        main_key = self.main_key
        for key, tile in self.tiles.items():
            if key == main_key or key in self._closed:
                continue
            if tile.phone == "inline":
                inline.append(tile)  # at its own height under the main tile: nothing to open
                continue
            keys.append(TextButton(tile.title, icon=tile.icon, style="raised", size="small",
                                   on_click=lambda k=key: self.open_drawer(k)))
        keys.set_visible(keys.get_first_child() is not None)
        box.append(keys)
        main = self.tiles[main_key]
        main.set_vexpand(True)
        box.append(main)
        for tile in inline:
            tile.set_vexpand(False)
            tile.add_css_class("inline")
            box.append(tile)
        return box

    def open_drawer(self, key: str) -> None:
        """On a phone: the tile in a bottom drawer (Esc or a tap outside closes it)."""
        from .structure_layers import LayerHost

        tile = self.tiles[key]
        self._close_drawer()
        parent = tile.get_parent()
        if parent is not None:
            parent.remove(tile)
        # The kit's one drawer surface (the menu drawer's), with its grab handle.
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("lumaui-menu-drawer")
        card.add_css_class("lumaui-dock-drawer")
        card.update_property([Gtk.AccessibleProperty.LABEL], [tile.title])
        grab = Gtk.Box(halign=Gtk.Align.CENTER)
        grab.add_css_class("lumaui-drawer-handle")
        card.append(grab)
        card.append(tile)
        host = LayerHost.window_host(self)
        self._drawer = (key, card, host.present_modal(card, on_cancel=self._drawer_closed, drawer=True))

    def _drawer_closed(self) -> None:
        if self._drawer is None:
            return
        key, card, _handle = self._drawer
        self._drawer = None
        tile = self.tiles[key]
        if tile.get_parent() is card:
            card.remove(tile)

    def _close_drawer(self) -> None:
        if self._drawer is not None:
            handle = self._drawer[2]
            self._drawer_closed()
            handle.close()
