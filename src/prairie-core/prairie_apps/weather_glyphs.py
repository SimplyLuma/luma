# SPDX-License-Identifier: Apache-2.0

"""The weather glyphs, and the one tinted surface in the application.

Eight shapes — sun, sun behind cloud, cloud, rain, snow, fog, thunder, moon —
drawn once, on a 24-unit grid, in whichever of two palettes the *surface* asks
for. Same construction, same weights, two palettes: that is what makes the set
read as one family whether it is sitting on a navy tile or on grey chrome.

They are drawn rather than shipped as icons because they are two-tone (a warm
sun against a neutral cloud), which a symbolic icon cannot be, and because the
palette has to change with the surface, which an icon name cannot do.

Nothing here animates. Not the sun, not the rain, not ever.
"""

from __future__ import annotations

import math

import cairo

from .weather_model import (
    GLYPH_CLOUD, GLYPH_FOG, GLYPH_MOON, GLYPH_PARTLY, GLYPH_RAIN, GLYPH_SNOW,
    GLYPH_SUN, GLYPH_THUNDER, GRADIENT_ANGLE_DEGREES, SKY_GRADIENTS,
)

GRID = 24.0


def rgb(value: str) -> tuple[float, float, float]:
    text = value.lstrip("#")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _set(context: cairo.Context, colour: str, alpha: float = 1.0) -> None:
    red, green, blue = rgb(colour)
    context.set_source_rgba(red, green, blue, alpha)


# ── The two primitives everything is built from ──────────────────────────

def _sun(context: cairo.Context, palette: dict[str, str], *, radius: float = 5.0,
         centre: tuple[float, float] = (12.0, 12.0)) -> None:
    """A disc with eight rays. The rays are drawn at the same weight at every
    size because the whole glyph is scaled, never restroked."""
    x, y = centre
    _set(context, palette["sun"])
    context.arc(x, y, radius, 0, math.tau)
    context.fill()
    context.set_line_width(1.7)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    inner, outer = radius * 1.88, radius * 2.36
    for step in range(8):
        angle = step * math.pi / 4.0
        cosine, sine = math.cos(angle), math.sin(angle)
        context.move_to(x + cosine * inner, y + sine * inner)
        context.line_to(x + cosine * outer, y + sine * outer)
    context.stroke()


# The cloud is three lobes on a flat base, which is what the design's own path
# draws: a 4.2 left lobe, a 5.4 crown and a 3.4 right lobe, closed along 18.6.
_CLOUD_LOBES = ((7.30, 14.40, 4.20), (12.76, 9.52, 5.40), (17.65, 15.15, 3.45))
_CLOUD_BASE = (7.20, 14.40, 10.40, 4.20)


def _cloud_path(context: cairo.Context, *, squash: float = 1.0) -> None:
    for x, y, radius in _CLOUD_LOBES:
        context.new_sub_path()
        context.arc(x, 18.6 - (18.6 - y) * squash, radius * squash if squash < 1 else radius,
                    0, math.tau)
    x, y, width, height = _CLOUD_BASE
    context.new_sub_path()
    context.rectangle(x, 18.6 - (18.6 - y) * squash, width, height * squash)


def _cloud(context: cairo.Context, colour: str, *, squash: float = 1.0) -> None:
    _set(context, colour)
    context.set_fill_rule(cairo.FILL_RULE_WINDING)
    _cloud_path(context, squash=squash)
    context.fill()


def _strokes(context: cairo.Context, colour: str, width: float = 1.9) -> None:
    _set(context, colour)
    context.set_line_width(width)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    for x in (9.0, 13.0, 17.0):
        context.move_to(x, 19.5)
        context.line_to(x - 1.0, 22.1)
    context.stroke()


def _crosses(context: cairo.Context, colour: str) -> None:
    """Snow: three small crosses where rain has its strokes."""
    _set(context, colour)
    context.set_line_width(1.5)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    for x in (8.6, 12.6, 16.6):
        for dx, dy in ((1.0, 1.0), (1.0, -1.0)):
            context.move_to(x - dx, 20.8 - dy)
            context.line_to(x + dx, 20.8 + dy)
    context.stroke()


def _bars(context: cairo.Context, colour: str) -> None:
    """Fog: three horizontal bars under a flattened cloud."""
    _set(context, colour)
    context.set_line_width(1.7)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    for index, (start, end) in enumerate(((7.4, 17.2), (8.6, 16.0), (7.4, 17.2))):
        y = 18.4 + index * 2.0
        context.move_to(start, y)
        context.line_to(end, y)
    context.stroke()


def _bolt(context: cairo.Context, colour: str) -> None:
    _set(context, colour)
    context.move_to(13.4, 18.4)
    context.line_to(10.2, 23.2)
    context.line_to(12.4, 23.2)
    context.line_to(11.0, 26.0)
    context.line_to(15.0, 20.6)
    context.line_to(12.6, 20.6)
    context.line_to(14.4, 18.4)
    context.close_path()
    context.fill()


def _crescent(context: cairo.Context, colour: str) -> None:
    """A crescent as one path: the outer disc's arc, then the cut disc's arc.

    Drawing it as a disc with another disc painted over in the background
    colour is what makes a moon show a seam on a gradient, so the shape is
    computed instead.
    """
    ox, oy, outer = 11.6, 11.8, 7.0
    kx, ky, cut = 16.1, 7.3, 6.6
    dx, dy = kx - ox, ky - oy
    distance = math.hypot(dx, dy)
    a = (distance * distance + outer * outer - cut * cut) / (2 * distance)
    height = math.sqrt(max(0.0, outer * outer - a * a))
    mx, my = ox + a * dx / distance, oy + a * dy / distance
    px, py = -dy / distance, dx / distance
    first = (mx + height * px, my + height * py)
    second = (mx - height * px, my - height * py)
    start_outer = math.atan2(first[1] - oy, first[0] - ox)
    end_outer = math.atan2(second[1] - oy, second[0] - ox)
    start_cut = math.atan2(second[1] - ky, second[0] - kx)
    end_cut = math.atan2(first[1] - ky, first[0] - kx)
    _set(context, colour)
    context.new_path()
    context.arc(ox, oy, outer, start_outer, end_outer)
    context.arc_negative(kx, ky, cut, start_cut, end_cut)
    context.close_path()
    context.fill()


# ── The eight glyphs ─────────────────────────────────────────────────────

def draw_glyph(context: cairo.Context, kind: str, size: float,
               palette: dict[str, str]) -> None:
    """Draw one glyph, `size` points square, at the origin.

    `palette` comes from `palette_for_surface()`. Choosing it from the
    condition instead of the surface is the bug that puts a white cloud on
    grey chrome and makes it disappear.
    """
    context.save()
    context.scale(size / GRID, size / GRID)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind == GLYPH_SUN:
        _sun(context, palette)
    elif kind == GLYPH_MOON:
        _crescent(context, palette["moon"])
    elif kind == GLYPH_PARTLY:
        context.save()
        context.translate(2.0, -2.0)
        context.scale(0.62, 0.62)
        _sun(context, palette)
        context.restore()
        _cloud(context, palette["cloud"])
    elif kind == GLYPH_CLOUD:
        _cloud(context, palette["cloud_alt"])
    elif kind == GLYPH_RAIN:
        _cloud(context, palette["cloud"])
        _strokes(context, palette["rain"])
    elif kind == GLYPH_SNOW:
        _cloud(context, palette["cloud"])
        _crosses(context, palette["rain"])
    elif kind == GLYPH_FOG:
        _cloud(context, palette["cloud_alt"], squash=0.74)
        _bars(context, palette["cloud"] if palette["cloud"] != "#ffffff"
              else palette["cloud_alt"])
    elif kind == GLYPH_THUNDER:
        _cloud(context, palette["cloud"])
        _bolt(context, palette["bolt"])
    else:
        _cloud(context, palette["cloud_alt"])
    context.restore()


# ── The one tinted surface ───────────────────────────────────────────────

def rounded_rectangle(context: cairo.Context, x: float, y: float,
                      width: float, height: float, radius: float) -> None:
    radius = min(radius, width / 2.0, height / 2.0)
    context.new_sub_path()
    context.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    context.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    context.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    context.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    context.close_path()


def sky_gradient(sky: str, width: float, height: float) -> cairo.LinearGradient:
    """The condition gradient, at the design's 165 degrees.

    CSS measures the angle clockwise from "up", and the gradient line is as
    long as the box's projection onto it, which is what keeps the last stop
    landing exactly in the corner rather than somewhere inside the tile.
    """
    stops = SKY_GRADIENTS.get(sky) or SKY_GRADIENTS["cloudy"]
    angle = math.radians(GRADIENT_ANGLE_DEGREES)
    dx, dy = math.sin(angle), -math.cos(angle)
    length = abs(width * dx) + abs(height * dy)
    cx, cy = width / 2.0, height / 2.0
    gradient = cairo.LinearGradient(cx - dx * length / 2.0, cy - dy * length / 2.0,
                                    cx + dx * length / 2.0, cy + dy * length / 2.0)
    for offset, colour in stops:
        red, green, blue = rgb(colour)
        gradient.add_color_stop_rgb(offset, red, green, blue)
    return gradient


def draw_condition_tile(context: cairo.Context, width: float, height: float,
                        radius: float, sky: str, glyph: str, glyph_size: float,
                        palette: dict[str, str]) -> None:
    """The 88x88 tile: the only place in this application the sky has a colour.

    There is no full-bleed version of this. The pane behind it is `--body`, the
    cards on it are white, and that is the whole point of the app.
    """
    rounded_rectangle(context, 0, 0, width, height, radius)
    context.set_source(sky_gradient(sky, width, height))
    context.fill()
    context.save()
    context.translate((width - glyph_size) / 2.0, (height - glyph_size) / 2.0)
    draw_glyph(context, glyph, glyph_size, palette)
    context.restore()


def draw_sun_arc(context: cairo.Context, width: float, height: float,
                 setting: bool, ink: tuple[float, float, float]) -> None:
    """The sunrise/sunset glyph: one arc, one horizon, one disc.

    The disc sits left of centre for sunrise and right of centre for sunset.
    It is the one mirrored pair in the app that earns its keep — you can tell
    the two rows apart without reading them.
    """
    scale_x, scale_y = width / 16.0, height / 11.0
    context.save()
    context.scale(scale_x, scale_y)
    context.set_source_rgba(ink[0], ink[1], ink[2], 0.32)
    context.set_line_width(1.4)
    context.new_path()
    context.arc(8.0, 9.5, 6.0, math.pi, math.tau)
    context.stroke()
    context.set_line_width(1.2)
    context.move_to(0.5, 9.5)
    context.line_to(15.5, 9.5)
    context.stroke()
    red, green, blue = rgb("#e8a33d")
    context.set_source_rgb(red, green, blue)
    context.arc(12.2 if setting else 3.8, 6.6, 2.1, 0, math.tau)
    context.fill()
    context.restore()
