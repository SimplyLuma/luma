# SPDX-License-Identifier: Apache-2.0

"""Maps-only Oakland fixture scene, traced from v70's `MPMAP` geometry.

This is cartographic content: LumaUI owns the window, controls and overlays.
The scene never requests tiles, searches the network or reads a user store.
"""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from luma_appkit import icons
from .style import SCENE_PALETTES


def _rgba(hex_value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    value = hex_value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (alpha,)


def _source(ctx: cairo.Context, colour: str, alpha: float = 1.0) -> None:
    ctx.set_source_rgba(*_rgba(colour, alpha))


class FixtureMapCanvas(Gtk.Overlay):
    """The v70 sample geometry with GTK marker children for accessibility."""

    def __init__(self, fixture, *, on_select) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.set_name("mp-map")
        self.add_css_class("mp-fixture-map")
        self.fixture = fixture
        self.on_select = on_select
        self.view = dict(fixture.opening["view"])
        self.layer = "map"
        self.route = False
        self.route_mode = "drive"
        self.selected_id: str | None = None
        self.result_ids: set[str] | None = None
        self.position = (760.0, 900.0)
        self.dark = Adw.StyleManager.get_default().get_dark()
        self.drawing = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.drawing.set_draw_func(self._draw)
        self.drawing.connect("resize", self._resized)
        self.set_child(self.drawing)
        self.marks = Gtk.Fixed(hexpand=True, vexpand=True)
        self.add_overlay(self.marks)
        self.set_measure_overlay(self.marks, False)
        self._placed: list[tuple[Gtk.Widget, float, float, float, float]] = []
        self._make_places()
        self._update_treatment()
        Adw.StyleManager.get_default().connect("notify::dark", self._theme_changed)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        self.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("released", self._clicked)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)

    def _theme_changed(self, manager, _pspec) -> None:
        self.dark = manager.get_dark()
        self.drawing.queue_draw()
        self._update_treatment()

    def _update_treatment(self) -> None:
        self.remove_css_class("light")
        if not self.dark:
            self.add_css_class("light")

    def _make_places(self) -> None:
        self.poi: dict[str, Gtk.Image] = {}
        for place in self.fixture.places:
            icon = icons.image(place.icon, pixel_size=13)
            icon.add_css_class("mp-poi-icon")
            icon.set_size_request(34, 34)
            self.marks.put(icon, 0, 0)
            self._placed.append((icon, place.map_x, place.map_y, .5, .5))
            self.poi[place.id] = icon

    def _screen(self, x: float, y: float) -> tuple[float, float]:
        z = self.view["zoom"]
        return (self.get_width() / 2 + (x - self.view["x"]) * z,
                self.get_height() / 2 + (y - self.view["y"]) * z)

    def _resized(self, _area, _width: int, _height: int) -> None:
        # DrawingArea's resize signal fires after the overlay has been given
        # space. Fixed children are measured only then; a one-shot idle keeps
        # their first placement out of the top-left corner.
        GLib.idle_add(self._layout_marks)

    def _layout_marks(self) -> None:
        if self.get_width() <= 0 or self.get_height() <= 0:
            return False
        for widget, x, y, ax, ay in self._placed:
            sx, sy = self._screen(x, y)
            minimum, natural, _, _ = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
            width = max(minimum, natural)
            minimum, natural, _, _ = widget.measure(Gtk.Orientation.VERTICAL, width)
            height = max(minimum, natural)
            self.marks.move(widget, round(sx - width * ax), round(sy - height * ay))
        return False

    def set_view(self, x: float, y: float, zoom: float) -> None:
        self.view = {"x": x, "y": y, "zoom": zoom}
        self.drawing.queue_draw()
        self._layout_marks()

    def set_layer(self, layer: str) -> None:
        self.layer = layer
        if layer == "sat":
            self.add_css_class("sat")
        else:
            self.remove_css_class("sat")
        self.drawing.queue_draw()

    def set_route(self, shown: bool, mode: str = "drive") -> None:
        self.route, self.route_mode = shown, mode
        self.drawing.queue_draw()

    def set_position(self, x: float, y: float) -> None:
        self.position = (x, y)
        self.drawing.queue_draw()

    def set_selected(self, place_id: str | None) -> None:
        self.selected_id = place_id
        self.drawing.queue_draw()

    def set_results(self, place_ids: set[str] | None) -> None:
        self.result_ids = place_ids
        for place_id, icon in self.poi.items():
            icon.set_visible(place_ids is None or place_id in place_ids)
        self.drawing.queue_draw()

    def _drag_begin(self, _gesture, _x, _y) -> None:
        self._drag_at = dict(self.view)

    def _clicked(self, _gesture, _count: int, x: float, y: float) -> None:
        for place in reversed(self.fixture.places):
            sx, sy = self._screen(place.map_x, place.map_y)
            if math.hypot(x - sx, y - sy) <= 22:
                self.on_select(place)
                return

    def _drag_update(self, _gesture, dx, dy) -> None:
        z = self._drag_at["zoom"]
        self.set_view(self._drag_at["x"] - dx / z, self._drag_at["y"] - dy / z, z)

    def _scroll(self, _controller, _dx, dy) -> bool:
        factor = 1.08 if dy < 0 else 1 / 1.08
        self.set_view(self.view["x"], self.view["y"], max(.4, min(3, self.view["zoom"] * factor)))
        return True

    def _draw(self, _area, ctx: cairo.Context, width: int, height: int) -> None:
        p = SCENE_PALETTES["sat" if self.layer == "sat" else "dark" if self.dark else "light"]
        ctx.save()
        ctx.new_path()
        _source(ctx, p["land"])
        ctx.paint()
        z = self.view["zoom"]
        ctx.translate(width / 2 - self.view["x"] * z, height / 2 - self.view["y"] * z)
        ctx.scale(z, z)

        _source(ctx, p["water"])
        ctx.move_to(-400, -400); ctx.line_to(380, -400)
        ctx.curve_to(300, 0, 420, 300, 300, 560)
        ctx.curve_to(200, 780, 420, 960, 560, 1140)
        ctx.curve_to(700, 1300, 1200, 1260, 1600, 1330)
        ctx.curve_to(1900, 1380, 2200, 1340, 2400, 1360)
        ctx.line_to(2400, 1800); ctx.line_to(-400, 1800); ctx.close_path(); ctx.fill()

        _source(ctx, p["park"])
        ctx.move_to(1380, 560); ctx.curve_to(1500, 470, 1700, 500, 1760, 620)
        ctx.curve_to(1820, 760, 1740, 880, 1600, 900)
        ctx.curve_to(1480, 910, 1400, 820, 1380, 720); ctx.close_path(); ctx.fill()
        ctx.new_sub_path(); ctx.rectangle(880, 1020, 160, 110); ctx.rectangle(620, 330, 120, 160)
        ctx.arc(1260, 330, 60, 0, math.tau); ctx.fill()
        _source(ctx, p["water"])
        ctx.move_to(1450, 620); ctx.curve_to(1520, 560, 1660, 580, 1700, 660)
        ctx.curve_to(1740, 760, 1660, 840, 1570, 840)
        ctx.curve_to(1480, 840, 1430, 760, 1450, 620); ctx.close_path(); ctx.fill()

        _source(ctx, p["street"], .8); ctx.set_line_width(4)
        for x in range(440, 2300, 70):
            ctx.move_to(x, -200); ctx.line_to(x + 40, 1500)
        for y in range(-200, 1500, 62):
            ctx.move_to(300, y); ctx.line_to(2300, y - 70)
        ctx.stroke()
        arterial = p["street"] if self.layer == "transit" else p["arterial"]
        _source(ctx, arterial); ctx.set_line_width(11); ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        for x, y1, y2 in ((760, 1200, 200), (980, 1200, 100), (1180, 1200, 0), (1440, 1100, -100)):
            ctx.move_to(x, y1); ctx.line_to(x, y2)
        for x1, x2, y in ((400, 1900, 820), (500, 1400, 640), (600, 2200, 500), (500, 2000, 1000)):
            ctx.move_to(x1, y); ctx.line_to(x2, y)
        ctx.stroke()
        _source(ctx, p["street"] if self.layer == "transit" else p["freeway"])
        ctx.set_line_width(20)
        ctx.move_to(300, 1180); ctx.curve_to(700, 1120, 900, 1160, 1200, 1080)
        ctx.curve_to(1500, 1000, 1700, 1020, 2300, 940)
        ctx.move_to(1900, -200); ctx.curve_to(1860, 200, 1880, 500, 1960, 900)
        ctx.curve_to(2020, 1200, 2000, 1400, 2020, 1600); ctx.stroke()
        _source(ctx, "#a03fc7" if self.layer == "transit" else p["rail"])
        ctx.set_line_width(9 if self.layer == "transit" else 5)
        ctx.set_dash([] if self.layer == "transit" else [16, 10])
        ctx.move_to(420, 1130); ctx.curve_to(800, 1090, 1100, 1120, 1400, 1060); ctx.stroke()
        ctx.set_dash([])
        self._draw_labels(ctx, p)
        if self.route:
            points = self.fixture.route_points
            _source(ctx, "#000000", .25); ctx.set_line_width(22 / z)
            ctx.set_line_cap(cairo.LINE_CAP_ROUND); ctx.set_line_join(cairo.LINE_JOIN_ROUND)
            ctx.move_to(*points[0])
            for point in points[1:]: ctx.line_to(*point)
            ctx.stroke()
            _source(ctx, {"drive": "#329cff", "transit": "#a03fc7", "walk": "#527fae",
                          "bike": "#24a975"}.get(self.route_mode, "#329cff"))
            ctx.set_line_width(12 / z)
            if self.route_mode == "walk": ctx.set_dash([1, 18])
            ctx.move_to(*points[0])
            for point in points[1:]: ctx.line_to(*point)
            ctx.stroke()
        self._draw_pins(ctx, z)
        self._draw_poi_captions(ctx, z)
        ctx.restore()

    def _draw_pins(self, ctx: cairo.Context, z: float) -> None:
        for place in self.fixture.places:
            if self.result_ids is not None and place.id not in self.result_ids:
                continue
            radius = 20 if place.id == self.selected_id else 15
            _source(ctx, "#397fc7" if place.favourite else "#bf5145")
            ctx.new_path()
            ctx.arc(place.map_x, place.map_y, radius / z, 0, math.tau)
            ctx.fill_preserve()
            _source(ctx, "#ffffff")
            ctx.set_line_width(2.5 / z)
            ctx.stroke()
        x, y = self.position
        _source(ctx, "#4e94df", .2)
        ctx.new_path()
        ctx.arc(x, y, 26 / z, 0, math.tau)
        ctx.fill()
        _source(ctx, "#4e94df")
        ctx.new_path()
        ctx.arc(x, y, 10 / z, 0, math.tau)
        ctx.fill_preserve()
        _source(ctx, "#ffffff")
        ctx.set_line_width(3 / z)
        ctx.stroke()

    @staticmethod
    def _text(ctx: cairo.Context, content: str, x: float, baseline: float, *,
              size: float, weight: int = 500, tracking: float = 0,
              centered: bool = True, vertical: bool = False) -> None:
        layout = PangoCairo.create_layout(ctx)
        layout.set_text(content, -1)
        face = Pango.FontDescription()
        face.set_family("Figtree")
        face.set_absolute_size(round(size * Pango.SCALE))
        face.set_weight(Pango.Weight.BOLD if weight >= 700 else
                        Pango.Weight.SEMIBOLD if weight >= 600 else Pango.Weight.MEDIUM)
        layout.set_font_description(face)
        if tracking:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(round(tracking * Pango.SCALE)))
            layout.set_attributes(attrs)
        width, height = layout.get_pixel_size()
        ctx.save()
        if vertical:
            ctx.translate(x, baseline)
            ctx.rotate(math.pi / 2)
            x, baseline = 0, 0
        ctx.move_to(x - width / 2 if centered else x, baseline - height)
        PangoCairo.show_layout(ctx, layout)
        ctx.restore()

    def _draw_labels(self, ctx: cairo.Context, p: dict[str, str]) -> None:
        _source(ctx, p["text"], .32 if self.dark else .38)
        for content, x, y in (("UPTOWN", 1260, 420), ("OLD OAKLAND", 700, 700),
                              ("CHINATOWN", 900, 940), ("JACK LONDON", 820, 1120),
                              ("LAKESIDE", 1600, 960), ("ADAMS POINT", 1700, 420)):
            self._text(ctx, content, x, y, size=22, weight=700, tracking=4.84)
        _source(ctx, p["text"], .55 if self.dark else .6)
        for content, x, y, vertical in (("Broadway", 1452, 300, True),
                                        ("Franklin St", 992, 380, True),
                                        ("20th St", 1300, 492, False),
                                        ("12th St", 560, 812, False),
                                        ("Alameda Ave", 772, 1060, True)):
            self._text(ctx, content, x, y, size=15, weight=600, vertical=vertical)
        _source(ctx, "#a2a9b0" if self.dark else "#dceafa", .6)
        for content, x, y in (("OAKLAND ESTUARY", 120, 720), ("Lake Merritt", 1500, 740)):
            self._text(ctx, content, x, y, size=22, weight=500, tracking=2.64, centered=False)

    def _draw_poi_captions(self, ctx: cairo.Context, z: float) -> None:
        # Satellite changes the cartography palette, not the window's --ink.
        # Its POI captions therefore keep the current light/dark treatment.
        _source(ctx, SCENE_PALETTES["dark" if self.dark else "light"]["ink"])
        for place in self.fixture.places:
            if self.result_ids is not None and place.id not in self.result_ids:
                continue
            if z > .6 or place.id == self.selected_id:
                self._text(ctx, place.favourite or place.name,
                           place.map_x, place.map_y + 30 / z, size=13 / z, weight=650)
