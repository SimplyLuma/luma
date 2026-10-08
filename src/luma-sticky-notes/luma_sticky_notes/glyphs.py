# SPDX-License-Identifier: Apache-2.0
"""The two marks this application has to draw for itself.

The kit's precedent is explicit: an application that needs a glyph no theme
can resolve hands over a widget rather than an icon name — "Phone's dialler
and handset", in NavigationRow's own words. Sticky Notes needs two such
marks, and one piece of text the toolkit can no longer set.

Everything here is stroked on Lucide's 24-unit grid with a 2-unit stroke and
round ends, so the glyphs carry the same weight as the kit's own icons at
whatever size they are asked for, and each paints in the widget's current
foreground rather than a colour of its own.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

_GRID = 24.0
_STROKE = 2.0
_TAB_TEXT_MARGIN = 11
_TAB_TEXT_TRACKING = 900  # Pango units; the kit's meta letter-spacing, in tabs.


class _Glyph(Gtk.DrawingArea):
    """A square Lucide-grid drawing that paints in the widget's own ink."""

    def __init__(self, size: int) -> None:
        super().__init__(content_width=size, content_height=size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self.set_draw_func(self._draw)

    def _draw(self, area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        colour = area.get_color()
        cr.set_source_rgba(colour.red, colour.green, colour.blue, colour.alpha)
        scale = min(width, height) / _GRID
        cr.translate((width - _GRID * scale) / 2, (height - _GRID * scale) / 2)
        cr.scale(scale, scale)
        cr.set_line_width(_STROKE)
        cr.set_line_cap(1)  # cairo.LINE_CAP_ROUND, without importing cairo
        cr.set_line_join(1)  # cairo.LINE_JOIN_ROUND
        self.path(cr)
        cr.stroke()

    def path(self, cr) -> None:  # pragma: no cover - each glyph states its own
        raise NotImplementedError


class PlusGlyph(_Glyph):
    """The dock's new-note mark."""

    def path(self, cr) -> None:
        cr.move_to(12, 5)
        cr.line_to(12, 19)
        cr.move_to(5, 12)
        cr.line_to(19, 12)


class PinGlyph(_Glyph):
    """A pushpin seen from the side, its point down and its head tilted back."""

    def __init__(self, size: int, *, filled: bool = False) -> None:
        self._filled = filled
        super().__init__(size)

    def set_filled(self, filled: bool) -> None:
        if filled != self._filled:
            self._filled = filled
            self.queue_draw()

    def path(self, cr) -> None:
        # The head: a slab across the pin, drawn as a closed quadrilateral so
        # a pinned note can fill it rather than change colour.
        cr.move_to(9, 4)
        cr.line_to(17, 4)
        cr.line_to(15.5, 12.5)
        cr.line_to(18, 15)
        cr.line_to(6, 15)
        cr.line_to(8.5, 12.5)
        cr.close_path()
        if self._filled:
            cr.fill_preserve()
        cr.new_sub_path()
        cr.move_to(12, 15)
        cr.line_to(12, 20)


class CheckGlyph(_Glyph):
    """What a completed note wears on its tab."""

    def path(self, cr) -> None:
        cr.move_to(5, 12.5)
        cr.line_to(10, 17.5)
        cr.line_to(19, 6.5)


class TabTitle(Gtk.DrawingArea):
    """A note's title, set along the spine of its tab.

    GTK 4 removed GtkLabel's angle, so rotated text is no longer something a
    label can be asked for. This lays the title out in the widget's own font —
    so the sheet still owns the type — ellipsises it at the tab's height, and
    paints it through a rotated matrix. The text reads downwards, which is the
    direction a right-hand tab is read in.

    The drawing is hidden from assistive technology; the tab button that owns
    it carries the note's real name.
    """

    def __init__(self, text: str = "", *, width: int = 30) -> None:
        super().__init__(content_width=width)
        self.add_css_class("sticky-tab-title")
        self.set_vexpand(True)
        self._text = text
        self.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self.set_draw_func(self._draw)

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.queue_draw()

    def _draw(self, area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        available = max(1, height - 2 * _TAB_TEXT_MARGIN)
        layout = area.create_pango_layout(self._text)
        layout.set_single_paragraph_mode(True)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(available * Pango.SCALE)
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_letter_spacing_new(_TAB_TEXT_TRACKING))
        layout.set_attributes(attributes)
        _, text_height = layout.get_pixel_size()
        colour = area.get_color()
        cr.set_source_rgba(colour.red, colour.green, colour.blue, colour.alpha)
        # Rotating a quarter turn clockwise sends the layout's baseline down
        # the tab and its height out to the left, so the origin is offset by
        # half the text's height from the tab's centre line.
        cr.translate((width + text_height) / 2, _TAB_TEXT_MARGIN)
        cr.rotate(math.pi / 2)
        PangoCairo.show_layout(cr, layout)
