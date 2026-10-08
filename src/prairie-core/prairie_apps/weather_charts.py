# SPDX-License-Identifier: Apache-2.0
"""Weather-only instruments; palette comes exclusively from kit color roles.

TODO(kit-request weather-04-weather-palette-and-icons.md): exact sky and
instrument colors await generated Weather tokens. No app color literals.
"""
from __future__ import annotations

import math
import cairo
from gi.repository import Gtk, Gdk, Gsk, Graphene, Pango, PangoCairo

from luma_appkit import lumaui_tokens, type_font
from .weather_gradient import gradient_stops


def color(widget: Gtk.Widget, role: str, fallback: str = "luma_sky_card_ink", *, fallback_opacity=1):
    style = widget.get_style_context()
    found, value = style.lookup_color(role)
    if not found:
        found, value = style.lookup_color(fallback)
        if found:
            value.alpha *= fallback_opacity
    return value if found else widget.get_color()


def ink(context, value, opacity=1):
    context.set_source_rgba(value.red, value.green, value.blue, value.alpha * opacity)


class WeatherSky(Gtk.Widget):
    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.sky = "day"
        self.set_name("wx-sky")

    def set_sky(self, sky):
        self.sky = sky
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        # Named palette roles are kept separate from app geometry. The keeper
        # can supply exact values without changes to this renderer.
        fallbacks = {
            "day": ("luma_blue", "luma_accent", "luma_accent_ink"),
            "dusk": ("luma_violet", "luma_destructive", "luma_amber"),
            "night": ("lumaui_shade_night", "lumaui_shade_deep", "luma_menu"),
            "rain": ("luma_faint", "luma_muted", "luma_state"),
            "cloud": ("luma_faint", "luma_muted", "luma_state"),
            "snow": ("luma_faint", "luma_muted", "luma_state"),
        }[self.sky]
        colors = []
        for index, at in enumerate((0, .5 if self.sky == "dusk" else .6, 1)):
            c = color(self, f"luma_weather_sky_{self.sky}_{index + 1}", fallbacks[index])
            colors.append((at, (c.red, c.green, c.blue, c.alpha)))
        stops = []
        for at, rgba in gradient_stops(colors):
            c = Gdk.RGBA()
            c.red, c.green, c.blue, c.alpha = rgba
            stop = Gsk.ColorStop()
            stop.offset, stop.color = at, c
            stops.append(stop)
        snapshot.append_linear_gradient(Graphene.Rect().init(0, 0, width, height),
                                        Graphene.Point().init(0, 0), Graphene.Point().init(0, height), stops)


class WeatherHours(Gtk.Box):
    """The hourly strip's 40px trailing alpha fade, over a kit ScrollView."""
    def __init__(self, child):
        super().__init__()
        self.append(child)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        stops = []
        for at, alpha in ((0, 1), (max(0, 1 - 40 / width), 1), (1, 0)):
            value = color(self, "luma_sky_card_ink")
            value.alpha = alpha
            stop = Gsk.ColorStop()
            stop.offset, stop.color = at, value
            stops.append(stop)
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_linear_gradient(Graphene.Rect().init(0, 0, width, height),
                                        Graphene.Point().init(0, 0), Graphene.Point().init(width, 0), stops)
        snapshot.pop()
        Gtk.Box.do_snapshot(self, snapshot)
        snapshot.pop()


class WeatherPrecipitationSpace(Gtk.Widget):
    """Reserve the hourly em's natural height, overlapping its glyph by6px."""
    def __init__(self, precipitation):
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.precipitation = precipitation

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 0, -1, -1
        if not self.precipitation.get_visible():
            return 6, 6, -1, -1
        minimum, natural, _, _ = self.precipitation.measure(orientation, for_size)
        # v70 .wxh em: min-height12px, margin-top:-6px. Empty text has
        # no line box; visible text keeps the kit role's actual line height.
        return max(12, minimum) - 6, max(12, natural) - 6, -1, -1


class ForecastDay(Gtk.Box):
    """A Weather forecast line with the inset divider from its sky card."""
    def __init__(self, *, first=False):
        super().__init__(spacing=8, height_request=42)
        self.first = first

    def do_snapshot(self, snapshot):
        Gtk.Box.do_snapshot(self, snapshot)
        if not self.first:
            divider = color(self, "luma_sky_card_ink")
            divider.alpha *= .12
            snapshot.append_color(divider,
                                  Graphene.Rect().init(0, 0, self.get_width(), 1))


class ForecastGraphic(Gtk.DrawingArea):
    def __init__(self, kind, *, value=None, maximum=None, low=0, high=1, scale=(0, 1), current=None):
        sizes = {"range": (-1, 13), "wind": (72, 72), "sun": (-1, 52), "index": (-1, 13)}
        width, height = sizes[kind]
        super().__init__(width_request=width, height_request=height, hexpand=width < 0,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.kind, self.value, self.maximum = kind, value, maximum
        self.low, self.high, self.scale, self.current = low, high, scale, current
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def do_snapshot(self, snapshot):
        Gtk.DrawingArea.do_snapshot(self, snapshot)
        if self.kind != "sun" or self.value is None:
            return
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        scale = min(width / 160, height / 72)
        x = (width - 160 * scale) / 2 + (8 + self.value * 144) * scale
        y = (height - 72 * scale) / 2 + (64 - math.sin(self.value * math.pi) * 50) * scale
        radius = 6 * scale
        bounds = Graphene.Rect().init(x - radius, y - radius, 2 * radius, 2 * radius)
        circle = Gsk.RoundedRect()
        circle.init_from_rect(bounds, radius)
        shadow = Gsk.Shadow()
        shadow.color = color(self, "luma_weather_sun_glow", "luma_amber", fallback_opacity=.9)
        shadow.dx = shadow.dy = 0
        # GSK halves the shadow radius for its Gaussian sigma; v70's filter sigma is6.
        shadow.radius = 12 * scale
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.push_shadow([shadow])
        snapshot.push_rounded_clip(circle)
        snapshot.append_color(color(self, "luma_sky_card_ink"), bounds)
        snapshot.pop()
        snapshot.pop()
        snapshot.pop()

    def _track(self, context, width, height):
        context.set_line_width(5)
        context.set_line_cap(cairo.LINE_CAP_ROUND)
        ink(context, color(self, "luma_weather_range_track", "luma_media_shadow", fallback_opacity=.22))
        context.move_to(2.5, height / 2)
        context.line_to(max(2.5, width - 2.5), height / 2)
        context.stroke()

    def _marker(self, context, x, y, radius=4.5):
        if self.kind in ("range", "index"):
            ink(context, color(self, "luma_weather_marker_outline", "luma_media_shadow", fallback_opacity=.25))
            context.arc(x, y, radius + 2, 0, 2 * math.pi)
            context.fill()
        ink(context, color(self, "luma_sky_card_ink"))
        context.arc(x, y, radius, 0, 2 * math.pi)
        context.fill()

    def _draw(self, _area, context, width, height):
        if self.kind in ("range", "index"):
            self._track(context, width, height)
            span = self.scale[1] - self.scale[0]
            start = (self.low - self.scale[0]) / span if span else 0
            end = (self.high - self.scale[0]) / span if span else 1
            if self.kind == "index":
                start, end = 0, 1
            gradient = cairo.LinearGradient(start * width, 0, end * width, 0)
            roles = ("luma_blue", "luma_amber", "luma_destructive") if self.kind == "range" else (
                "luma_green", "luma_amber", "luma_amber", "luma_destructive", "luma_violet")
            colors = []
            for i, role in enumerate(roles):
                c = color(self, f"luma_weather_{self.kind}_{i + 1}", role)
                colors.append((i / (len(roles) - 1), (c.red, c.green, c.blue, c.alpha)))
            for at, rgba in gradient_stops(colors):
                gradient.add_color_stop_rgba(at, *rgba)
            context.set_source(gradient)
            left, right = max(0, start * width), min(width, end * width)
            radius = min(2.5, max(0, (right - left) / 2))
            if right > left:
                # CSS percentages describe the entire rounded fill, including its caps.
                top, bottom = height / 2 - 2.5, height / 2 + 2.5
                context.new_sub_path()
                context.arc(right - radius, top + radius, radius, -math.pi / 2, 0)
                context.arc(right - radius, bottom - radius, radius, 0, math.pi / 2)
                context.arc(left + radius, bottom - radius, radius, math.pi / 2, math.pi)
                context.arc(left + radius, top + radius, radius, math.pi, 3 * math.pi / 2)
                context.close_path()
                context.fill()
            marker = self.current if self.kind == "range" else self.value
            if marker is not None:
                fraction = (marker - self.scale[0]) / span if self.kind == "range" and span else marker / (self.maximum or 1)
                self._marker(context, max(0, min(1, fraction)) * width, height / 2)
        elif self.kind == "sun":
            scale = min(width / 160, height / 72)
            context.translate((width - 160 * scale) / 2, (height - 72 * scale) / 2)
            context.scale(scale, scale)
            ink(context, color(self, "luma_sky_card_ink"), .35)
            context.set_line_width(1.5)
            context.set_dash((3, 4))
            context.move_to(8, 64)
            context.curve_to(56, -2.67, 104, -2.67, 152, 64)
            context.stroke()
            context.set_dash(())
            ink(context, color(self, "luma_sky_card_ink"), .25)
            context.set_line_width(1)
            context.move_to(0, 64)
            context.line_to(160, 64)
            context.stroke()
        elif self.kind == "wind":
            context.scale(width / 80, height / 80)
            ink(context, color(self, "luma_sky_card_ink"), .25)
            context.set_line_width(1.5)
            context.set_dash((2, 4))
            context.arc(40, 40, 34, 0, 2 * math.pi)
            context.stroke()
            context.set_dash(())
            layout = PangoCairo.create_layout(context)
            role = "weather-compass" if "weather_compass" in lumaui_tokens.TYPE_SCALE else "caption"
            layout.set_font_description(type_font(role))
            ink(context, color(self, "luma_sky_card_ink_secondary"))
            for i, text in enumerate(("N", "E", "S", "W")):
                layout.set_text(text, -1)
                tw, th = layout.get_pixel_size()
                context.move_to(40 + 26 * math.sin(i * math.pi / 2) - tw / 2,
                                44 - 26 * math.cos(i * math.pi / 2) - layout.get_baseline() / Pango.SCALE)
                PangoCairo.show_layout(context, layout)
            if self.value is not None:
                context.translate(40, 40)
                context.rotate(math.radians(self.value + 180))
                context.translate(-40, -40)
                ink(context, color(self, "luma_sky_card_ink"))
                context.move_to(40, 16)
                for point in ((45, 40), (40, 36), (35, 40)):
                    context.line_to(*point)
                context.close_path()
                context.fill()


class WeatherRadar(Gtk.DrawingArea):
    """The phone's Radar (v71 `.wxradar`): rain and storms over the next two hours, on a street grid.

    `step` is the scrubber's quarter-hour (0 = now … 8 = +2 h). `cells` are the
    rain cells as (x, y, rx, ry, level) on v71's 360×420 frame (level 1 light,
    2 moderate, 3 heavy); the frame slides them with the step, as v71's `.wxrg`
    does. With no cells the frame shows the place alone.
    """

    FRAME = (360, 420)

    def __init__(self, place_name, *, cells=(), step=0):
        super().__init__(hexpand=True, vexpand=False, height_request=470,
                         accessible_role=Gtk.AccessibleRole.IMG)
        self.set_name("wx-radar-canvas")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Rain radar"])
        self.place_name, self.cells, self.step = place_name, tuple(cells), step
        self.set_draw_func(self._draw)

    def set_step(self, step):
        self.step = step
        self.queue_draw()

    def _draw(self, _area, context, width, height):
        fw, fh = self.FRAME
        # preserveAspectRatio="xMidYMid slice": cover the area, centred.
        scale = max(width / fw, height / fh)
        context.translate((width - fw * scale) / 2, (height - fh * scale) / 2)
        context.scale(scale, scale)
        ink(context, color(self, "luma_weather_radar_ground", "lumaui_shade_deep"))
        context.paint()
        ink(context, color(self, "luma_sky_card_ink"), .07)
        context.set_line_width(6)
        for i in range(9):
            context.move_to(i * 45, 0)
            context.line_to(i * 45 + 30, 420)
            context.move_to(0, i * 52)
            context.line_to(360, i * 52 - 20)
        context.stroke()
        dx, dy = -80 + self.step * 18, self.step * 6
        roles = {1: ("luma_green", .5), 2: ("luma_amber", .65), 3: ("luma_destructive", .8)}
        for x, y, rx, ry, level in self.cells:
            role, alpha = roles[level]
            c = color(self, f"luma_weather_radar_{level}", role)
            # A soft edge stands in for v71's 6 px blur.
            context.save()
            context.translate(x + dx, y + dy)
            context.scale(rx, ry)
            glow = cairo.RadialGradient(0, 0, 0, 0, 0, 1 + 6 / max(rx, ry))
            glow.add_color_stop_rgba(0, c.red, c.green, c.blue, alpha)
            glow.add_color_stop_rgba(max(0, 1 - 6 / max(rx, ry)), c.red, c.green, c.blue, alpha)
            glow.add_color_stop_rgba(1, c.red, c.green, c.blue, 0)
            context.set_source(glow)
            context.arc(0, 0, 1 + 6 / max(rx, ry), 0, 2 * math.pi)
            context.fill()
            context.restore()
        ink(context, color(self, "luma_sky_card_ink"))
        context.arc(200, 200, 9, 0, 2 * math.pi)
        context.fill()
        ink(context, color(self, "luma_blue"))
        context.set_line_width(4)
        context.arc(200, 200, 7, 0, 2 * math.pi)
        context.stroke()
        ink(context, color(self, "luma_sky_card_ink"))
        context.arc(200, 200, 5, 0, 2 * math.pi)
        context.fill()
        layout = PangoCairo.create_layout(context)
        font = type_font("small")  # v71: 600 12px
        font.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(font)
        layout.set_text(self.place_name, -1)
        _ink_w, logical = layout.get_pixel_extents()
        context.move_to(212, 205 - logical.height * .75)
        PangoCairo.show_layout(context, layout)

