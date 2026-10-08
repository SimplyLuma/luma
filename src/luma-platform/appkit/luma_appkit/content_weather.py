# SPDX-License-Identifier: Apache-2.0
"""Static, multicolor weather symbols shared by Weather, Clock and forecasts.

Geometry is the Luma v71 simulator's GL family (32-unit view box). Palette
comes from the generated weather_glyph_* roles, independent of app styling.
These are informational images, never controls; no timers or animation.
"""
from __future__ import annotations

from functools import lru_cache

from gi.repository import Graphene, Gsk, Gtk

from .media_style import colour

__all__ = ["WeatherGlyph", "WEATHER_CONDITIONS"]

# (SVG path, color role suffix, stroke width; zero means a filled shape).
_CLOUD = "M11.5 27h12a4.8 4.8 0 0 0 .7-9.5 7 7 0 0 0-13.3 1.7 3.9 3.9 0 0 0 .6 7.8Z"
_GEOMETRY = {
    "sun": (
        ("M22.5 16a6.5 6.5 0 1 0-13 0a6.5 6.5 0 1 0 13 0Z", "sun", 0),
        ("M16 3.5v3M16 25.5v3M3.5 16h3M25.5 16h3M7.2 7.2l2.1 2.1M22.7 22.7l2.1 2.1M7.2 24.8l2.1-2.1M22.7 9.3l2.1-2.1", "sun", 2.2),
    ),
    "moon": (("M22.5 20.6A9.5 9.5 0 0 1 12 6.2a10 10 0 1 0 13.8 13.1 9.4 9.4 0 0 1-3.3 1.3Z", "moon", 0),),
    "cloud": (("M9.5 25h13.2a5.3 5.3 0 0 0 .8-10.5 7.8 7.8 0 0 0-14.8 1.9A4.4 4.4 0 0 0 9.5 25Z", "cloud", 0),),
    "rain": (
        ("M9.5 20h13.2a5.3 5.3 0 0 0 .8-10.5A7.8 7.8 0 0 0 8.7 11.4 4.4 4.4 0 0 0 9.5 20Z", "cloud", 0),
        ("M11 23.5l-1.4 3.5M16 23.5l-1.4 3.5M21 23.5l-1.4 3.5", "rain", 2.2),
    ),
    "part": (
        ("M17.5 12a5.5 5.5 0 1 0-11 0a5.5 5.5 0 1 0 11 0Z", "sun", 0),
        ("M12 2.5v2M3.5 12h2M5.8 5.8l1.4 1.4M18.2 5.8l-1.4 1.4", "sun", 2),
        (_CLOUD, "cloud", 0),
    ),
    "ncloud": (
        ("M17 8.5a6.5 6.5 0 0 1-6.3 8.2A7 7 0 0 0 16.5 12Z", "moon", 0),
        (_CLOUD, "cloud", 0),
    ),
}
WEATHER_CONDITIONS = tuple(_GEOMETRY)
_ALIASES = {"clear": "sun", "partly": "part", "cloud-sun": "part", "cloud-moon": "ncloud", "cloud-rain": "rain"}
_LABELS = {"sun": "Clear", "moon": "Clear night", "cloud": "Cloudy", "rain": "Rain", "part": "Partly cloudy", "ncloud": "Partly cloudy night"}


@lru_cache(maxsize=None)
def _path(data: str) -> Gsk.Path:
    path = Gsk.Path.parse(data)
    if path is None:
        raise ValueError("Invalid weather glyph geometry")
    return path


class WeatherGlyph(Gtk.Widget):
    """A static forecast image, `size` square, in the current appearance palette.

    `condition` is one of WEATHER_CONDITIONS; clear/partly and cloud-sun,
    cloud-moon, cloud-rain aliases are accepted. `night=True` changes sun and
    partly-cloudy skies to their night counterparts. `label` can supply a
    localized accessible description; otherwise the condition names it.
    """

    __gtype_name__ = "LumaUIWeatherGlyph"

    def __init__(self, condition: str, *, night: bool = False, size: int = 26,
                 label: str | None = None) -> None:
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("weather glyph size must be a positive integer")
        super().__init__(accessible_role=Gtk.AccessibleRole.IMG,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         focusable=False, can_target=False)
        self.add_css_class("lumaui-weather-glyph")
        self.size = size
        self.set_condition(condition, night=night, label=label)

    def set_condition(self, condition: str, *, night: bool = False,
                      label: str | None = None) -> None:
        kind = _ALIASES.get(condition, condition)
        if kind not in _GEOMETRY:
            raise ValueError(f"unknown weather condition: {condition!r}")
        if night:
            kind = {"sun": "moon", "part": "ncloud"}.get(kind, kind)
        self.condition = kind
        self.description = label if label is not None else _LABELS[kind]
        self.update_property([Gtk.AccessibleProperty.LABEL], [self.description])
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        side = min(self.get_width(), self.get_height())
        if side <= 0:
            return
        snapshot.save()
        snapshot.translate(Graphene.Point().init((self.get_width()-side)/2, (self.get_height()-side)/2))
        snapshot.scale(side / 32, side / 32)
        for data, role, width in _GEOMETRY[self.condition]:
            ink = colour(self, "luma_weather_glyph_" + role)
            path = _path(data)
            if width:
                stroke = Gsk.Stroke.new(width)
                stroke.set_line_cap(Gsk.LineCap.ROUND)
                snapshot.append_stroke(path, stroke, ink)
            else:
                snapshot.append_fill(path, Gsk.FillRule.WINDING, ink)
        snapshot.restore()
