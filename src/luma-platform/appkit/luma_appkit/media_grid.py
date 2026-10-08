# SPDX-License-Identifier: Apache-2.0
"""LumaUI media: MediaTile and MediaGrid (backlog MD4).

A picture as a tile, and a recycling grid of them. The app says what each
picture is (favourite, selected, set aside, its duration or "Edited"); the kit
owns the tile's shape, overlays, selection ring, spacing and phone layout.

    grid = MediaGrid(items, kind="photo", on_open=show_viewer)       # Photos
    grid = MediaGrid(items, kind="library", on_select=pick, on_open=edit,
                     meta=lambda item: stars_row(item))                # Darkroom
    grid = MediaGrid(items, kind="clip", on_activate=add_to_timeline) # Reel's browser
    tile = MediaTile(texture, kind="library", selected=True, badge="Edited")

Kinds (v70):
- `photo` — `.pgrid > .pc`: square tiles 3 px apart, filling columns of at
  least 168 px (`big=True`: 1.55 times that, for months), three columns at
  phone width unless an explicit tile size is requested. The group is one shape: every corner on its outside edge is
  round (12), every inside corner square, worked out from each tile's real
  neighbours (`pCorners`, audit #8). A favourite shows a filled white heart.
- `library` — `.drgrid > .drtile`: 3:2 tiles 10 px apart in columns of at
  least 170 px, round 10, a 2.5 px selection ring 2 px outside, a 22 px meta
  row under the picture (the app's widget: stars, flag, label), "set aside"
  dims to 35% and greys by 60%, the badge ("Edited") top-left.
- `clip` — `.rlmedia`: 16:9 columns 8 px apart (two in Reel's 264 px
  browser: columns of at least 116 px), round 8, the duration
  badge bottom-right, the caption under the picture in 11.5/500.

Items are `MediaItem`s (or anything with the same properties) in a
`Gio.ListModel`; the grid is a `Gtk.GridView`, so only the visible tiles exist
and they are recycled as the list scrolls. `picture` is a `Gdk.Paintable` or a
path: paths are decoded on worker threads at the tile's size into a shared
cache and drawn when ready, so scrolling never waits on a decode. Tiles draw
their picture, heart, badge and ring in one snapshot (no child widgets but the
caption and the app's meta row).

Keyboard: arrows move between tiles, Enter opens (`on_open`), Space
activates (`on_activate`, or `on_select` with the modifiers held). Click
activates or selects (Shift extends, Ctrl toggles); a double click opens.
Each tile reads as its title, then "Favourite", the badge and "Selected".
"""
from __future__ import annotations

import math
import weakref
from typing import Callable, Iterable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from . import media_style as style  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["MediaItem", "MediaTile", "MediaGrid", "MEDIA_KINDS", "tile_corners", "tile_insets", "grid_columns"]

T = tokens.MEDIA["tile"]

#: Tile kinds, in v70's words: Photos' joined grid, Darkroom's library, Reel's clips.
MEDIA_KINDS = ("photo", "library", "clip")

_ASPECT = {"photo": 1.0, "library": 3 / 2, "clip": 16 / 9}


# ── Pure layout (no GTK state): tested directly ────────────────────────────

def grid_columns(width: float, kind: str = "photo", min_side: float | None = None, *, phone: bool = False) -> int:
    """How many columns a grid `width` px wide holds (CSS `repeat(auto-fill, minmax(min_side, 1fr))`)."""
    if kind == "photo" and phone and min_side is None:
        return int(T["phone_columns"])
    gap = _gap(kind)
    side = float(min_side or {"photo": T["min_side"], "library": T["library_min_side"],
                              "clip": T["clip_min_side"]}[kind])
    return max(1, int(math.floor((width + gap) / (side + gap))))


def tile_corners(index: int, count: int, columns: int) -> frozenset[str]:
    """Which corners of tile `index` are on the group's outside edge (v70 `pCorners`).

    A corner is outside when neither side next to it has a tile: "tl", "tr",
    "br" and "bl". A single tile has all four.
    """
    columns = max(1, columns)
    col = index % columns
    left = col > 0
    right = col < columns - 1 and index + 1 < count
    up = index >= columns
    down = index + columns < count
    corners = set()
    if not up and not left:
        corners.add("tl")
    if not up and not right:
        corners.add("tr")
    if not down and not right:
        corners.add("br")
    if not down and not left:
        corners.add("bl")
    return frozenset(corners)


def tile_insets(index: int, count: int, columns: int, gap: float) -> tuple[float, float, float]:
    """(left, right, bottom) space a cell leaves so equal cells show tiles exactly `gap` apart."""
    columns = max(1, columns)
    col = index % columns
    last_row_start = ((max(1, count) - 1) // columns) * columns
    bottom = 0.0 if index >= last_row_start else float(gap)
    return gap * col / columns, gap * (columns - 1 - col) / columns, bottom


def _gap(kind: str) -> float:
    return float({"photo": T["gap"], "library": T["library_gap"], "clip": T["clip_gap"]}[kind])


def _radius(kind: str) -> float:
    return float({"photo": T["radius"], "library": T["library_radius"], "clip": T["clip_radius"]}[kind])


# ── The item ───────────────────────────────────────────────────────────────

class MediaItem(GObject.Object):
    """One picture in a MediaGrid. Change a property and its tile redraws."""

    __gtype_name__ = "LumaUIMediaItem"

    id = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    picture = GObject.Property(type=object)
    favourite = GObject.Property(type=bool, default=False)
    selected = GObject.Property(type=bool, default=False)
    dimmed = GObject.Property(type=bool, default=False)
    badge = GObject.Property(type=str, default="")
    caption = GObject.Property(type=str, default="")
    sensitive = GObject.Property(type=bool, default=True)

    def __init__(self, id: str = "", picture: object = None, *, title: str = "", favourite: bool = False,
                 selected: bool = False, dimmed: bool = False, badge: str = "", caption: str = "",
                 sensitive: bool = True, data: object = None) -> None:
        super().__init__(id=id, title=title, favourite=favourite, selected=selected, dimmed=dimmed,
                         badge=badge or "", caption=caption or "", sensitive=sensitive)
        self.picture = picture
        #: Whatever the app wants to keep with the item (its own record).
        self.data = data


_ITEM_PROPS = ("picture", "favourite", "selected", "dimmed", "badge", "caption", "title", "sensitive")


# ── The tile ───────────────────────────────────────────────────────────────

class MediaTile(Gtk.Widget):
    """A picture as a tile: `MediaTile(texture, kind="library", selected=True, badge="Edited")`."""

    __gtype_name__ = "LumaUIMediaTile"

    def __init__(self, picture: object = None, *, kind: str = "photo", title: str = "", selected: bool = False,
                 dimmed: bool = False, favourite: bool = False, badge: str | None = None,
                 caption: str | None = None, meta: Gtk.Widget | None = None, sensitive: bool = True,
                 on_activate: Callable[[], None] | None = None, on_open: Callable[[], None] | None = None) -> None:
        if kind not in MEDIA_KINDS:
            raise ValueError(f"MediaTile kind must be one of {MEDIA_KINDS}, not {kind!r}")
        super().__init__(css_name="lumaui-media-tile", focusable=True, overflow=Gtk.Overflow.VISIBLE)
        self.kind = kind
        self.add_css_class(kind)
        self._picture: Gdk.Paintable | None = None
        self._source: str | None = None
        self._wanted: tuple | None = None
        self._request: style.TextureRequest | None = None
        self._preview_visible = False
        self._title = title
        self._selected = self._dimmed = self._favourite = False
        self._badge = ""
        self._corners: frozenset[str] = frozenset({"tl", "tr", "br", "bl"})
        self._insets = (0.0, 0.0, 0.0)
        self._hover = 0.0
        self._hover_target = 0.0
        self._hover_tick = 0
        self._item: MediaItem | None = None
        self._item_handlers: list[int] = []
        self._grid: "MediaGrid | None" = None
        self._position = 0
        self.on_activate = on_activate
        self.on_open = on_open
        self._caption = Gtk.Label(xalign=0, ellipsize=3, visible=False)
        self._caption.add_css_class("caption")
        self._caption.set_parent(self)
        self._meta: Gtk.Widget | None = None
        self.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        self.set_cursor_from_name("zoom-in" if kind == "photo" else "pointer")

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", self._clicked)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._hover_to(1.0))
        motion.connect("leave", lambda *_: self._hover_to(0.0))
        self.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

        self.connect("map", self._mapped)
        self.connect("unmap", lambda *_: self._release_preview())
        self.set_picture(picture)
        self.set_selected(selected)
        self.set_dimmed(dimmed)
        self.set_favourite(favourite)
        self.set_badge(badge)
        self.set_caption(caption)
        self.set_meta(meta)
        self.set_sensitive(sensitive)

    # ── State ──
    def set_picture(self, picture: object) -> None:
        """A `Gdk.Paintable`, a path or a `Gio.File`; paths decode off the main loop."""
        self._release_preview()
        self._picture = None
        self._source = None
        if isinstance(picture, Gio.File):
            picture = picture.get_path()
        if isinstance(picture, Gdk.Paintable):
            self._picture = picture
        elif picture:
            self._source = str(picture)
            self._load()
        self.queue_draw()

    def _decode_side(self, width: int, scale: int) -> float:
        # Preserve detail through the decoder and renderer resampling passes.
        # The bounded preview keeps cache entries finite even on large HiDPI grids.
        return min(2048, max(width, width / _ASPECT[self.kind]) * scale * 2)

    def _mapped(self, *_args) -> None:
        if self._grid is None:
            self._set_preview_visible(True)

    def _release_preview(self) -> None:
        if self._request is not None:
            self._request.cancel()
            self._request = None
        self._wanted = None
        if self._source is not None:
            self._picture = None

    def _set_preview_visible(self, visible: bool) -> None:
        self._preview_visible = visible
        if visible:
            self._load()
        else:
            self._release_preview()

    def _load(self) -> None:
        # Constructing a full-height group is not permission to decode its entire
        # model. Wait for native allocation and the real scroller's viewport.
        if not self._source or not self.get_mapped() or self.get_width() <= 0:
            return
        if self._grid is not None and not self._preview_visible:
            return
        width = self.get_width()
        scale = self.get_scale_factor() or 1
        side = self._decode_side(width, scale)
        loader = style.TextureLoader.shared()
        key = (self._source, loader.bucket(side))
        if self._wanted == key:
            return
        previous = self._picture
        self._release_preview()
        self._picture = previous  # Keep a visible lower-resolution preview while sharpening.
        self._wanted = key
        cached = loader.cached(self._source, side)
        if cached is not None:
            self._picture = cached
            self.queue_draw()
            return
        owner = weakref.ref(self)

        def arrived(texture: Gdk.Texture | None, wanted: tuple = key) -> None:
            tile = owner()
            if tile is not None and tile._wanted == wanted:
                tile._request = None
                if texture is not None and tile.get_mapped() and tile._preview_visible:
                    tile._picture = texture
                    tile.queue_draw()
                else:
                    tile._wanted = None

        self._request = loader.request(self._source, side, arrived)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        (self.add_css_class if self._selected else self.remove_css_class)("selected")
        self.update_state([Gtk.AccessibleState.SELECTED], [int(self._selected)])
        self.queue_draw()

    def set_dimmed(self, dimmed: bool) -> None:
        self._dimmed = bool(dimmed)
        (self.add_css_class if self._dimmed else self.remove_css_class)("dimmed")
        self._describe()
        self.queue_draw()

    def set_favourite(self, favourite: bool) -> None:
        self._favourite = bool(favourite)
        self._describe()
        self.queue_draw()

    def set_badge(self, badge: str | None) -> None:
        self._badge = (badge or "").strip()
        self._describe()
        self.queue_draw()

    def set_caption(self, caption: str | None) -> None:
        text = (caption or "").strip()
        self._caption.set_label(text)
        self._caption.set_visible(bool(text))
        self.queue_resize()

    def set_meta(self, meta: Gtk.Widget | None) -> None:
        """The app's row under a library tile (stars, flag, colour label)."""
        if self._meta is not None:
            self._meta.unparent()
        self._meta = meta
        if meta is not None:
            meta.add_css_class("lumaui-media-meta")
            meta.set_parent(self)
        self.queue_resize()

    def set_title(self, title: str) -> None:
        self._title = title or ""
        self._describe()

    @property
    def selected(self) -> bool:
        return self._selected

    @property
    def favourite(self) -> bool:
        return self._favourite

    @property
    def dimmed(self) -> bool:
        return self._dimmed

    @property
    def badge(self) -> str:
        return self._badge

    @property
    def corners(self) -> frozenset[str]:
        return self._corners

    def accessible_text(self) -> str:
        parts = [self._title or "Picture"]
        if self._favourite:
            parts.append("Favourite")
        if self._badge:
            parts.append(self._badge)
        if self._dimmed:
            parts.append("Set aside")
        return ", ".join(parts)

    def _describe(self) -> None:
        self.update_property([Gtk.AccessibleProperty.LABEL], [self.accessible_text()])

    # ── Grid placement (MediaGrid only) ──
    def _place(self, corners: frozenset[str], insets: tuple[float, float, float]) -> None:
        resize = insets[2] != self._insets[2]
        changed = corners != self._corners or insets != self._insets
        self._corners, self._insets = corners, insets
        if resize:
            self.queue_resize()
        elif changed:
            self.queue_draw()

    def _bind(self, item: MediaItem, grid: "MediaGrid", position: int) -> None:
        self._unbind()
        self._item, self._grid, self._position = item, grid, position
        self._sync_item()
        for prop in _ITEM_PROPS:
            self._item_handlers.append(item.connect(f"notify::{prop}", lambda *_: self._sync_item()))
        if grid._meta is not None:
            self.set_meta(grid._meta(item))

    def _unbind(self) -> None:
        self._release_preview()
        self._preview_visible = False
        if self._item is not None:
            for handler in self._item_handlers:
                self._item.disconnect(handler)
        self._item_handlers = []
        self._item = None

    def _sync_item(self) -> None:
        item = self._item
        if item is None:
            return
        if item.picture is not self._picture and str(item.picture or "") != (self._source or ""):
            self.set_picture(item.picture)
        self._title = item.title
        self.set_selected(item.selected)
        self.set_dimmed(item.dimmed)
        self.set_favourite(item.favourite)
        self.set_badge(item.badge)
        self.set_caption(item.caption)
        self.set_sensitive(item.sensitive)

    # ── Input ──
    def _mode(self, state: Gdk.ModifierType) -> str:
        if state & Gdk.ModifierType.SHIFT_MASK:
            return "extend"
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            return "toggle"
        return "replace"

    def _clicked(self, gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float) -> None:
        mode = self._mode(gesture.get_current_event_state())
        if n_press >= 2:
            self._open()
        else:
            self._activate(mode)

    def _key(self, _controller, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            self._open()
            return True
        if keyval == Gdk.KEY_space:
            self._activate(self._mode(state))
            return True
        return False

    def _activate(self, mode: str = "replace") -> None:
        if self._grid is not None and self._item is not None:
            self._grid._activated(self._item, mode)
        elif self.on_activate is not None:
            self.on_activate()
        elif self.on_open is not None:
            self.on_open()

    def _open(self) -> None:
        if self._grid is not None and self._item is not None:
            self._grid._opened(self._item)
        elif self.on_open is not None:
            self.on_open()
        elif self.on_activate is not None:
            self.on_activate()

    def _hover_to(self, target: float) -> None:
        self._hover_target = target
        from .lumaui import reduced_motion

        if reduced_motion() or not self.get_mapped():
            self._hover = target
            self.queue_draw()
            return
        if self._hover_tick:
            return
        start = [None]

        def tick(_widget: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
            now = clock.get_frame_time() / 1000.0
            if start[0] is None:
                start[0] = (now, self._hover)
            began, origin = start[0]
            span = 600.0 if self.kind == "photo" else 120.0
            t = min(1.0, (now - began) / span)
            eased = 1 - (1 - t) ** 3
            self._hover = origin + (self._hover_target - origin) * eased
            self.queue_draw()
            if t >= 1.0 or abs(self._hover - self._hover_target) < 0.001:
                self._hover = self._hover_target
                self._hover_tick = 0
                return False
            return True

        self._hover_tick = self.add_tick_callback(tick)

    # ── Layout ──
    def _picture_height(self, width: float) -> float:
        left, right, _bottom = self._insets
        return max(0.0, (width - left - right) / _ASPECT[self.kind])

    def _under(self, width: int) -> float:
        height = 0.0
        if self._caption.get_visible():
            _m, nat, _b, _n = self._caption.measure(Gtk.Orientation.VERTICAL, width)
            height += T["clip_caption_gap"] + nat
        if self._meta is not None and self._meta.get_visible():
            height += T["meta_height"]
        return height

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        natural_side = int(T["library_min_side"] if self.kind == "library" else T["min_side"])
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 24, natural_side, -1, -1
        width = for_size if for_size > 0 else natural_side
        height = self._picture_height(width) + self._under(width) + self._insets[2]
        height = int(math.ceil(height))
        return height, height, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        left, right, _bottom = self._insets
        inner = max(0, int(width - left - right))
        y = self._picture_height(width)
        if self._caption.get_visible():
            _m, nat, _b, _n = self._caption.measure(Gtk.Orientation.VERTICAL, inner)
            y += T["clip_caption_gap"]
            self._caption.size_allocate(_alloc(left, y, inner, nat), -1)
            y += nat
        if self._meta is not None and self._meta.get_visible():
            self._meta.size_allocate(_alloc(left, y, inner, T["meta_height"]), -1)
        if self._source and self._wanted is not None and self._wanted[1] < style.TextureLoader.bucket(
                self._decode_side(width, self.get_scale_factor() or 1)):
            # Grown past the decode we have (a bigger zoom): ask for a sharper one.
            self._load()

    # ── Drawing ──
    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._grid is None:
            self._load()
        width = self.get_width()
        if width <= 0:
            return
        left, right, _bottom = self._insets
        inner = width - left - right
        box = style.rect(left, 0, inner, self._picture_height(width))
        radius = _radius(self.kind)
        if self.kind == "photo":
            radii = [radius if corner in self._corners else 0.0 for corner in ("tl", "tr", "br", "bl")]
        else:
            radii = [radius] * 4
        shape = style.rounded(box, *radii)

        if self._selected and self.kind != "photo":
            ring = T["ring_width"] if self.kind == "library" else T["clip_ring_width"]
            offset = T["ring_offset"] if self.kind == "library" else T["clip_ring_offset"]
            grow = offset + ring
            outer = style.rounded(style.rect(left - grow, -grow, inner + 2 * grow, box.get_height() + 2 * grow),
                                  *[r + grow for r in radii])
            colour = style.colour(self, "luma_media_select")
            snapshot.append_border(outer, [ring] * 4, [colour] * 4)

        dim = self._dimmed and self.kind != "clip"
        if dim:
            snapshot.push_opacity(T["dim_opacity"])
            matrix, offset_vec = style.grayscale_matrix(T["dim_grayscale"])
            snapshot.push_color_matrix(matrix, offset_vec)
        snapshot.push_rounded_clip(shape)
        snapshot.append_color(style.colour(self, "luma_hover"), box)
        if self._picture is not None:
            self._draw_picture(snapshot, box)
        snapshot.pop()
        # The shared theme hairline separates dark/light photos from an equally
        # coloured canvas. An inset stroke preserves the image's outer bounds.
        if self.kind == "photo":
            snapshot.append_border(shape, [1.0] * 4, [style.colour(self, "luma_line")] * 4)
        if dim:
            snapshot.pop()
            snapshot.pop()

        if self._favourite and self.kind == "photo":
            size = T["fav_icon"]
            x, y = left + T["fav_inset"], box.get_height() - T["fav_inset"] - T["fav_padding_bottom"] - size
            shadow = style.colour(self, "luma_media_shadow", 0.5)
            snapshot.push_blur(1.5)
            style.draw_glyph(snapshot, "heart", x, y + 1, size, shadow, filled=True)
            snapshot.pop()
            style.draw_glyph(snapshot, "heart", x, y, size, style.colour(self, "luma_on_media"), filled=True)

        if self._badge:
            self._draw_badge(snapshot, box)

        if self._selected and self.kind == "photo":
            colour = style.colour(self, "luma_media_select")
            ring = T["ring_width"]
            snapshot.append_border(shape, [ring] * 4, [colour] * 4)

        for child in (self._caption, self._meta):
            if child is not None and child.get_visible():
                self.snapshot_child(child, snapshot)

    def _draw_picture(self, snapshot: Gtk.Snapshot, box: Graphene.Rect) -> None:
        picture = self._picture
        w, h = picture.get_intrinsic_width(), picture.get_intrinsic_height()
        target = style.cover_rect(w or box.get_width(), h or box.get_height(), box)
        hover = self._hover
        if hover > 0 and self.kind == "photo":
            zoom = 1 + (T["hover_scale"] - 1) * hover
            cx, cy = box.get_x() + box.get_width() / 2, box.get_y() + box.get_height() / 2
            target = style.rect(cx - (cx - target.get_x()) * zoom, cy - (cy - target.get_y()) * zoom,
                                target.get_width() * zoom, target.get_height() * zoom)
        brighten = hover > 0 and self.kind != "photo"
        if brighten:
            matrix, offset = style.brightness_matrix(1 + (T["hover_brightness"] - 1) * hover)
            snapshot.push_color_matrix(matrix, offset)
        if isinstance(picture, Gdk.Texture):
            snapshot.append_scaled_texture(picture, Gsk.ScalingFilter.TRILINEAR, target)
        else:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(target.get_x(), target.get_y()))
            picture.snapshot(snapshot, target.get_width(), target.get_height())
            snapshot.restore()
        if brighten:
            snapshot.pop()

    def _draw_badge(self, snapshot: Gtk.Snapshot, box: Graphene.Rect) -> None:
        clip = self.kind == "clip"
        size = T["clip_badge_size"] if clip else T["badge_size"]
        layout = style.text_layout(self, self._badge, size, 600, tabular=clip)
        _ink, logical = layout.get_pixel_extents()
        pad_x = T["clip_badge_padding_x"] if clip else T["badge_padding_x"]
        pad_y = 0 if clip else T["badge_padding_y"]
        inset = T["clip_badge_inset"] if clip else T["badge_inset"]
        width, height = logical.width + 2 * pad_x, logical.height + 2 * pad_y
        if clip:
            x, y = box.get_x() + box.get_width() - inset - width, box.get_height() - inset - height
        else:
            x, y = box.get_x() + inset, inset
        radius = T["clip_badge_radius"] if clip else T["badge_radius"]
        snapshot.push_rounded_clip(style.rounded(style.rect(x, y, width, height), radius))
        snapshot.append_color(style.colour(self, "luma_media_badge_scrim"), style.rect(x, y, width, height))
        snapshot.pop()
        style.append_layout(snapshot, layout, x + pad_x, y + pad_y, style.colour(self, "luma_on_media"))

    def do_unroot(self) -> None:
        self._release_preview()
        self._preview_visible = False
        if self._hover_tick:
            self.remove_tick_callback(self._hover_tick)
            self._hover_tick = 0
        self._hover = self._hover_target = 0.0
        Gtk.Widget.do_unroot(self)

    def do_dispose(self) -> None:
        self._unbind()
        for child in (self._caption, self._meta):
            if child is not None and child.get_parent() is self:
                child.unparent()
        self._meta = None


def _alloc(x: float, y: float, width: float, height: float) -> Gdk.Rectangle:
    rectangle = Gdk.Rectangle()
    rectangle.x, rectangle.y = int(round(x)), int(round(y))
    rectangle.width, rectangle.height = max(0, int(round(width))), max(0, int(round(height)))
    return rectangle


# ── The grid ───────────────────────────────────────────────────────────────

class MediaGrid(Gtk.Widget):
    """A recycling grid of MediaTiles: `MediaGrid(items, kind="photo", on_open=show)`.

    `items` is a `Gio.ListModel` of `MediaItem`s (a plain list is wrapped in a
    `Gio.ListStore`). `scrolls=False` gives a grid that takes its full height,
    for a group inside a page that scrolls as a whole (Photos' days).
    """

    __gtype_name__ = "LumaUIMediaGrid"

    def __init__(self, items: Gio.ListModel | Iterable[MediaItem] = (), *, kind: str = "photo",
                 min_side: float | None = None, big: bool = False, scrolls: bool = True,
                 on_activate: Callable[[MediaItem], None] | None = None,
                 on_open: Callable[[MediaItem], None] | None = None,
                 on_select: Callable[[MediaItem, str], None] | None = None,
                 meta: Callable[[MediaItem], Gtk.Widget] | None = None,
                 label: str = "Pictures", columns: int | None = None, gap: float | None = None) -> None:
        if kind not in MEDIA_KINDS:
            raise ValueError(f"MediaGrid kind must be one of {MEDIA_KINDS}, not {kind!r}")
        if columns is not None and (isinstance(columns,bool) or not isinstance(columns,int) or columns < 1):
            raise ValueError("columns must be a positive integer")
        if gap is not None and gap < 0:
            raise ValueError("gap must be nonnegative")
        super().__init__(css_name="lumaui-media-grid", hexpand=True, vexpand=scrolls)
        self._fixed_columns, self._gap_override = columns, gap
        self.kind = kind
        self.add_css_class(kind)
        self._min_side = min_side
        self._big = big
        self._on_activate, self._on_open, self._on_select = on_activate, on_open, on_select
        self._meta = meta
        self._columns = 0
        self._viewport_handlers: list[tuple[GObject.Object, int]] = []
        self._visibility_idle = 0
        self.connect("map", self._mapped)
        self.connect("unmap", self._unmapped)
        self._tiles: set[MediaTile] = set()
        if not isinstance(items, Gio.ListModel):
            store = Gio.ListStore(item_type=MediaItem)
            for item in items:
                store.append(item)
            items = store
        self._model = items
        self._model.connect("items-changed", self._items_changed)

        self._flat: list[MediaTile] | None = None
        self.view: Gtk.GridView | None = None
        if scrolls:
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._setup)
            factory.connect("bind", self._bind)
            factory.connect("unbind", self._unbind)
            factory.connect("teardown", self._teardown)
            self.view = Gtk.GridView(model=Gtk.NoSelection(model=self._model), factory=factory,
                                     min_columns=1, max_columns=1, single_click_activate=False)
            self.view.add_css_class("lumaui-media-view")
            self.view.add_css_class(kind)
            self.view.update_property([Gtk.AccessibleProperty.LABEL], [label])
            self.view.connect("activate", lambda _view, position: self._opened(self._model.get_item(position)))
            self._outer = Gtk.ScrolledWindow(child=self.view, hscrollbar_policy=Gtk.PolicyType.NEVER)
            self._outer.set_parent(self)
        else:
            # A group inside a page that scrolls as a whole (Photos' days): every
            # tile exists and the grid is as tall as its rows. Keep these groups
            # to what a day holds; a whole library belongs in a scrolling grid.
            self._outer = None
            self._flat = []
            self.set_accessible_role(Gtk.AccessibleRole.GRID)
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])
            self._rebuild_flat()

    def _viewport(self) -> Gtk.ScrolledWindow | None:
        if isinstance(self._outer, Gtk.ScrolledWindow):
            return self._outer
        parent = self.get_parent()
        while parent is not None:
            if isinstance(parent, Gtk.ScrolledWindow):
                return parent
            parent = parent.get_parent()
        return None

    def _mapped(self, *_args) -> None:
        self._disconnect_viewport()
        viewport = self._viewport()
        if viewport is not None:
            owner = weakref.ref(self)
            def changed(*_args):
                grid = owner()
                if grid is not None:
                    grid._queue_visibility()
            for adjustment in (viewport.get_vadjustment(), viewport.get_hadjustment()):
                self._viewport_handlers.append((adjustment, adjustment.connect("value-changed", changed)))
        self._queue_visibility()

    def _disconnect_viewport(self) -> None:
        for obj, handler in self._viewport_handlers:
            obj.disconnect(handler)
        self._viewport_handlers.clear()

    def _unmapped(self, *_args) -> None:
        self._disconnect_viewport()
        if self._visibility_idle:
            GLib.source_remove(self._visibility_idle)
            self._visibility_idle = 0
        for tile in self._tiles:
            tile._set_preview_visible(False)

    def _queue_visibility(self) -> None:
        if self._visibility_idle or not self.get_mapped():
            return
        owner = weakref.ref(self)
        def refresh():
            grid = owner()
            if grid is not None:
                grid._visibility_idle = 0
                grid._sync_visibility()
            return False
        self._visibility_idle = GLib.idle_add(refresh)

    def _sync_visibility(self) -> None:
        viewport = self._viewport()
        for tile in self._tiles:
            visible = self.get_mapped() and tile.get_mapped() and tile.get_width() > 0
            if visible and viewport is not None:
                found, bounds = tile.compute_bounds(viewport)
                visible = bool(found and bounds.get_x() < viewport.get_width() and
                               bounds.get_y() < viewport.get_height() and
                               bounds.get_x() + bounds.get_width() > 0 and
                               bounds.get_y() + bounds.get_height() > 0)
            tile._set_preview_visible(visible)

    # ── Public ──
    @property
    def model(self) -> Gio.ListModel:
        return self._model

    @property
    def columns(self) -> int:
        return self._columns

    def set_min_side(self, min_side: float | None, big: bool | None = None) -> None:
        """The smallest a tile may be (Photos' size slider); the grid refills its columns."""
        self._min_side = min_side
        if big is not None:
            self._big = big
        self.queue_resize()

    def scroll_to(self, position: int) -> None:
        if self.view is not None:
            self.view.scroll_to(position, Gtk.ListScrollFlags.FOCUS, None)
        elif self._flat and 0 <= position < len(self._flat):
            self._flat[position].grab_focus()

    def _rebuild_flat(self) -> None:
        for tile in self._flat or []:
            tile._unbind()
            tile.unparent()
        self._flat = []
        self._tiles.clear()
        for position in range(self._model.get_n_items()):
            tile = MediaTile(kind=self.kind)
            tile.set_parent(self)
            tile._bind(self._model.get_item(position), self, position)
            self._flat.append(tile)
            self._tiles.add(tile)
            self._place(tile)
        self.queue_resize()

    # ── Factory ──
    def _setup(self, _factory, list_item: Gtk.ListItem) -> None:
        tile = MediaTile(kind=self.kind)
        tile.set_focusable(False)
        list_item.set_child(tile)

    def _bind(self, _factory, list_item: Gtk.ListItem) -> None:
        tile = list_item.get_child()
        position = list_item.get_position()
        tile._bind(list_item.get_item(), self, position)
        self._tiles.add(tile)
        self._place(tile)
        if hasattr(list_item, "set_accessible_label"):
            list_item.set_accessible_label(tile.accessible_text())

    def _unbind(self, _factory, list_item: Gtk.ListItem) -> None:
        tile = list_item.get_child()
        tile._unbind()
        if self._meta is not None:
            tile.set_meta(None)
        self._tiles.discard(tile)

    def _teardown(self, _factory, list_item: Gtk.ListItem) -> None:
        list_item.set_child(None)

    def _items_changed(self, _model, _position: int, _removed: int, _added: int) -> None:
        if self._flat is not None:
            self._rebuild_flat()
            return
        # Positions after the change moved and the last row may be a new one:
        # every live tile works its corners out again.
        GLib.idle_add(self._replace_all, priority=GLib.PRIORITY_HIGH_IDLE)

    def _replace_all(self) -> bool:
        for tile in list(self._tiles):
            if tile._item is not None:
                found, position = self._model.find(tile._item) if hasattr(self._model, "find") else (False, 0)
                if found:
                    tile._position = position
            self._place(tile)
        return False

    def _place(self, tile: MediaTile) -> None:
        count = self._model.get_n_items()
        columns = max(1, self._columns or 1)
        tile._place(tile_corners(tile._position, count, columns),
                    tile_insets(tile._position, count, columns, self._gap_override if self._gap_override is not None else _gap(self.kind)))

    # ── Activation ──
    def _activated(self, item: MediaItem, mode: str) -> None:
        if self._on_select is not None:
            self._on_select(item, mode)
        elif self._on_activate is not None:
            self._on_activate(item)
        elif self._on_open is not None:
            self._on_open(item)

    def _opened(self, item: MediaItem | None) -> None:
        if item is None:
            return
        if self._on_open is not None:
            self._on_open(item)
        elif self._on_activate is not None:
            self._on_activate(item)

    # ── Layout ──
    def _min(self) -> float | None:
        side = self._min_side
        if side is None and self._big:
            side = T["min_side"]
        return side * T["big_scale"] if side is not None and self._big else side

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if self._outer is not None:
            minimum, natural, _mb, _nb = self._outer.measure(orientation, for_size)
            return minimum, natural, -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            side = int(self._side())
            # min_side selects the preferred column count, not a hard floor:
            # a single photo must still fit a viewport narrower than that size.
            return 0, side, -1, -1
        width = for_size if for_size > 0 else int(self._side()) * 4
        height = sum(self._rows(width, (self._fixed_columns or grid_columns(width, self.kind, self._min(), phone=self._phone()))))
        return height, height, -1, -1

    def _side(self) -> float:
        return float(self._min() or {"photo": T["min_side"], "library": T["library_min_side"],
                                      "clip": T["clip_min_side"]}[self.kind])

    def _phone(self) -> bool:
        from .lumaui_tokens import PHONE_MAX_WIDTH

        root = self.get_root()
        return root is not None and 0 < root.get_width() <= PHONE_MAX_WIDTH

    def _rows(self, width: int, columns: int) -> list[int]:
        """Row heights for `width` px in `columns` columns, without touching any tile's state."""
        count = len(self._flat or [])
        if not count:
            return []
        gap = self._gap_override if self._gap_override is not None else _gap(self.kind)
        inner = max(0.0, (width - gap * (columns - 1)) / columns)
        picture = inner / _ASPECT[self.kind]
        rows: list[int] = []
        for start in range(0, count, columns):
            under = max(tile._under(int(inner)) for tile in self._flat[start:start + columns])
            last = start + columns >= count
            rows.append(int(math.ceil(picture + under + (0 if last else gap))))
        return rows

    def _set_columns(self, width: int) -> None:
        columns = (self._fixed_columns or grid_columns(width, self.kind, self._min(), phone=self._phone()))
        if columns != self._columns:
            self._columns = columns
            if self.view is not None:
                self.view.set_min_columns(columns)
                self.view.set_max_columns(columns)
            for tile in list(self._tiles):
                self._place(tile)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self._set_columns(width)
        if self._outer is not None:
            self._outer.allocate(width, height, baseline, None)
            self._queue_visibility()
            return
        columns = max(1, self._columns)
        cell = width / columns
        y = 0
        for row, row_height in enumerate(self._rows(width, columns)):
            for col, tile in enumerate(self._flat[row * columns:(row + 1) * columns]):
                x0, x1 = round(col * cell), round((col + 1) * cell)
                tile.size_allocate(_alloc(x0, y, x1 - x0, row_height), -1)
            y += row_height
        self._queue_visibility()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        self._sync_visibility()
        if self._outer is not None:
            self.snapshot_child(self._outer, snapshot)
            return
        for tile in self._flat:
            if tile._preview_visible:
                self.snapshot_child(tile, snapshot)

    def do_dispose(self) -> None:
        self._unmapped()
        if self._outer is not None and self._outer.get_parent() is self:
            self._outer.unparent()
        for tile in self._flat or []:
            tile._unbind()
            if tile.get_parent() is self:
                tile.unparent()
        self._flat = []
