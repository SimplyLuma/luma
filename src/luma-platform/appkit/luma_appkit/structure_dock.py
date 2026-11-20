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
- **A phone shows the main tile.** Under 560 px the main tile fills the
  dock and the others wait as raised keys above it; each opens as a bottom
  drawer (the kit's drawer, from the layer host).
"""
from __future__ import annotations

from typing import Callable, Iterable

from gi.repository import Gdk, GLib, GObject, Gtk

from . import lumaui

__all__ = ["Dock", "DockTile", "EDGES"]

EDGES = ("left", "right", "top", "bottom", "center")
_SAVE_DELAY_MS = 300


def _tokens() -> dict:
    from . import lumaui_tokens

    return getattr(lumaui_tokens, "DOCK", {"gap": 8, "min_size": 96, "header_height": 36})


class DockTile(Gtk.Box):
    """One inset of a dock: a header (glyph, title, its own actions, More) over the tile's content."""

    __gtype_name__ = "LumaUIDockTile"

    def __init__(self, key: str, title: str, icon: str, child: Gtk.Widget, *, main: bool = False,
                 actions: Iterable[Gtk.Widget] = (), more: Callable[[], list] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        if not key:
            raise ValueError("a dock tile has a key")
        self.key, self.title, self.icon, self.main = key, title, icon, main
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
        self.body.append(child)
        self.append(self.body)
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
                 on_layout: Callable[[dict | str], None] | None = None) -> None:
        """``under_bar``: the island has a floating ActionCenter; the tiles stop above it."""
        super().__init__(hexpand=True, vexpand=True)
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
        return {"split": node["split"], "ratio": round(node["ratio"], 4),
                "start": Dock._copy(node["start"]), "end": Dock._copy(node["end"])}

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
        return {"split": node["split"], "ratio": ratio, "start": start, "end": end}

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
        paned.connect("notify::position", lambda p, _s, n=node: self._moved(p, n))
        return paned

    def _fit(self, paned: Gtk.Paned, node: dict) -> None:
        """A size change keeps each split's share."""
        total = paned.get_property("max-position")
        if total <= 0:
            return
        self._applying += 1
        try:
            paned.set_position(round(node["ratio"] * total))
        finally:
            self._applying -= 1

    def _moved(self, paned: Gtk.Paned, node: dict) -> None:
        if self._applying:
            return
        total = paned.get_property("max-position")
        if total <= 0:
            return
        node["ratio"] = min(0.95, max(0.05, paned.get_position() / total))
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
        self._close_drawer()
        self._rebuild()

    def _phone_content(self) -> Gtk.Widget:
        from .content_controls import TextButton

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # The other tiles' keys wrap onto a second line rather than widen a narrow window.
        keys = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, max_children_per_line=6,
                           column_spacing=8, row_spacing=8)
        keys.add_css_class("lumaui-dock-keys")
        for key, tile in self.tiles.items():
            if key == self.main_key:
                continue
            keys.append(TextButton(tile.title, icon=tile.icon, style="raised", size="small",
                                   on_click=lambda k=key: self.open_drawer(k)))
        box.append(keys)
        main = self.tiles[self.main_key]
        main.set_vexpand(True)
        box.append(main)
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
