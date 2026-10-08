# SPDX-License-Identifier: Apache-2.0
"""Weather's small shared pieces: type roles, labels and glyphs, for the desktop and phone pages."""
from __future__ import annotations

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk  # noqa: E402

from luma_appkit import ScrollView, TypeLabel, WeatherGlyph, icons  # noqa: E402
from .weather_charts import ForecastDay, ForecastGraphic, WeatherHours, WeatherPrecipitationSpace  # noqa: E402
from .weather_data import weather_icon  # noqa: E402

def label(text, role='body', *, center=False, name=None, wrap=False, opacity=None):
    widget = TypeLabel(text, role=role, wrap=wrap)
    if wrap:
        widget.label.set_hexpand(True)
    if opacity is not None:
        widget.set_opacity(opacity)
    if center:
        widget.set_halign(Gtk.Align.CENTER)
    if name:
        widget.set_name(name)
    return widget


def glyph(condition, night=False, size=26):
    kind = weather_icon(condition, night=night)
    if kind in {"sun", "moon", "cloud", "cloud-sun", "cloud-moon", "cloud-rain"}:
        return WeatherGlyph(kind, size=size)
    # Snow, fog and thunder retain the shared symbolic vocabulary until the
    # multicolor family has approved shapes for those live conditions.
    image = icons.image(kind, pixel_size=size)
    image.set_valign(Gtk.Align.CENTER)
    return image


def hour_strip(hours):
    """The sideways hours (desktop's hours card, the phone's Next hours): time, sky, rain, temperature."""
    hour_row = Gtk.Box(spacing=2)
    for hour in hours:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=52)
        column.set_margin_top(2)
        column.set_margin_bottom(2)
        column.append(label(hour.label, 'weather-hour-now' if hour.label == 'Now' else 'weather-hour', center=True, opacity=1 if hour.label == 'Now' else .7))
        condition = Gtk.Overlay()
        condition_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        condition_body.append(glyph(hour.condition, hour.night))
        precipitation = label(hour.precipitation, 'weather-precipitation', center=True)
        precipitation.set_size_request(-1, 12)
        precipitation.set_visible(bool(hour.precipitation))
        condition_body.append(WeatherPrecipitationSpace(precipitation))
        condition.set_child(condition_body)
        precipitation.set_valign(Gtk.Align.END)
        condition.add_overlay(precipitation)
        condition.set_measure_overlay(precipitation, True)
        column.append(condition)
        column.append(label(hour.temperature, 'weather-hour-temperature', center=True))
        hour_row.append(column)
    scroller = ScrollView(hour_row, horizontal=True, vexpand=False)
    scroller.set_propagate_natural_height(True)
    scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
    return WeatherHours(scroller)


def day_rows(days, snapshot):
    """The ten days, each with its range on the week's shared scale, appended to `days`."""
    scale = (min((d.low_value for d in snapshot.days), default=0), max((d.high_value for d in snapshot.days), default=1))
    for index, day in enumerate(snapshot.days):
        row = ForecastDay(first=index == 0)
        name = label(day.label, 'weather-day-name')
        name.set_size_request(50, -1)
        row.append(name)
        condition = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=40, valign=Gtk.Align.CENTER)
        condition.append(glyph(day.condition, size=22))
        if day.precipitation:
            condition.append(label(day.precipitation, 'weather-day-precipitation', center=True))
        row.append(condition)
        lo = label(day.low, 'weather-day-low')
        lo.set_size_request(34, -1)
        lo.set_hexpand(False)
        lo.label.set_hexpand(True)
        lo.label.set_xalign(1)
        row.append(lo)
        row.append(ForecastGraphic('range', low=day.low_value, high=day.high_value, scale=scale,
                                   current=snapshot.current_value if index == 0 else None))
        hi = label(day.high, 'weather-day')
        hi.set_size_request(34, -1)
        row.append(hi)
        days.append(row)
