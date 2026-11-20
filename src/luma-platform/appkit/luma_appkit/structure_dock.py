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
- **Headers move tiles.** Drag a tile by its header onto another: the edge
  you drop on splits that tile and puts yours there; the middle swaps the
  two. The header's More menu does the same from the keyboard (Move left,
  right, up or down swaps with the nearest neighbour that way).
- **Layouts are data.** ``layout()`` is a tree of ``{"split": "h"|"v",
  "ratio": start share, "start": …, "end": …}`` with tile keys at the
  leaves, plain JSON for settings; ``set_layout()`` restores one (unknown
  keys are skipped, missing tiles join the end). ``on_layout`` follows a
  person's moves and resizes.
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

from gi.repository import Gdk, GLib, GObject, Gtk

from . import lumaui

__all__ = ["Dock", "DockTile", "EDGES", "PHONE"]

EDGES = ("left", "right", "top", "bottom", "center")
#: Where a tile other than the main one goes on a phone.
PHONE = ("inline", "drawer")
_SAVE_DELAY_MS = 300


def _tokens() -> dict:
    from . import lumaui_tokens

    return getattr(lumaui_tokens, "DOCK", {"gap": 8, "min_size": 96, "header_height": 36, "fit_max_pct": 40})


class DockTile(Gtk.Box):
    """One inset of a dock: a header (glyph, title, its own actions, More) over the tile's content."""

    __gtype_name__ = "LumaUIDockTile"

    def __init__(self, key: str, title: str, icon: str, child: Gtk.Widget, *, main: bool = False,
                 bare: bool = False, fit: bool = False, phone: str | None = None,
                 actions: Iterable[Gtk.Widget] = (), more: Callable[[], list] | None = None) -> None:
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
        self.title_label = Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=3)
        self.title_label.add_css_class("lumaui-dock-tile-title")
        self.header.append(self.title_label)
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
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", lambda *_a: Gdk.ContentProvider.new_for_value(f"lumaui-dock-tile:{self.key}")
                     if self.dock is not None and not self.dock.phone else None)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-end", lambda *_a: self.remove_css_class("moving"))
        self.header.add_controller(drag)

    def set_title(self, title: str) -> None:
        self.title = title
        self.title_label.set_label(title)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def _drag_begin(self, source: Gtk.DragSource, _drag) -> None:
        source.set_icon(Gtk.WidgetPaintable.new(self.header), 24, 18)
        self.add_css_class("moving")

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
        mains = [t for t in self.tiles.values() if t.main]
        self.main_key = mains[0].key if mains else next(iter(self.tiles))
        self.phone = False
        self._tree = None
        self._panes: list[tuple[Gtk.Paned, dict]] = []
        self._applying = 0
        self._save_id = 0
        self._drawer = None
        self.on_layout = on_layout
        self._refit_id = 0
        self._allocated = (0, 0)
        self.target = Gtk.Box(can_target=False, visible=False)
        self.target.add_css_class("lumaui-dock-target")
        self._target_rect: Gdk.Rectangle | None = None
        self.add_overlay(self.target)
        self.connect("get-child-position", self._place_target)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._drop_motion)
        drop.connect("leave", lambda *_a: self._show_target(None))
        drop.connect("drop", self._dropped)
        self.add_controller(drop)
        self.set_layout(layout if layout is not None else self._default_layout())
        from .structure_adapt import WidthWatch

        self._width_watch = WidthWatch(self, on_tier=lambda tier: self._set_phone(tier == "phone"))

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
        """The current arrangement as plain data (see the module's docs)."""
        return self._copy(self._tree)

    @staticmethod
    def _copy(node):
        if isinstance(node, str):
            return node
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
        seen: set[str] = set()
        tree = self._clean(layout, seen)
        for key in self.tiles:  # tiles the layout doesn't name join at the end
            if key not in seen:
                tree = key if tree is None else self.split("h", 0.75, tree, key)
        self._tree = tree
        self._rebuild()

    # ── Building ──

    def _detach_tiles(self) -> None:
        for tile in self.tiles.values():
            tile.remove_css_class("inline")
            parent = tile.get_parent()
            if isinstance(parent, Gtk.Paned):
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
            if self.phone:
                content = self._phone_content()
            else:
                content = self._build(self._tree)
            frame = Gtk.Box(hexpand=True, vexpand=True)
            frame.add_css_class("lumaui-dock-frame")
            content.set_hexpand(True)
            content.set_vexpand(True)
            frame.append(content)
            self.set_child(frame)
        finally:
            self._applying -= 1

    def _build(self, node) -> Gtk.Widget:
        if isinstance(node, str):
            return self.tiles[node]
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
        hold = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        hold.connect("drag-begin", lambda _g, x, y, p=paned: setattr(p, "held", self._on_seam(p, x, y)))
        hold.connect("drag-end", lambda *_a, p=paned: setattr(p, "held", False))
        paned.add_controller(hold)
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
        """What the two sides of a split share: the paned's length less its seam."""
        along = paned.get_width() if node["split"] == "h" else paned.get_height()
        return along - int(_tokens()["gap"])

    def _fits(self, node) -> bool:
        """Every tile on this side of a split fits its content."""
        if isinstance(node, str):
            tile = self.tiles.get(node)
            return tile is not None and tile.fit
        return self._fits(node["start"]) and self._fits(node["end"])

    def _natural(self, node, orientation: Gtk.Orientation) -> int:
        """What a side whose tiles fit asks for along ``orientation``: one tile's content; two side by
        side, the larger; two one after the other, both and the gap."""
        if isinstance(node, str):
            tile = self.tiles[node]
            across = tile.get_width() if orientation == Gtk.Orientation.VERTICAL else tile.get_height()
            return tile.measure(orientation, across if across > 0 else -1)[1]
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

    def _moved(self, paned: Gtk.Paned, node: dict) -> None:
        if self._applying:
            return
        total = paned.get_property("max-position")
        if total <= 0:
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
        if fits["start"] != fits["end"] and getattr(paned, "held", False):
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
    def _remove(node, key):
        if isinstance(node, str):
            return None if node == key else node
        start, end = Dock._remove(node["start"], key), Dock._remove(node["end"], key)
        if start is None or end is None:
            return end if start is None else start
        node["start"], node["end"] = start, end
        return node

    @staticmethod
    def _replace(node, key, new):
        if isinstance(node, str):
            return new if node == key else node
        node["start"] = Dock._replace(node["start"], key, new)
        node["end"] = Dock._replace(node["end"], key, new)
        return node

    def move_tile(self, key: str, target: str, edge: str) -> None:
        """Put tile ``key`` at ``edge`` of tile ``target`` (``"center"`` swaps the two)."""
        if edge not in EDGES:
            raise ValueError(f"an edge is one of {EDGES}, not {edge!r}")
        if key == target or key not in self.tiles or target not in self.tiles:
            return
        if edge == "center":
            self._tree = self._replace(self._replace(self._replace(self._tree, key, "\0"), target, key), "\0", target)
        else:
            tree = self._remove(self._tree, key)
            orientation = "h" if edge in ("left", "right") else "v"
            first = edge in ("left", "top")
            node = self.split(orientation, 0.5, key if first else target, target if first else key)
            self._tree = self._replace(tree, target, node)
        self._rebuild()
        self._changed()

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

    def _zone(self, x: float, y: float, dragged: str) -> tuple[str, str, Gdk.Rectangle] | None:
        for key in self.tiles:
            if key == dragged:
                continue
            r = self._bounds(key)
            if r is None:
                continue
            fx = (x - r.x) / max(1, r.width)
            fy = (y - r.y) / max(1, r.height)
            if not (0 <= fx <= 1 and 0 <= fy <= 1):
                continue
            if 0.3 <= fx <= 0.7 and 0.3 <= fy <= 0.7:
                return key, "center", r
            edge = min((("left", fx), ("right", 1 - fx), ("top", fy), ("bottom", 1 - fy)), key=lambda e: e[1])[0]
            area = Gdk.Rectangle()
            area.x, area.y, area.width, area.height = r.x, r.y, r.width, r.height
            if edge == "left":
                area.width //= 2
            elif edge == "right":
                area.x += area.width // 2
                area.width -= area.width // 2
            elif edge == "top":
                area.height //= 2
            else:
                area.y += area.height // 2
                area.height -= area.height // 2
            return key, edge, area
        return None

    def _dragged(self, value) -> str | None:
        text = value if isinstance(value, str) else None
        if text and text.startswith("lumaui-dock-tile:") and text[17:] in self.tiles:
            return text[17:]
        return None

    def _drop_motion(self, target: Gtk.DropTarget, x: float, y: float):
        drop = target.get_current_drop()
        key = None
        if drop is not None:
            drag = drop.get_drag()
            content = drag.get_content() if drag is not None else None
            if content is not None:
                try:
                    key = self._dragged(content.get_value(GObject.TYPE_STRING))
                except GLib.Error:
                    key = None
        zone = self._zone(x, y, key) if key else None
        self._show_target(zone[2] if zone else None)
        return Gdk.DragAction.MOVE if zone else 0

    def _dropped(self, _target, value, x: float, y: float) -> bool:
        self._show_target(None)
        key = self._dragged(value)
        zone = self._zone(x, y, key) if key else None
        if zone is None:
            return False
        self.move_tile(key, zone[0], zone[1])
        return True

    def _show_target(self, rect: Gdk.Rectangle | None) -> None:
        self._target_rect = rect
        self.target.set_visible(rect is not None)
        self.queue_allocate()

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
        for key, tile in self.tiles.items():
            if key == self.main_key:
                continue
            if tile.phone == "inline":
                inline.append(tile)  # at its own height under the main tile: nothing to open
                continue
            keys.append(TextButton(tile.title, icon=tile.icon, style="raised", size="small",
                                   on_click=lambda k=key: self.open_drawer(k)))
        keys.set_visible(keys.get_first_child() is not None)
        box.append(keys)
        main = self.tiles[self.main_key]
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
