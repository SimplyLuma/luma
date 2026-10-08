# SPDX-License-Identifier: Apache-2.0
"""Weather on a phone (v71 `wxBodyPhone`, `wxBarPhone`): built around your one place.

Under 560 px the page is about the place you check: the temperature large
beside its sky, one plain sentence, What's next, any official warning before
any numbers, your remaining plans with the weather at each, the next hours and
six tiles. The bar picks how you look (Today, Hourly, 10 days, Radar); Alerts
and Places sit beside it, smaller, because you set them once.

These are Weather's own surfaces. The bar, its menus and the rows inside them
are LumaUI's: this module only says what goes in them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk  # noqa: E402

from luma_appkit import ContentLitCard, IconOnlyButton, icons  # noqa: E402
from .weather_charts import WeatherRadar  # noqa: E402
from .weather_data import Snapshot, weather_icon  # noqa: E402
from .weather_parts import day_rows, glyph, hour_strip, label  # noqa: E402

#: The bar's views (v71 `VW`): key, name, icon.
VIEWS = (('today', 'Today', 'sun'), ('hourly', 'Hourly', 'clock'),
         ('days', '10 days', 'calendar'), ('radar', 'Radar', 'radar'))

#: "Tell me about" (v71 `WXN` and its switches): key, name, what it means, on by default.
NOTIFY = (('severe', 'Severe weather', 'Storms, heat, wind and flood warnings', True),
          ('rain', 'Rain starting soon', '15 minutes before, where you are', True),
          ('aq', 'Unhealthy air', 'When the air quality passes 100', False),
          ('daily', 'Tomorrow, the night before', 'A one-line forecast at 8 PM', True))

#: v71's radar cells on its 360×420 frame (`.wxrg` ellipses): x, y, rx, ry, level.
SAMPLE_CELLS = ((120, 150, 90, 46, 1), (140, 150, 52, 26, 2), (150, 148, 20, 10, 3), (40, 260, 70, 30, 1))
RADAR_STEPS = 8   # quarter hours: now … +2 h


@dataclass
class PhoneState:
    """What the phone page is showing; it lives on the window, across places and redraws."""
    view: str = 'today'
    radar_step: int = 0
    radar_playing: bool = False
    notify: dict = field(default_factory=lambda: {key: on for key, _n, _s, on in NOTIFY})


def view_name(key: str) -> tuple[str, str]:
    """(name, icon) of a view."""
    return next((name, icon) for k, name, icon in VIEWS if k == key)


def _box(css=None, *, vertical=True, spacing=0, name=None, **kwargs):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                  spacing=spacing, **kwargs)
    if css:
        box.add_css_class(css)
    if name:
        box.set_name(name)
    return box


def _card(name, heading=None, icon=None, *, shape="card"):
    """A glass card over the sky (v71 `.wxcard2`) with its small uppercase heading."""
    card = ContentLitCard(glass=True, shape=shape)
    card.set_name(name)
    if heading:
        line = _box('wx-card-heading', vertical=False, spacing=6)
        line.append(icons.image(icon, pixel_size=14))
        line.append(label(heading.upper(), 'weather-phone-kicker'))
        card.append(line)
    return card


# ── Today ───────────────────────────────────────────────────────────────

def hero(snapshot: Snapshot, on_places: Callable[[Gtk.Widget], None]) -> Gtk.Widget:
    """The place (a small button that opens Places), the temperature beside its sky, conditions and range."""
    head = _box('wx-hero2', name='wx-phone-hero', hexpand=True)
    place = Gtk.Button(halign=Gtk.Align.CENTER, tooltip_text='Places')
    place.add_css_class('wx-place-button')
    place.set_name('wx-place-button')
    line = _box(vertical=False, spacing=6)
    line.append(icons.image('navigation' if snapshot.my_location else 'map-pin', pixel_size=15))
    line.append(label(snapshot.place.name, 'weather-phone-place'))
    disclosure = icons.image('chevron-down', pixel_size=13)
    disclosure.set_opacity(.6)
    line.append(disclosure)
    place.set_child(line)
    place.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.HAS_POPUP],
                          [f'{snapshot.place.name}, places', True])
    place.connect('clicked', lambda button: on_places(button))
    head.append(place)
    now = _box(vertical=False, spacing=8, halign=Gtk.Align.CENTER)
    now.append(label(snapshot.temperature, 'weather-phone-temperature', name='wx-phone-temperature'))
    now.append(glyph(snapshot.glyph, size=64))
    head.append(now)
    head.append(label(snapshot.condition_line, 'weather-phone-condition', center=True, name='wx-phone-condition'))
    return head


def next_strip(snapshot: Snapshot) -> Gtk.Widget | None:
    """What's next: up to three changes in the coming 12 hours, joined by short lines."""
    if not snapshot.changes:
        return None
    strip = _card('wx-next', shape='strip')
    row = _box(vertical=False, spacing=4)
    for index, change in enumerate(snapshot.changes):
        if index:
            joint = _box('wx-next-joint', valign=Gtk.Align.CENTER)
            joint.set_size_request(18, -1)
            row.append(joint)
        step = _box(spacing=3, hexpand=True, halign=Gtk.Align.FILL)
        step.append(label(change.after.upper(), 'weather-phone-next-kicker', center=True, opacity=.65))
        if change.icon:
            sun = icons.image(change.icon, pixel_size=22)
            sun.add_css_class('wx-sun-glyph')
            sun.set_size_request(26, 26)
            step.append(sun)
        else:
            step.append(glyph(change.condition, change.night, 26))
        step.append(label(change.label, 'weather-phone-strong', center=True))
        step.append(label(change.time, 'weather-phone-small', center=True, opacity=.7))
        row.append(step)
    strip.append(row)
    return strip


def alert_card(alert, *, small=False) -> Gtk.Widget:
    """Heads up: an official warning as an amber card, before any numbers (v71 `.wxalert`)."""
    card = _box('wx-alert', name='wx-alert-now' if small else 'wx-alert',
                accessible_role=Gtk.AccessibleRole.ALERT)
    if small:
        card.add_css_class('small')
    head = _box(vertical=False, spacing=8)
    warn = icons.image('triangle-alert', pixel_size=18)
    warn.add_css_class('wx-alert-glyph')
    head.append(warn)
    head.append(label(alert.title, 'weather-phone-alert-title'))
    when = label(alert.when, 'weather-phone-small', opacity=.75)
    when.set_hexpand(True)
    when.set_halign(Gtk.Align.END)
    head.append(when)
    card.append(head)
    if not small:
        what = label(alert.description, 'weather-phone-subtitle', wrap=True)
        what.add_css_class('wx-alert-text')
        card.append(what)
        card.append(label(alert.source, 'weather-phone-caption', opacity=.65))
    card.update_property([Gtk.AccessibleProperty.LABEL], [f'{alert.title}, {alert.when}'])
    return card


def your_day(snapshot: Snapshot) -> Gtk.Widget | None:
    """Your remaining plans today, with the weather at each ("Launch walkthrough, 2 PM, 64°")."""
    if not snapshot.plans:
        return None
    card = _card('wx-your-day', 'Your day', 'calendar')
    for index, plan in enumerate(snapshot.plans):
        row = _box('wx-plan', vertical=False, spacing=10)
        if index:
            row.add_css_class('joined')
        text = _box(hexpand=True, valign=Gtk.Align.CENTER)
        text.append(label(plan.title, 'weather-phone-row-title'))
        text.append(label(plan.time, 'weather-phone-small', opacity=.7))
        row.append(text)
        row.append(glyph(plan.condition, plan.night, 22))
        row.append(label(f'{plan.temperature}{" · bring a jacket" if plan.jacket else ""}', 'weather-phone-value'))
        card.append(row)
    return card


def next_hours(snapshot: Snapshot) -> Gtk.Widget:
    card = _card('wx-next-hours', 'Next hours', 'clock')
    card.append(hour_strip(snapshot.hours[:13]))
    return card


def tiles(snapshot: Snapshot) -> Gtk.Widget | None:
    """Rain, Wind, UV, Air, Sunset or Sunrise, Humidity: three across."""
    if not snapshot.tiles:
        return None
    grid = Gtk.Grid(column_homogeneous=True, column_spacing=8, row_spacing=8)
    grid.set_name('wx-tiles')
    for index, tile in enumerate(snapshot.tiles):
        cell = ContentLitCard(glass=True, shape="tile")
        cell.set_name(f'wx-tile-{tile.key}')
        cell.add_css_class('wx-tile')
        cell.append(_left(icons.image(tile.icon, pixel_size=16)))
        cell.append(label(tile.title, 'weather-phone-caption', opacity=.7))
        cell.append(label(tile.value, 'weather-phone-tile'))
        cell.append(label(tile.description, 'weather-phone-tile-description', wrap=True, opacity=.7))
        grid.attach(cell, index % 3, index // 3, 1, 1)
    return grid


def _left(widget):
    widget.set_halign(Gtk.Align.START)
    return widget


def today(snapshot: Snapshot, on_places) -> list[Gtk.Widget]:
    say = label(snapshot.phone_sentence, 'weather-phone-sentence', wrap=True, name='wx-phone-say')
    parts = [hero(snapshot, on_places), say, next_strip(snapshot)]
    parts += [alert_card(alert) for alert in snapshot.alerts]
    parts += [your_day(snapshot), next_hours(snapshot), tiles(snapshot)]
    return [part for part in parts if part is not None]


# ── Hourly, 10 days, Radar ─────────────────────────────────────────────

def _page_head(title, subtitle):
    head = _box('wx-page-head', name='wx-page-head')
    head.append(label(title, 'weather-phone-title'))
    subtitle_label = label(subtitle, 'weather-phone-subtitle', opacity=.7)
    subtitle_label.set_margin_top(2)
    head.append(subtitle_label)
    return head


def hourly(snapshot: Snapshot) -> list[Gtk.Widget]:
    """24 rows: time, sky, conditions, rain chance, temperature."""
    rows = ContentLitCard(glass=True, shape="list")
    rows.set_name('wx-hourly')
    for index, hour in enumerate(snapshot.hours[:24]):
        row = _box(vertical=False, spacing=8)
        time = label(hour.time or hour.label, 'weather-phone-hour-label')
        time.set_size_request(64, -1)
        row.append(time)
        sky = glyph(hour.condition, hour.night, 24)
        sky.set_size_request(28, -1)
        row.append(sky)
        words = label(hour.description, 'weather-phone-hour-condition', opacity=.8)
        words.set_hexpand(True)
        row.append(words)
        if hour.precipitation:
            rain = _box('wx-hour-rain', vertical=False, spacing=3, valign=Gtk.Align.CENTER)
            rain.append(icons.image('umbrella', pixel_size=13))
            rain.append(label(hour.precipitation, 'weather-phone-hour-rain'))
            row.append(rain)
        temperature = label(hour.temperature, 'weather-phone-hour-temperature')
        temperature.set_size_request(44, -1)
        temperature.label.set_xalign(1)
        temperature.label.set_hexpand(True)
        row.append(temperature)
        rows.append_row(row, current=index == 0)
    return [_page_head('Hourly', f'{snapshot.place.name} · next 24 hours'), rows]


def ten_days(snapshot: Snapshot) -> list[Gtk.Widget]:
    card = _card('wx-days-phone')
    day_rows(card, snapshot)
    return [_page_head('10 days', snapshot.place.name), card]


def radar(snapshot: Snapshot, state: PhoneState, *, sample: bool, on_play, on_step) -> list[Gtk.Widget]:
    """Rain and storms for the next two hours, with a legend, a time scrubber and Play.

    Only the fixture has radar cells: the live backend has no radar source yet,
    so a real place shows its frame with nothing in it rather than invented rain.
    """
    frame = Gtk.Overlay()
    frame.set_name('wx-radar')
    frame.add_css_class('wx-radar')
    frame.set_overflow(Gtk.Overflow.HIDDEN)
    canvas = WeatherRadar(snapshot.place.name, cells=SAMPLE_CELLS if sample else (), step=state.radar_step)
    frame.set_child(canvas)
    legend = _box('wx-radar-legend', vertical=False, spacing=10, halign=Gtk.Align.START, valign=Gtk.Align.START)
    for level, name in ((1, 'Light'), (2, 'Moderate'), (3, 'Heavy')):
        key = _box(vertical=False, spacing=4)
        swatch = _box(f'wx-radar-key-{level}', valign=Gtk.Align.CENTER)
        swatch.set_size_request(10, 10)
        key.append(swatch)
        key.append(label(name, 'weather-phone-caption'))
        legend.append(key)
    frame.add_overlay(legend)
    scrub = _box('wx-radar-time', vertical=False, spacing=10, valign=Gtk.Align.END)
    play = IconOnlyButton('pause' if state.radar_playing else 'play', 'Pause' if state.radar_playing else 'Play',
                          on_click=on_play)
    play.set_name('wx-radar-play')
    scrub.append(play)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, RADAR_STEPS, 1)
    scale.set_draw_value(False)
    scale.set_hexpand(True)
    scale.set_value(state.radar_step)
    scale.update_property([Gtk.AccessibleProperty.LABEL], ['Time'])
    scale.connect('value-changed', lambda s: on_step(int(round(s.get_value()))))
    scrub.append(scale)
    when = label(f'+{state.radar_step * 15} min' if state.radar_step else 'Now', 'weather-phone-strong')
    when.set_size_request(64, -1)
    when.label.set_xalign(1)
    scrub.append(when)
    frame.add_overlay(scrub)
    frame.radar_canvas, frame.radar_scale, frame.radar_when = canvas, scale, when
    return [_page_head('Radar', f'{snapshot.place.name} · rain and storms, next 2 hours'), frame]


# ── What the bar's menus hold ──────────────────────────────────────────

def place_row_parts(snapshot: Snapshot) -> tuple[str, str, str, str]:
    """(title, subtitle, value, icon) for a place in the Places panel."""
    title = f'{snapshot.place.name}{" · My location" if snapshot.my_location else ""}'
    when = snapshot.clock or snapshot.location
    return title, f'{snapshot.condition} · {when}' if when else snapshot.condition, snapshot.temperature, \
        weather_icon(snapshot.glyph)
