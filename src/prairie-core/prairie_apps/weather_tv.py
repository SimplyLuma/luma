# SPDX-License-Identifier: Apache-2.0
"""Weather on a TV: the same app, places and forecast, shown for the room.

The window shows this presentation while the shell is in TV posture
(`AppWindow.tv_posture`); the desktop, tablet and phone pages are untouched.
It is built from LumaUI's TV parts (TvStage, TvTopBar, TvFocus, TvToast,
TvIdle) and the day as the desktop tells it: one sentence, one number and one
band of sky.

    Up       the places, in the bar; moving along them switches city (no OK).
             Your location is first and stays; "+" adds a place by typing;
             OK on an open added place offers Remove.
    middle   the band: 24 hours of the sky's light. Left and Right scrub the
             hours and the whole screen follows, the sky behind it included.
    Down     ten days; moving along them shows that day from midnight.
    Back     out of a sheet, then to the TV home.

Left alone for 20 seconds it settles into Glance: the temperature and its
icon bottom left, the day's sentence, the clock where it always is, and the
place's name only when it isn't home. Any key comes back.
"""
from __future__ import annotations

import math
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from luma_appkit import icons, lumaui_tokens  # noqa: E402
from luma_appkit.content_weather import WeatherGlyph  # noqa: E402
from luma_appkit.lumaui import oklch_rgba, reduced_motion  # noqa: E402
from luma_appkit.tv_focus import LEFT, RIGHT, TvFocus  # noqa: E402
from luma_appkit.tv_idle import TvIdle  # noqa: E402
from luma_appkit.tv_parts import TvButton, TvField, TvTab, TvTopBar  # noqa: E402
from luma_appkit.tv_stage import TvLift, TvStage, add_tv_style, tv_fade, tv_material  # noqa: E402
from luma_appkit.tv_toast import TvToast  # noqa: E402

from .weather_tv_model import (  # noqa: E402
    LIGHT_DAY, LIGHT_GOLDEN, LIGHT_NIGHT, TvForecast, band_hour, short_time, sky_colours, tv_from_forecast,
)

TV = lumaui_tokens.TV
SKY_FADE_MS = 1200

# Weather's own TV sheet, in TV units (luma-tv-weather.html, after its density pass).
STYLE = """
.wx-tv-when { font-size: 28tv; font-weight: 650; color: %(ink2)s; margin-bottom: 4tv; }
.wx-tv-temp { font-size: 200tv; font-weight: 300; letter-spacing: -10tv; line-height: 0.9; }
.wx-tv-cond { font-size: 38tv; font-weight: 700; letter-spacing: -0.76tv; margin-top: 10tv; }
.wx-tv-say { font-size: 28tv; font-weight: 500; line-height: 1.3; color: %(ink2)s; margin-top: 10tv; }
.wx-tv-tile { min-height: 100tv; padding: 16tv 20tv; border-radius: 24tv; }
.wx-tv-tile-title { font-size: 17tv; font-weight: 650; letter-spacing: 0.68tv; color: %(ink3)s; }
.wx-tv-tile-title image { -gtk-icon-size: 17tv; color: %(ink3)s; }
.wx-tv-tile-title > box { border-spacing: 8tv; }
.wx-tv-tile-value { font-size: 32tv; font-weight: 650; letter-spacing: -0.64tv; }
.wx-tv-tile-unit { font-size: 20tv; font-weight: 600; color: %(ink2)s; margin-left: 6tv; }
.wx-tv-days { border-spacing: 12tv; }
.wx-tv-day { min-height: 124tv; padding: 18tv 10tv 16tv; border-radius: 22tv; }
.wx-tv-day-name { font-size: 19tv; font-weight: 650; color: %(ink2)s; }
.wx-tv-day-high { font-size: 23tv; font-weight: 700; }
.wx-tv-day-low { font-size: 23tv; font-weight: 600; color: %(ink3)s; margin-left: 8tv; }
.wx-tv-day.tv-key .wx-tv-day-name, .wx-tv-day.tv-key .wx-tv-day-low { color: alpha(%(keyink)s, .6); }
.wx-tv-glance-where { font-size: 34tv; font-weight: 650; color: %(ink2)s; margin-bottom: 4tv; }
.wx-tv-glance-temp { font-size: 300tv; font-weight: 300; letter-spacing: -15tv; margin-left: -14tv; line-height: 0.86; }
.wx-tv-glance-say { font-size: 40tv; font-weight: 600; letter-spacing: -0.4tv; margin-top: 26tv; line-height: 1.25; }
.wx-tv-glance label { text-shadow: 0 4tv 40tv rgba(0,0,0,.22); }
.wx-tv-sheet-list { border-spacing: 6tv; padding: 4tv; }
.wx-tv-row { min-height: 84tv; padding: 0 22tv; border-radius: 24tv; font-size: 30tv; }
.wx-tv-row > box { border-spacing: 18tv; }
.wx-tv-row-name { font-weight: 650; }
.wx-tv-row image { -gtk-icon-size: 30tv; }
.wx-tv-row-sub { font-size: 24tv; color: %(ink3)s; }
.wx-tv-row.tv-focus { background-image: linear-gradient(180deg, %(keytop)s, %(keybottom)s); color: %(keyink)s;
  box-shadow: inset 0 1.5tv 0 %(keyglint)s, 0 0 0 1.5tv %(keyrim)s, 0 3tv 10tv %(keydrop)s; }
.wx-tv-row.tv-focus .wx-tv-row-sub { color: alpha(%(keyink)s, .6); }
.wx-tv-none { font-size: 28tv; color: %(ink3)s; margin: 10tv 22tv; }
.wx-tv-actions { border-spacing: 14tv; }
.wx-tv-panel { border-spacing: 22tv; }
"""


def _style(opaque: bool) -> str:
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    return STYLE % {"ink2": m("ink_2"), "ink3": m("ink_3"), "keyink": m("key_ink"), "keytop": m("key_top"),
                    "keybottom": m("key_bottom"), "keyglint": m("key_glint"), "keyrim": m("key_rim"),
                    "keydrop": m("key_drop")}


def _rgba(value: str, alpha: float | None = None) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.parse(value)
    if alpha is not None:
        colour.alpha = alpha
    return colour


def _mix(a: Gdk.RGBA, b: Gdk.RGBA, t: float) -> Gdk.RGBA:
    out = Gdk.RGBA()
    out.red, out.green, out.blue = (x + (y - x) * t for x, y in ((a.red, b.red), (a.green, b.green), (a.blue, b.blue)))
    out.alpha = a.alpha + (b.alpha - a.alpha) * t
    return out


def _stops(pairs) -> list[Gsk.ColorStop]:
    stops = []
    for offset, colour in pairs:
        stop = Gsk.ColorStop()
        stop.offset, stop.color = offset, colour
        stops.append(stop)
    return stops


def _label(text: str = "", css: str = "", *, xalign: float = 0.0, wrap: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if wrap:
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for name in css.split():
        label.add_css_class(name)
    return label


def glyph(condition: str, night: bool, size: float) -> Gtk.Widget:
    """The TV's weather glyph: the kit's multicolour family, partly cloudy with the whole sun."""
    kind = {"clear": "sun", "part": "part-whole", "cloud": "cloud", "rain": "rain"}.get(condition)
    pixels = max(1, int(round(size)))
    if kind is not None:
        return WeatherGlyph(kind, night=night, size=pixels)
    image = icons.image({"snow": "snowflake", "fog": "cloud-fog", "thunder": "cloud-lightning"}.get(condition, "cloud"),
                        pixel_size=pixels)
    image.set_valign(Gtk.Align.CENTER)
    return image


# ── the sky ───────────────────────────────────────────────────────────────

class TvSky(Gtk.Widget):
    """The selected hour's sky: its light, the sun or the stars, drifting clouds or rain.

    Colours and layers cross-fade over 1.2 s when the hour changes. Clouds
    drift and rain falls unless motion is reduced. Drawn with GSK gradients:
    no blur, so it costs little on a television's GPU.
    """

    __gtype_name__ = "PrairieWeatherTvSky"
    STARS = ((.12, .18, 1.5), (.30, .08, 1), (.48, .26, 1.5), (.66, .12, 1), (.84, .30, 1.5), (.92, .06, 1),
             (.22, .38, 1), (.58, .04, 1.5), (.76, .44, 1), (.06, .52, 1))
    CLOUDS = ((900, 260, 80, -.30, 80, 0), (700, 200, 300, -.50, 110, -40), (1100, 300, 20, -.70, 140, -90))

    def __init__(self, stage: TvStage) -> None:
        super().__init__(can_target=False)
        self.stage = stage
        self._from = (_rgba("#2b5fb8"), _rgba("#0d2457"))
        self._to = self._from
        self._t = 1.0
        self._layers = {"sun": 0.0, "stars": 0.0, "clouds": 0.0, "rain": 0.0, "grey": 0.0}
        self._layers_from = dict(self._layers)
        self._layers_to = dict(self._layers)
        self._animation = None
        self._tick = 0
        self._start = time.monotonic()
        stage.connect("notify::unit", lambda *_a: self.queue_draw())

    def set_hour(self, light: str, condition: str, *, animate: bool = True) -> None:
        a, b = sky_colours(light, condition)
        current = self._current()
        self._from, self._to = current, (_rgba(a), _rgba(b))
        self._layers_from = self._layer_now()
        cloudy = condition in ("part", "cloud", "rain", "fog", "snow", "thunder")
        self._layers_to = {"sun": 1.0 if light in (LIGHT_DAY, LIGHT_GOLDEN) else 0.0,
                           "stars": 1.0 if light == LIGHT_NIGHT else 0.0,
                           "clouds": 1.0 if cloudy else 0.0,
                           "rain": 1.0 if condition in ("rain", "thunder") else 0.0,
                           "grey": 1.0 if condition in ("cloud", "rain", "fog", "snow", "thunder") else 0.0}
        if self._animation is not None:
            self._animation.pause()
        self._t = 0.0
        ms = SKY_FADE_MS if animate and not reduced_motion() else 0
        target = Adw.CallbackAnimationTarget.new(self._step)
        self._animation = Adw.TimedAnimation.new(self, 0.0, 1.0, ms, target)
        self._animation.set_easing(Adw.Easing.EASE)
        self._animation.play()
        self._ensure_tick()

    def _step(self, value: float) -> None:
        self._t = value
        self.queue_draw()

    def _current(self) -> tuple[Gdk.RGBA, Gdk.RGBA]:
        return _mix(self._from[0], self._to[0], self._t), _mix(self._from[1], self._to[1], self._t)

    def _layer_now(self) -> dict[str, float]:
        return {k: self._layers_from[k] + (self._layers_to[k] - self._layers_from[k]) * self._t for k in self._layers}

    def _ensure_tick(self) -> None:
        moving = not reduced_motion() and (self._layers_to["clouds"] or self._layers_to["rain"] or self._layers_to["stars"])
        if moving and not self._tick:
            self._tick = self.add_tick_callback(self._on_tick)
        elif not moving and self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0

    def _on_tick(self, _widget, _clock) -> bool:
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_unroot(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        Gtk.Widget.do_unroot(self)

    def do_snapshot(self, snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        u = self.stage.unit
        top, floor = self._current()
        layers = self._layer_now()
        now = 0.0 if reduced_motion() else time.monotonic() - self._start
        whole = Graphene.Rect().init(0, 0, w, h)
        snapshot.append_linear_gradient(whole, Graphene.Point().init(0, 0), Graphene.Point().init(0, h),
                                        _stops(((0, top), (1, floor))))
        glow = _mix(top, _rgba("#ffffff"), 0.2)
        clear = glow.copy()
        clear.alpha = 0
        snapshot.append_radial_gradient(whole, Graphene.Point().init(w * .78, h * .08), w * 1.2, h * .9, 0, 1,
                                        _stops(((0, glow), (.6, clear))))
        if layers["stars"] > 0.01:
            twinkle = 1 - .45 * (0.5 - 0.5 * math.cos(now / 6 * math.pi))
            snapshot.push_opacity(layers["stars"] * twinkle)
            star = _rgba("#ffffff")
            for x, y, r in self.STARS:
                radius = r * u
                rect = Graphene.Rect().init(x * w - radius, y * h - radius, radius * 2, radius * 2)
                clip = Gsk.RoundedRect()
                clip.init_from_rect(rect, radius)
                snapshot.push_rounded_clip(clip)
                snapshot.append_color(star, rect)
                snapshot.pop()
            snapshot.pop()
        if layers["sun"] > 0.01:
            size = 520 * u
            cx, cy = w - 260 * u - size / 2, 120 * u + size / 2
            reach = size / 2 * math.sqrt(2)
            sun = _rgba(oklch_rgba(0.97, 0.08, 90, .55))
            rim = _rgba(oklch_rgba(0.9, 0.12, 80, .12))
            gone = rim.copy()
            gone.alpha = 0
            snapshot.push_opacity(layers["sun"])
            snapshot.append_radial_gradient(Graphene.Rect().init(cx - size / 2, cy - size / 2, size, size),
                                            Graphene.Point().init(cx, cy), reach, reach, 0, 1,
                                            _stops(((0, sun), (.45, rim), (.7, gone))))
            snapshot.pop()
        if layers["clouds"] > 0.01:
            snapshot.push_opacity(layers["clouds"])
            white = _rgba("rgba(255,255,255,.2)")
            grey = _rgba(oklch_rgba(0.85, 0.01, 250, .3))
            puff = _mix(white, grey, layers["grey"])
            for cw, ch, top_u, left, period, delay in self.CLOUDS:
                cw, ch = (cw + 120) * u, (ch + 120) * u      # the mockup's 60-unit blur, as a soft edge
                progress = ((now - delay) % period) / period if period else 0
                x = left * w + progress * 2.6 * w - 60 * u
                y = top_u * u - 60 * u
                gone = puff.copy()
                gone.alpha = 0
                snapshot.append_radial_gradient(Graphene.Rect().init(x, y, cw, ch),
                                                Graphene.Point().init(x + cw / 2, y + ch / 2), cw / 2, ch / 2, 0, 1,
                                                _stops(((0, puff), (.55, puff), (1, gone))))
            snapshot.pop()
        if layers["rain"] > 0.01:
            self._rain(snapshot, w, h, u, now, layers["rain"])
        shade = _stops(((0, _rgba("rgba(0,0,0,.22)")), (.25, _rgba("rgba(0,0,0,0)")),
                        (.45, _rgba("rgba(0,0,0,0)")), (1, _rgba("rgba(0,0,0,.45)"))))
        snapshot.append_linear_gradient(whole, Graphene.Point().init(0, 0), Graphene.Point().init(0, h), shade)

    @staticmethod
    def _rain(snapshot, w, h, u, now, opacity) -> None:
        """Streaks at 104°: a 58-unit repeat in 180-unit tiles, falling every half second."""
        tile, period = 180 * u, 58 * u
        phase = (now % .5) / .5
        dx, dy = -40 * u * phase, tile * phase
        angle = math.radians(104)
        direction = (math.sin(angle), -math.cos(angle))
        streak = _rgba("rgba(255,255,255,.13)")
        none = _rgba("rgba(255,255,255,0)")
        stops = _stops(((0, none), (22 / 58, none), (22 / 58, streak), (24 / 58, streak), (24 / 58, none), (1, none)))
        snapshot.push_opacity(opacity)
        y = -h * .1 - tile + dy
        while y < h:
            row = Graphene.Rect().init(0, y, w, tile)
            snapshot.push_clip(row)
            ox, oy = dx, y
            snapshot.append_repeating_linear_gradient(
                row, Graphene.Point().init(ox, oy),
                Graphene.Point().init(ox + direction[0] * period, oy + direction[1] * period), stops)
            snapshot.pop()
            y += tile
        snapshot.pop()


# ── the band ──────────────────────────────────────────────────────────────

class TvBand(Gtk.Widget):
    """24 hours of the sky's light in a recessed track, scrubbed by the remote.

    Above the track: the temperature every three hours and sunrise/sunset
    times; below: the hours. Rain is one continuous hatch, revealed with
    half-hour fades at each edge. The hour marker is the key.
    """

    __gtype_name__ = "PrairieWeatherTvBand"
    MARKS, TEMPS, GAP_A, STRIP, GAP_B, HOURS = 46, 34, 14, 48, 12, 34
    HEIGHT = MARKS + TEMPS + GAP_A + STRIP + GAP_B + HOURS

    def __init__(self, stage: TvStage) -> None:
        super().__init__(focusable=False, accessible_role=Gtk.AccessibleRole.SLIDER)
        self.stage = stage
        self.hours = ()
        self.sun = ()
        self.index = 0
        self.today = True
        self.focused = False
        self._cursor = 0.0
        self._animation = None
        stage.connect("notify::unit", lambda *_a: self.queue_draw())

    def set_day(self, hours, sun, today: bool) -> None:
        self.hours, self.sun, self.today = tuple(hours), tuple(sun), today
        self.queue_draw()

    def set_hour(self, index: int, *, animate: bool = True) -> None:
        self.index = index
        if self._animation is not None:
            self._animation.pause()
        target = Adw.CallbackAnimationTarget.new(self._move)
        ms = 220 if animate and not reduced_motion() else 0
        self._animation = Adw.TimedAnimation.new(self, self._cursor, float(index), ms, target)
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._animation.play()
        hour = self.hours[index] if 0 <= index < len(self.hours) else None
        if hour is not None:
            self.update_property([Gtk.AccessibleProperty.VALUE_TEXT],
                                 [f"{'Now' if self.today and index == 0 else short_time(hour.at)}, {hour.temperature}, {hour.text}"])

    def _move(self, value: float) -> None:
        self._cursor = value
        self.queue_draw()

    def set_tv_focus(self, on: bool) -> None:
        self.focused = on
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        size = round(self.HEIGHT * self.stage.unit) if orientation == Gtk.Orientation.VERTICAL else 0
        return size, size, -1, -1

    def _layout(self, text: str, size: float, weight: int) -> Pango.Layout:
        layout = self.create_pango_layout(text)
        # Figtree is a variable font: the exact weight is its wght axis, the enum the nearest step.
        font = Pango.FontDescription.from_string(f"Figtree @wght={weight}")
        font.set_absolute_size(size * self.stage.unit * Pango.SCALE)
        font.set_weight(Pango.Weight(max(100, min(900, weight // 100 * 100))))
        layout.set_font_description(font)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_font_features_new("tnum=1"))
        layout.set_attributes(attrs)
        return layout

    def _text(self, snapshot, text, x, y, size, weight, colour, anchor) -> None:
        layout = self._layout(text, size, weight)
        width = layout.get_pixel_extents()[1].width
        left = x - (width / 2 if anchor == "centre" else width if anchor == "end" else 0)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(round(left), round(y)))
        snapshot.append_layout(layout, colour)
        snapshot.restore()

    def do_snapshot(self, snapshot) -> None:
        u = self.stage.unit
        w = self.get_width()
        if w <= 0:
            return
        ink, ink3 = _rgba(TV["materials"]["ink"]), _rgba(TV["materials"]["ink_3"])
        n = 24
        hours = self.hours
        top = (self.MARKS + self.TEMPS + self.GAP_A) * u
        sh = self.STRIP * u
        strip = Graphene.Rect().init(0, top, w, sh)
        radius = 18 * u
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(strip, radius)
        # the day's light
        snapshot.push_rounded_clip(rounded)
        if hours:
            pairs = [(i / n, _rgba(hours[min(i, len(hours) - 1)].sky[0])) for i in range(n + 1)]
            snapshot.append_linear_gradient(strip, Graphene.Point().init(0, top), Graphene.Point().init(w, top),
                                            _stops(pairs))
        else:
            snapshot.append_color(_rgba(TV["materials"]["well"]), strip)
        self._hatch(snapshot, strip, hours, u)
        # the well: rim, inner shade and the recess's lower glint
        shadow = _rgba("rgba(0,0,0,.28)")
        snapshot.append_inset_shadow(rounded, shadow, 0, 6 * u, 0, 14 * u)
        snapshot.append_inset_shadow(rounded, _rgba(TV["materials"]["well_shade"]), 0, 2 * u, 0, 4 * u)
        snapshot.append_border(rounded, [1.5 * u] * 4, [_rgba(TV["materials"]["well_rim"])] * 4)
        snapshot.pop()
        if self.focused:
            ring = Gsk.RoundedRect()
            ring.init_from_rect(Graphene.Rect().init(-10 * u, top - 10 * u, w + 20 * u, sh + 20 * u), radius + 10 * u)
            snapshot.append_border(ring, [4 * u] * 4, [_rgba(TV["materials"]["key_top"])] * 4)
        # the hour marker: the key
        if hours:
            x = (self._cursor + .5) / n * w
            cw = 10 * u
            marker = Graphene.Rect().init(x - cw / 2, top - 10 * u, cw, sh + 20 * u)
            key = Gsk.RoundedRect()
            key.init_from_rect(marker, 3 * u)
            snapshot.append_outset_shadow(key, _rgba(TV["materials"]["key_drop"]), 0, 3 * u, 0, 10 * u)
            snapshot.push_rounded_clip(key)
            snapshot.append_linear_gradient(marker, Graphene.Point().init(0, marker.get_y()),
                                            Graphene.Point().init(0, marker.get_y() + marker.get_height()),
                                            _stops(((0, _rgba(TV["materials"]["key_top"])),
                                                    (1, _rgba(TV["materials"]["key_bottom"])))))
            snapshot.pop()
            snapshot.append_border(key, [1.5 * u] * 4, [_rgba(TV["materials"]["key_rim"])] * 4)
        # temperatures every three hours, the hours under the track
        temps_y = self.MARKS * u
        hours_y = top + sh + self.GAP_B * u + 8 * u
        for i in range(0, n + 1, 3):
            if i >= len(hours) and not (i == n and len(hours) == n):
                continue
            hour = hours[min(i, len(hours) - 1)]
            anchor = "start" if i == 0 else "end" if i == n else "centre"
            self._text(snapshot, hour.temperature, i / n * w, temps_y, 22, 700, ink, anchor)
            if i < n:
                label = "Now" if self.today and i == 0 else band_hour(hour.at)
                self._text(snapshot, label, i / n * w, hours_y, 19, 600, ink3, "start" if i == 0 else "centre")
        # sunrise and sunset, on their own line above the temperatures
        if hours:
            begin = hours[0].at
            for at, _kind in self.sun:
                offset = (at - begin).total_seconds() / 3600
                if 0 <= offset < n:
                    self._text(snapshot, short_time(at), offset / n * w, 0, 16, 650, ink3, "centre")

    @staticmethod
    def _hatch(snapshot, strip, hours, u) -> None:
        """One continuous hatch, masked by the wet hours with half-hour fades at each edge."""
        wet = [h.wet for h in hours]
        if not any(wet):
            return
        n = 24
        black, clear = _rgba("#000000"), _rgba("rgba(0,0,0,0)")
        pairs = []
        half = 1 / n / 2
        for i, w in enumerate(wet):
            a, b = i / n, (i + 1) / n
            prev = wet[i - 1] if i else False
            nxt = wet[i + 1] if i + 1 < len(wet) else False
            if not w:
                pairs += [(a, clear), (b, clear)]
                continue
            pairs += [(a, black if prev else clear), (a if prev else a + half, black),
                      (b if nxt else b - half, black), (b, black if nxt else clear)]
        x, y, width = strip.get_x(), strip.get_y(), strip.get_width()
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_linear_gradient(strip, Graphene.Point().init(x, y), Graphene.Point().init(x + width, y),
                                        _stops(pairs))
        snapshot.pop()
        angle = math.radians(-55)
        direction = (math.sin(angle), -math.cos(angle))
        period = 11 * u
        line = _rgba("rgba(255,255,255,.32)")
        none = _rgba("rgba(255,255,255,0)")
        snapshot.append_repeating_linear_gradient(
            strip, Graphene.Point().init(x, y),
            Graphene.Point().init(x + direction[0] * period, y + direction[1] * period),
            _stops(((0, line), (3 / 11, line), (3 / 11, none), (1, none))))
        snapshot.pop()


class _Dot(Gtk.Widget):
    """A place's dot in the bar: its sky's colour with a hairline rim (14 units)."""

    __gtype_name__ = "PrairieWeatherTvDot"

    def __init__(self, stage: TvStage, colour: str) -> None:
        super().__init__(valign=Gtk.Align.CENTER, can_target=False)
        self.stage, self.colour = stage, _rgba(colour)

    def do_measure(self, orientation, for_size):
        size = round(14 * self.stage.unit)
        return size, size, -1, -1

    def do_snapshot(self, snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        rect = Gsk.RoundedRect()
        rect.init_from_rect(Graphene.Rect().init(0, 0, w, h), w / 2)
        snapshot.push_rounded_clip(rect)
        snapshot.append_color(self.colour, Graphene.Rect().init(0, 0, w, h))
        snapshot.pop()
        on_key = self.get_ancestor(TvTab) is not None and self.get_ancestor(TvTab).has_css_class("tv-key")
        rim = _rgba("rgba(0,0,0,.25)" if on_key else "rgba(255,255,255,.4)")
        snapshot.append_border(rect, [1.5 * self.stage.unit] * 4, [rim] * 4)


class _DayCard(TvLift):
    """A day card: bar material, the chosen day a chip, the focused one the key, lifted 1.06."""

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__(child, scale=TV["tile"]["focus_scale"])
        self.chosen = False

    def set_chosen(self, chosen: bool) -> None:
        self.chosen = chosen
        self._paint(self.has_css_class("tv-focus"))

    def set_tv_focus(self, on: bool) -> None:
        self._paint(on)

    def _paint(self, focused: bool) -> None:
        child = self.child
        for name, on in (("tv-key", focused), ("tv-chip", self.chosen and not focused),
                         ("tv-bar", not focused and not self.chosen)):
            (child.add_css_class if on else child.remove_css_class)(name)


# ── the presentation ──────────────────────────────────────────────────────

class WeatherTV(TvStage):
    """Weather's TV presentation over the window's places and snapshots."""

    __gtype_name__ = "PrairieWeatherTV"

    def __init__(self, window) -> None:
        super().__init__()
        add_tv_style(_style)
        self.window = window
        self.day = 0
        self.hour = 0
        self._model_key = None
        self._model: TvForecast | None = None
        self._sheet = None
        self._search_serial = 0
        self._shown_sky = None

        self.sky = TvSky(self)
        self.place(self.sky, fill=True)
        self.bar = TvTopBar()
        self.place(self.bar, **TvTopBar.PIN)
        self.hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.when = _label("", "wx-tv-when")
        self.temp_row = Gtk.Box(valign=Gtk.Align.START)
        self.temp = _label("", "wx-tv-temp tv-num")
        self.temp_row.append(self.temp)
        self.glyph_slot = Gtk.Box(valign=Gtk.Align.START)
        self.temp_row.append(self.glyph_slot)
        self.cond = _label("", "wx-tv-cond")
        self.cond.set_use_markup(True)
        self.say = _label("", "wx-tv-say", wrap=True)
        for part in (self.when, self.temp_row, self.cond, self.say):
            self.hero.append(part)
        self.place(self.hero, left=96, top=190, width=900)
        self.tiles = Gtk.Grid(column_homogeneous=True)
        self.place(self.tiles, right=96, top=190, width=452)
        self.band = TvBand(self)
        self.place(self.band, left=96, right=96, bottom=222, height=TvBand.HEIGHT)
        self.days = Gtk.Box(homogeneous=True)
        self.days.add_css_class("wx-tv-days")
        self.place(self.days, left=96, right=96, bottom=56)
        self.glance = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, opacity=0, can_target=False)
        self.glance.add_css_class("wx-tv-glance")
        self.place(self.glance, left=96, right=96, bottom=43)
        self.focus = TvFocus(self, on_back=lambda: False)
        self.focus.set_group_entry("places", lambda: self._tabs[self._open_index()] if self._tabs else None)
        self.focus.set_group_entry("days", lambda: self._cards[self.day] if self.day < len(self._cards) else None)
        self.focus.add(self.band, group="band", on_key=self._scrub)
        self.idle = TvIdle(self.focus, seconds=TV["idle"]["weather_seconds"],
                           on_idle=self._go_idle, on_wake=self._wake)
        self._tabs: list[TvTab] = []
        self._cards: list[_DayCard] = []
        self.connect("notify::unit", lambda *_a: self._on_unit())
        self.connect("notify::docked", lambda *_a: self._on_docked())
        self._unit_source = 0

    # ── data ──────────────────────────────────────────────────────────

    def _selected_place(self):
        return next((p for p in self.window.places if p.uid == self.window.selected), None)

    def _open_index(self) -> int:
        return next((i for i, p in enumerate(self.window.places) if p.uid == self.window.selected), 0)

    def model(self) -> TvForecast | None:
        place = self._selected_place()
        if place is None:
            return None
        snapshot = self.window.snapshots.get(place.uid)
        fixture = self.window.fixture
        key = (place.uid, id(snapshot), self.window.units, getattr(fixture, "minute", None))
        if key != self._model_key:
            self._model_key = key
            if fixture:
                self._model = fixture.tv(place)
            elif snapshot is not None and snapshot.forecast is not None:
                self._model = tv_from_forecast(snapshot.forecast, snapshot, self.window.units)
            else:
                self._model = None
        return self._model

    # ── drawing ───────────────────────────────────────────────────────

    def render(self) -> None:
        """Rebuild from the window's places and snapshots (a refresh, a new place)."""
        self._model_key = None
        self._draw_places()
        self._draw_days()
        self._draw_hour(animate=False)
        if self.idle.idle:
            self._draw_glance()
        if self.focus.focused is None and not self.focus.in_scope:
            self.focus.focus(self.band)

    def _on_unit(self) -> None:
        # Pixel-sized pieces (glyphs, grid gaps) follow the unit once the size settles.
        if not self._unit_source:
            self._unit_source = GLib.idle_add(self._unit_settled)

    def _unit_settled(self) -> bool:
        self._unit_source = 0
        self._draw_days()
        self._draw_hour(animate=False)
        if self.idle.idle:
            self._draw_glance()
        return False

    def _draw_places(self) -> None:
        focused = self.focus.focused
        refocus = self.focus.group_of(focused) == "places"
        focused_index = self._tabs.index(focused) if refocus and focused in self._tabs else -1
        self.focus.clear("places")
        tabs = []
        for index, place in enumerate(self.window.places):
            if index == 0:
                tab = TvTab(place.name, icon="navigation", accessible=f"{place.name}, your location")
            else:
                tab = TvTab(place.name, lead=_Dot(self, self._dot_colour(place)))
            self.focus.add(tab, group="places", on_focus=lambda i=index: self._go_place(i),
                           on_activate=lambda i=index: self._place_ok(i))
            tabs.append(tab)
        add = TvTab("Add a place" if len(self.window.places) <= 1 else "", icon="plus", accessible="Add a place")
        self.focus.add(add, group="places", on_activate=self._open_add)
        tabs.append(add)
        self._tabs = tabs
        self.bar.set_tabs(tabs)
        self.bar.set_open(self._open_index())
        if refocus:
            self.focus.focus(tabs[min(max(focused_index, 0), len(tabs) - 1)])

    def _dot_colour(self, place) -> str:
        """A place's sky right now, as the dot beside its name."""
        snapshot = self.window.snapshots.get(place.uid)
        if snapshot is None:
            return "#7d8ea3"
        sky = snapshot.sky
        light = {"night": LIGHT_NIGHT, "dusk": LIGHT_GOLDEN, "rain": "r"}.get(sky, LIGHT_DAY)
        condition = {"cloud": "cloud", "rain": "rain"}.get(sky, "part" if snapshot.glyph in ("partly", "ncloud") else "clear")
        return sky_colours(light, condition)[0]

    def _draw_days(self) -> None:
        model = self.model()
        refocus = self.focus.group_of(self.focus.focused) == "days"
        self.focus.clear("days")
        while child := self.days.get_first_child():
            self.days.remove(child)
        self._cards = []
        if model is None:
            return
        u = self.unit
        for index, day in enumerate(model.days):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            card.add_css_class("wx-tv-day")
            name = _label(day.label, "wx-tv-day-name", xalign=.5)
            mark = glyph(day.condition, False, 40 * u)
            mark.set_halign(Gtk.Align.CENTER)
            mark.set_vexpand(True)
            temps = Gtk.Box(halign=Gtk.Align.CENTER)
            temps.append(_label(day.high, "wx-tv-day-high tv-num"))
            temps.append(_label(day.low, "wx-tv-day-low tv-num"))
            for part in (name, mark, temps):
                card.append(part)
            lift = _DayCard(card)
            lift.update_property([Gtk.AccessibleProperty.LABEL], [f"{day.name}, {day.high}, low {day.low}"])
            lift.set_chosen(index == self.day)
            self.focus.add(lift, group="days", on_focus=lambda i=index: self._go_day(i),
                           on_activate=lambda: self.focus.focus(self.band))
            self.days.append(lift)
            self._cards.append(lift)
        if refocus and self.day < len(self._cards):
            self.focus.focus(self._cards[self.day])

    def _draw_hour(self, *, animate: bool = True) -> None:
        model = self.model()
        place = self._selected_place()
        if model is None or not model.days:
            self.when.set_label(f"{place.name} · Loading weather…" if place else "")
            for part in (self.temp, self.cond, self.say):
                part.set_label("")
            return
        day = model.day(self.day)
        hours = day.hours
        self.hour = max(0, min(self.hour, len(hours) - 1)) if hours else 0
        self.band.set_day(hours, day.sun, self.day == 0)
        self.band.set_hour(self.hour, animate=animate)
        if not hours:
            return
        hour = hours[self.hour]
        if self.day == 0 and self.hour == 0:
            when = "Now"
        else:
            when = (f"{day.name}, " if self.day else "") + short_time(hour.at)
        self.when.set_label(f"{model.name} · {when}")
        self.temp.set_label(hour.temperature)
        u = self.unit
        while child := self.glyph_slot.get_first_child():
            self.glyph_slot.remove(child)
        mark = glyph(hour.condition, hour.night, 118 * u)
        mark.set_margin_top(round(26 * u))
        mark.set_margin_start(round(20 * u))
        self.glyph_slot.append(mark)
        high_low = " ".join(p for p in (f"High {day.high}" if day.high else "", f"Low {day.low}" if day.low else "") if p)
        tail = f" <span weight='500' fgalpha='74%'>· {GLib.markup_escape_text(high_low)}</span>" \
            if high_low else ""
        self.cond.set_markup(GLib.markup_escape_text(hour.text) + tail)
        self.say.set_label(day.sentence)
        self.say.set_max_width_chars(-1)
        self.say.set_size_request(round(760 * u), -1)
        self._draw_tiles(model.tiles(self.day, self.hour))
        sky = (hour.light, hour.condition)
        if sky != self._shown_sky:
            self.sky.set_hour(*sky, animate=animate and self._shown_sky is not None)
            self._shown_sky = sky

    def _draw_tiles(self, tiles) -> None:
        while child := self.tiles.get_first_child():
            self.tiles.remove(child)
        u = self.unit
        self.tiles.set_row_spacing(round(12 * u))
        self.tiles.set_column_spacing(round(12 * u))
        for index, tile in enumerate(tiles):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.FILL)
            box.add_css_class("wx-tv-tile")
            box.add_css_class("tv-bar")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, vexpand=True,
                            spacing=round(8 * u))
            title = Gtk.Box()
            title.add_css_class("wx-tv-tile-title")
            row = Gtk.Box()
            row.append(icons.image(tile.icon))
            row.append(_label(tile.title.upper()))
            title.append(row)
            inner.append(title)
            value = Gtk.Box(valign=Gtk.Align.BASELINE_FILL)
            value.append(_label(tile.value, "wx-tv-tile-value tv-num"))
            if tile.unit:
                unit = _label(tile.unit, "wx-tv-tile-unit")
                unit.set_valign(Gtk.Align.BASELINE_FILL)
                value.append(unit)
            inner.append(value)
            box.append(inner)
            self.tiles.attach(box, index % 2, index // 2, 1, 1)

    # ── the remote ────────────────────────────────────────────────────

    def _scrub(self, direction: str) -> bool:
        if direction not in (LEFT, RIGHT):
            return False
        model = self.model()
        hours = model.day(self.day).hours if model and model.days else ()
        if not hours:
            return True
        step = -1 if direction == LEFT else 1
        target = max(0, min(len(hours) - 1, self.hour + step))
        if target != self.hour:
            self.hour = target
            self._draw_hour()
        return True

    def _go_place(self, index: int) -> None:
        places = self.window.places
        if not 0 <= index < len(places) or places[index].uid == self.window.selected:
            return
        self.window.selected = places[index].uid
        self.day, self.hour = 0, 0
        self.bar.set_open(index)
        self._model_key = None
        self._draw_days()
        self._draw_hour()

    def _place_ok(self, index: int) -> None:
        if index == self._open_index() and index > 0:
            self._open_remove(index)
        else:
            self._go_place(index)

    def _go_day(self, index: int) -> None:
        if index == self.day:
            return
        self.day = index
        self.hour = 0 if index == 0 else 12
        for i, card in enumerate(self._cards):
            card.set_chosen(i == index)
        self._draw_hour()

    # ── sheets: Remove, Add a place ───────────────────────────────────

    def _scrim(self) -> Gtk.Widget:
        scrim = Gtk.Box()
        scrim.add_css_class("tv-scrim")
        return scrim

    def _show_sheet(self, panel: Gtk.Widget, items, *, first=None, top: bool = False) -> None:
        self._close_sheet(restore=False)
        scrim = self._scrim()
        self.place(scrim, fill=True)
        if top:
            self.place(panel, top=56, center_x=True)
        else:
            self.place(panel, center_x=True)
        self._sheet = (scrim, panel)
        for part in (self.hero, self.tiles, self.band, self.days):
            part.set_opacity(.55)
        self._release_idle = self.idle.hold()
        self.focus.push_scope(items, on_back=self._sheet_back, first=first)

    def _sheet_back(self) -> bool:
        self._close_sheet(restore=False)
        return True

    def _close_sheet(self, *, restore: bool = True) -> None:
        if self._sheet is None:
            return
        for widget in self._sheet:
            self.remove(widget)
        self._sheet = None
        for part in (self.hero, self.tiles, self.band, self.days):
            part.set_opacity(1)
        release = getattr(self, "_release_idle", None)
        if release:
            release()
            self._release_idle = None
        if restore and self.focus.in_scope:
            self.focus.pop_scope()

    def _panel(self, title: str, width: float) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("tv-sheet")
        panel.add_css_class("wx-tv-panel")
        panel.set_size_request(round(width * self.unit), -1)
        panel.append(_label(title, "tv-sheet-title"))
        return panel

    def _open_remove(self, index: int) -> None:
        place = self.window.places[index]
        panel = self._panel(place.name, TV["sheet"]["small_width"])
        actions = Gtk.Box(homogeneous=True)
        actions.add_css_class("wx-tv-actions")
        remove = TvButton("Remove", danger=True, hexpand=True)
        cancel = TvButton("Cancel", hexpand=True)
        for button in (remove, cancel):
            button.add_css_class("tv-action")
            actions.append(button)
        panel.append(actions)
        self.focus.add(remove, group="sheet", on_activate=lambda: self._remove(place))
        self.focus.add(cancel, group="sheet", on_activate=lambda: self._close_sheet())
        self._show_sheet(panel, [remove, cancel], first=cancel)

    def _remove(self, place) -> None:
        """Remove an added place; the place before it opens and takes the focus."""
        places = list(self.window.places)
        index = next((i for i, p in enumerate(places) if p.uid == place.uid), 0)
        self._close_sheet()
        self.window.selected = places[max(0, index - 1)].uid
        self.day, self.hour = 0, 0
        self.window._remove(place)
        if self._tabs:
            self.focus.focus(self._tabs[max(0, index - 1)])

    def _open_add(self) -> None:
        panel = self._panel("Add a place", TV["sheet"]["width"])
        field = TvField(placeholder="Search for a city or ZIP", action="Search")
        field.set_value("")
        panel.append(field)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._list.add_css_class("wx-tv-sheet-list")
        panel.append(self._list)
        self._field = field
        self._results = ()
        self.focus.add(field.text, group="sheet", editable=True, on_activate=self._field_ok)
        field.text.connect("changed", lambda *_a: self._search_later())
        self._show_sheet(panel, [field.text], first=field.text, top=True)
        self._fill_results(())

    def _field_ok(self) -> None:
        rows = self._rows()
        if rows:
            self.focus.focus(rows[0])

    def _rows(self) -> list[Gtk.Widget]:
        return [row for row in getattr(self, "_row_widgets", [])]

    def _search_later(self) -> None:
        self._search_serial += 1
        serial = self._search_serial
        query = self._field.get_value()
        if not query.strip():
            self._fill_results(())
            return
        GLib.timeout_add(150, self._search_now, serial, query)

    def _search_now(self, serial: int, query: str) -> bool:
        """Look places up off the main thread (the location database loads lazily)."""
        if serial != self._search_serial:
            return False
        provider = self.window.search_provider

        def run() -> None:
            try:
                results = tuple(provider.search(query))
            except Exception:  # noqa: BLE001 - a failed lookup is no results; the field stays usable
                results = ()
            GLib.idle_add(self._searched, serial, results)
        threading.Thread(target=run, daemon=True, name="weather-tv-search").start()
        return False

    def _searched(self, serial: int, results) -> bool:
        if serial == self._search_serial and self._sheet is not None:
            saved = {(round(p.latitude, 4), round(p.longitude, 4)) for p in self.window.places}
            fresh = [r for r in results if not hasattr(r, "latitude")
                     or (round(r.latitude, 4), round(r.longitude, 4)) not in saved]
            self._fill_results(tuple(fresh))
        return False

    def _fill_results(self, results) -> None:
        # The sheet sits above the system keyboard while it is up (the mockup's 560-unit
        # panel): three rows then, five once the keyboard is away.
        self._all_results = tuple(results)
        results = self._all_results[:3 if self.docked == "keyboard" else 5]
        rows = getattr(self, "_row_widgets", [])
        held = rows.index(self.focus.focused) if self.focus.focused in rows else -1
        while child := self._list.get_first_child():
            self._list.remove(child)
        for row in getattr(self, "_row_widgets", []):
            self.focus.remove(row)
        self._row_widgets = []
        query = self._field.get_value().strip()
        if not results:
            if query:
                self._list.append(_label(f"No places match “{query}”", "wx-tv-none"))
            self.focus.set_scope_items([self._field.text])
            if held >= 0:
                self.focus.focus(self._field.text)
            return
        for result in results:
            name, sub = self._result_text(result)
            row = Gtk.Box()
            row.add_css_class("wx-tv-row")
            inner = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=True)
            pin = icons.image("map-pin")
            inner.append(pin)
            inner.append(_label(name, "wx-tv-row-name"))
            if sub:
                inner.append(_label(sub, "wx-tv-row-sub"))
            row.append(inner)
            self.focus.add(row, group="sheet", on_activate=lambda r=result: self._add(r))
            self._list.append(row)
            self._row_widgets.append(row)
        self.focus.set_scope_items([self._field.text, *self._row_widgets])
        if held >= 0:
            # Results that arrive while a row has the focus keep it on the same line.
            self.focus.focus(self._row_widgets[min(held, len(self._row_widgets) - 1)])

    def _on_docked(self) -> None:
        if self._sheet is not None and hasattr(self, "_all_results"):
            self._fill_results(self._all_results)

    @staticmethod
    def _result_text(result) -> tuple[str, str]:
        if hasattr(result, "subtitle") and not hasattr(result, "latitude"):
            return result.name, result.subtitle
        region = ", ".join(p for p in (getattr(result, "region", ""), getattr(result, "country", "")) if p)
        return result.name, region

    def _add(self, result) -> None:
        self._close_sheet()
        self.window._add_place(result)

    # ── toasts ────────────────────────────────────────────────────────

    def toast(self, title: str, *, lead: str | None = None) -> None:
        TvToast.show(self, title, lead=lead)

    # ── Glance ────────────────────────────────────────────────────────

    def _draw_glance(self) -> None:
        while child := self.glance.get_first_child():
            self.glance.remove(child)
        model = self.model()
        if model is None or not model.days or not model.days[0].hours:
            return
        now = model.days[0].hours[0]
        u = self.unit
        if self._open_index() != 0:
            self.glance.append(_label(model.name, "wx-tv-glance-where"))
        big = Gtk.Box(spacing=round(30 * u))
        big.append(_label(now.temperature, "wx-tv-glance-temp tv-num"))
        mark = glyph(now.condition, now.night, 170 * u)
        mark.set_valign(Gtk.Align.CENTER)
        big.append(mark)
        self.glance.append(big)
        say = _label(model.days[0].sentence, "wx-tv-glance-say", wrap=True)
        say.set_size_request(-1, -1)
        say.set_max_width_chars(60)
        self.glance.append(say)

    def _go_idle(self) -> None:
        self._close_sheet()
        self.day, self.hour = 0, 0
        self._draw_days()
        self._draw_hour(animate=False)
        self._draw_glance()
        fade = TV["motion"]["idle_fade_ms"]
        for part in (self.bar.track, self.hero, self.tiles, self.band, self.days):
            tv_fade(part, 0.0, fade)
        tv_fade(self.glance, 1.0, TV["motion"]["idle_in_ms"])

    def _wake(self) -> None:
        fade = TV["motion"]["idle_fade_ms"]
        tv_fade(self.glance, 0.0, fade)
        for part in (self.bar.track, self.hero, self.tiles, self.band, self.days):
            tv_fade(part, 1.0, fade)
        self.focus.focus(self.band)
