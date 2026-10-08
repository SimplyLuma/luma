# SPDX-License-Identifier: Apache-2.0
"""The arrangement drawn to scale: pick a display, drag it where it sits."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo


@dataclass
class Tile:
    id: str
    title: str
    detail: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False


PADDING = 28
GAP = 4  # drawn between touching displays so each reads as its own object


class ArrangeCanvas(Gtk.DrawingArea):
    def __init__(self, *, editable: bool = True) -> None:
        super().__init__(hexpand=True, content_height=250)
        self.add_css_class("luma-displays-canvas")
        self.tiles: list[Tile] = []
        self.selected: str | None = None
        self.editable = editable
        self.on_select: Callable[[str], None] | None = None
        self.on_moved: Callable[[str, float, float], None] | None = None
        self._drag: tuple[str, float, float] | None = None  # id, start x, start y (logical)
        self._offset = (0.0, 0.0)
        self._fit = (1.0, 0.0, 0.0)
        self.set_draw_func(self._draw)
        self.set_focusable(True)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Display arrangement"])
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

    def set_tiles(self, tiles: list[Tile], selected: str | None) -> None:
        self.tiles = tiles
        self.selected = selected
        self._drag = None
        self.queue_draw()

    # ── Geometry ─────────────────────────────────────────────────────────

    def _fit_for(self, width: int, height: int) -> tuple[float, float, float]:
        if not self.tiles:
            return 1.0, 0.0, 0.0
        left = min(t.x for t in self.tiles)
        top = min(t.y for t in self.tiles)
        right = max(t.x + t.width for t in self.tiles)
        bottom = max(t.y + t.height for t in self.tiles)
        # Leave room to drag a display past the edge of the others.
        span_w, span_h = (right - left) * 1.25, (bottom - top) * 1.25
        scale = min((width - 2 * PADDING) / max(1, span_w), (height - 2 * PADDING) / max(1, span_h))
        ox = (width - (right - left) * scale) / 2 - left * scale
        oy = (height - (bottom - top) * scale) / 2 - top * scale
        return scale, ox, oy

    def _box(self, tile: Tile) -> tuple[float, float, float, float]:
        scale, ox, oy = self._fit
        x, y = tile.x * scale + ox, tile.y * scale + oy
        if self._drag and self._drag[0] == tile.id:
            x += self._offset[0]
            y += self._offset[1]
        return x + GAP / 2, y + GAP / 2, tile.width * scale - GAP, tile.height * scale - GAP

    def _hit(self, px: float, py: float) -> Tile | None:
        for tile in reversed(self.tiles):
            x, y, w, h = self._box(tile)
            if x <= px <= x + w and y <= py <= y + h:
                return tile
        return None

    # ── Input ────────────────────────────────────────────────────────────

    def _drag_begin(self, gesture: Gtk.GestureDrag, px: float, py: float) -> None:
        tile = self._hit(px, py)
        if tile is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.grab_focus()
        if tile.id != self.selected:
            self.selected = tile.id
            if self.on_select:
                self.on_select(tile.id)
        if self.editable and len(self.tiles) > 1:
            self._drag = (tile.id, px, py)
            self._offset = (0.0, 0.0)
        self.queue_draw()

    def _drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._drag:
            self._offset = (dx, dy)
            self.queue_draw()

    def _drag_end(self, _gesture, dx: float, dy: float) -> None:
        if not self._drag:
            return
        identity = self._drag[0]
        self._drag = None
        if math.hypot(dx, dy) < 4:
            self.queue_draw()
            return
        tile = next(t for t in self.tiles if t.id == identity)
        scale = self._fit[0]
        if self.on_moved:
            self.on_moved(identity, tile.x + dx / scale, tile.y + dy / scale)
        self.queue_draw()

    # ── Drawing ──────────────────────────────────────────────────────────

    def _colour(self, name: str, fallback):
        found, colour = self.get_style_context().lookup_color(name)
        return colour if found else fallback

    @staticmethod
    def _rounded(cr, x, y, w, h, r) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, _area, cr, width: int, height: int) -> None:
        ink = self.get_color()
        accent = self._colour("luma_accent", ink)
        # The stage keeps a still layout while a display is being dragged.
        if not self._drag:
            self._fit = self._fit_for(width, height)
        order = sorted(self.tiles, key=lambda t: (self._drag is not None and t.id == self._drag[0]))
        for tile in order:
            x, y, w, h = self._box(tile)
            chosen = tile.id == self.selected
            self._rounded(cr, x, y, w, h, 9)
            if chosen:
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.92)
            else:
                cr.set_source_rgba(ink.red, ink.green, ink.blue, 0.10)
            cr.fill_preserve()
            cr.set_line_width(1)
            cr.set_source_rgba(ink.red, ink.green, ink.blue, 0.0 if chosen else 0.14)
            cr.stroke()
            text = (1, 1, 1) if chosen else (ink.red, ink.green, ink.blue)
            if tile.primary and h > 36:
                # The Dash lives on the main display; show a small one there.
                dash_w = min(w * 0.42, 110)
                self._rounded(cr, x + (w - dash_w) / 2, y + h - 13, dash_w, 6, 3)
                cr.set_source_rgba(*text, 0.55)
                cr.fill()
            self._label(cr, tile, x, y, w, h, text)

    def _label(self, cr, tile: Tile, x, y, w, h, rgb) -> None:
        layout = self.create_pango_layout(tile.title)
        font = Pango.FontDescription.from_string("600 11")
        layout.set_font_description(font)
        layout.set_width(int(max(1, w - 16) * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment(Pango.Alignment.CENTER)
        detail = self.create_pango_layout(tile.detail)
        detail.set_font_description(Pango.FontDescription.from_string("10"))
        detail.set_width(int(max(1, w - 16) * Pango.SCALE))
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        detail.set_alignment(Pango.Alignment.CENTER)
        _ink, title_rect = layout.get_pixel_extents()
        _ink, detail_rect = detail.get_pixel_extents()
        show_detail = h > title_rect.height + detail_rect.height + 22
        total = title_rect.height + (detail_rect.height + 2 if show_detail else 0)
        top = y + (h - total) / 2 - (4 if tile.primary else 0)
        cr.set_source_rgba(*rgb, 1)
        cr.move_to(x + 8, top)
        PangoCairo.show_layout(cr, layout)
        if show_detail:
            cr.set_source_rgba(*rgb, 0.7)
            cr.move_to(x + 8, top + title_rect.height + 2)
            PangoCairo.show_layout(cr, detail)
