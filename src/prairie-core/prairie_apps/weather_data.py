# SPDX-License-Identifier: Apache-2.0
"""Weather's presentation data, independent of GTK and its visual parts.

Missing provider values stay missing. Only weather_fixture computes Studio's
sample numbers; live forecasts never pass through those formulas.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import math

from .weather_backend import local_now, to_local
from .weather_observation import Observation
from .weather_model import (
    CityForecast, Place, UNITS_IMPERIAL, cardinal, clock_text, condition_for,
    glyph_at, hour_label, is_night, precipitation_amount_text, summary_sentence,
    temperature_text, uv_word,
)


@dataclass(frozen=True)
class Hour:
    label: str
    temperature: str
    condition: str
    night: bool
    precipitation: str = ""
    description: str = ""   # "Light rain": the phone's Hourly rows
    time: str = ""          # "11 AM": the phone's Hourly rows (label is the strip's "11AM")
    at: datetime | None = None  # the hour's start, where the source knows it (Your day looks plans up by it)


@dataclass(frozen=True)
class Day:
    label: str
    low: str
    high: str
    condition: str
    low_value: float
    high_value: float
    precipitation: str = ""


@dataclass(frozen=True)
class Metric:
    key: str
    title: str
    icon: str
    value: str
    description: str = ""
    position: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class Alert:
    """An official warning ("Heads up"): what, when, what to do, and who says so."""
    title: str
    when: str
    description: str
    source: str
    starts: str = ""      # "3 PM": the clause the day's sentence gains ("Wind advisory from 3 PM.")
    level: str = "warn"


@dataclass(frozen=True)
class Plan:
    """One of your remaining plans today, with the weather at its start ("Your day")."""
    title: str
    time: str
    condition: str
    night: bool
    temperature: str
    jacket: bool = False


@dataclass(frozen=True)
class Change:
    """One step of "What's next": a change of sky, or the sun rising or setting, in the coming 12 hours."""
    after: str            # "In 7 h", "Soon"
    label: str            # "Light rain", "Sunset"
    time: str             # "6 PM"
    condition: str = ""   # a weather glyph key, or "" for the sun's own icon
    night: bool = False
    icon: str = ""        # "sunrise" / "sunset"


@dataclass(frozen=True)
class Tile:
    """One of the phone's six tiles (Rain, Wind, UV, Air, Sunset or Sunrise, Humidity)."""
    key: str
    title: str
    icon: str
    value: str
    description: str = ""


@dataclass(frozen=True)
class Snapshot:
    place: Place
    location: str
    temperature: str
    condition: str
    glyph: str
    sky: str
    high: str
    low: str
    sentence: str
    hours: tuple[Hour, ...]
    days: tuple[Day, ...]
    metrics: tuple[Metric, ...]
    my_location: bool = False
    current_value: float | None = None
    provenance: str = ''
    alerts: tuple[Alert, ...] = ()
    changes: tuple[Change, ...] = ()
    tiles: tuple[Tile, ...] = ()
    plans: tuple[Plan, ...] = ()
    clock: str = ''   # the place's own time, "10:40 AM" (the Places panel)

    @property
    def phone_sentence(self) -> str:
        """The day's sentence, plus the first warning's clause (v71 wxBodyPhone `say`)."""
        if self.alerts and self.alerts[0].starts:
            return f"{self.sentence} {self.alerts[0].title} from {self.alerts[0].starts}."
        return self.sentence

    @property
    def condition_line(self) -> str:
        """Under the phone's temperature: "Clear · H 71° L 53°"."""
        parts = [f"H {self.high}" if self.high else "", f"L {self.low}" if self.low else ""]
        high_low = " ".join(p for p in parts if p)
        return f"{self.condition} · {high_low}" if high_low else self.condition


def js_round(value: float) -> int:
    """Studio uses Math.round, including negative half-degrees."""
    return math.floor(value + .5)


def weather_icon(glyph: str, *, night: bool = False) -> str:
    icon = {
        "clear": "sun", "sun": "sun", "moon": "moon", "partly": "cloud-sun", "part": "cloud-sun",
        "cloud": "cloud", "rain": "cloud-rain", "snow": "snowflake",
        "fog": "cloud-fog", "thunder": "cloud-lightning", "ncloud": "cloud-moon",
    }.get(glyph, "cloud")
    return {"sun": "moon", "cloud-sun": "cloud-moon"}.get(icon, icon) if night else icon


def _night_at(symbol: str, moment: datetime) -> bool:
    if symbol.endswith("_night"):
        return True
    if symbol.endswith("_day"):
        return False
    return is_night(moment)


def short_time(moment: datetime, *, minutes: bool = True) -> str:
    """v71's `hm`: "2 PM", "10:30 AM" (`minutes=False` is `hh`: "10 AM")."""
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {suffix}" if minutes and moment.minute else f"{hour} {suffix}"


def _changes(hours: list, moment: datetime, sunrise: datetime | None, sunset: datetime | None,
             night: bool) -> tuple[Change, ...]:
    """What's next (v71 `nx`): up to three changes of sky in the next 12 hours, and the sun."""
    found: list[tuple[int, Change]] = []
    previous = hours[0].condition if hours else ""
    for index, hour in enumerate(hours[1:13], start=1):
        if hour.at is None:
            continue
        if hour.condition != previous:
            after = js_round((hour.at - moment).total_seconds() / 3600)
            found.append((after, Change(_after(after), hour.description, short_time(hour.at),
                                        hour.condition, hour.night)))
            previous = hour.condition
    target = sunrise if night else sunset
    if target is not None and timedelta(0) <= target - moment < timedelta(hours=12):
        after = js_round((target - moment).total_seconds() / 3600)
        name = "Sunrise" if night else "Sunset"
        found.append((after, Change(_after(after), name, short_time(target), icon=name.lower())))
    found.sort(key=lambda item: item[0])
    return tuple(change for _after_hours, change in found[:3])


def _after(hours: int) -> str:
    return "Soon" if hours < 1 else f"In {hours} h"


def plans_for(snapshot: Snapshot, events, moment: datetime) -> tuple[Plan, ...]:
    """Your day (v71 `dayEvents`): the rest of today's timed plans, with the hour's weather at each start.

    `events` carry `summary`, `start`, `end` and `all_day` (calendar_backend.Event). A plan whose
    start has no forecast hour (it began before the first one) takes the current conditions.
    """
    plans = []
    for event in sorted(events, key=lambda e: e.start):
        if getattr(event, "all_day", False) or event.end <= moment:
            continue
        start = event.start.astimezone(moment.tzinfo) if moment.tzinfo and event.start.tzinfo else event.start
        hour = next((h for h in snapshot.hours if h.at is not None and h.at <= start < h.at + timedelta(hours=1)),
                    snapshot.hours[0] if snapshot.hours and start <= moment else None)
        if hour is None:
            continue
        plans.append(Plan((getattr(event, "summary", "") or "").strip() or "Busy", short_time(start),
                          hour.condition, hour.night, hour.temperature, hour.condition == "rain"))
        if len(plans) == 3:
            break
    return tuple(plans)


def snapshot_from_forecast(forecast: CityForecast, units: str, *, now: datetime | None = None,
                           observation: Observation | None = None, alerts: tuple[Alert, ...] = ()) -> Snapshot:
    current, place = forecast.current, forecast.place
    moment = local_now(place, now)
    timed_hours = [(hour, to_local(place, hour.time)) for hour in forecast.hours]
    covered = [index for index, (_, at) in enumerate(timed_hours)
               if at is not None and at <= moment < at + timedelta(hours=1)]
    start = covered[-1] if covered else 0
    fetched_at = to_local(place, forecast.fetched_at)
    first_hour_became_current = bool(covered and fetched_at is not None
                                    and timed_hours[start][1] > fetched_at)
    if start or first_hour_became_current:
        hour, at = timed_hours[start]
        current = replace(current, temperature_c=hour.temperature_c, symbol=hour.symbol,
                          precipitation_probability=hour.precipitation_probability,
                          precipitation_mm=hour.precipitation_mm)
        first_at = timed_hours[0][1]
        if first_at is not None and first_at.date() != moment.date():
            today = next((day for day in forecast.days if day.date == moment.date().isoformat()), None)
            current = replace(current, high_c=today.high_c if today else None,
                              low_c=today.low_c if today else None, sunrise='', sunset='')
    visible_hours = timed_hours[start:start + 25]
    if observation is not None:
        current = replace(current, temperature_c=observation.temperature_c,
                          symbol=observation.symbol)
    night = _night_at(current.symbol, moment)
    condition = condition_for(current.symbol)
    sky = "night" if night else "day"
    if condition.sky in ("rain", "cloudy", "snow"):
        sky = {"cloudy": "cloud", "rain": "rain", "snow": "snow"}[condition.sky]
    sunrise, sunset = (to_local(place, value) for value in (current.sunrise, current.sunset))
    if sunrise is not None and sunset is not None and sunrise < sunset:
        night = moment < sunrise or moment > sunset
        if condition.sky not in ("rain", "cloudy", "snow"):
            nearest = min(abs((moment - sunrise).total_seconds()), abs((moment - sunset).total_seconds()))
            sky = "dusk" if nearest < 75 * 60 else "night" if night else "day"
    hours = []
    for index, (hour, at) in enumerate(visible_hours):
        if at is None:
            continue
        kind = condition_for(hour.symbol)
        hours.append(Hour("Now" if covered and index == 0 else hour_label(at, twelve_hour=True).replace(" ", ""),
                          temperature_text(hour.temperature_c, units), kind.glyph, _night_at(hour.symbol, at),
                          f"{js_round(hour.precipitation_probability)}%"
                          if hour.precipitation_probability else "",
                          kind.text, "Now" if covered and index == 0 else short_time(at, minutes=False), at))
    days = []
    for day in forecast.days:
        at = datetime.fromisoformat(day.date)
        if at.date() < moment.date():
            continue
        days.append(Day("Today" if at.date() == moment.date() else at.strftime("%a"),
                        temperature_text(day.low_c, units), temperature_text(day.high_c, units),
                        condition_for(day.symbol).glyph, day.low_c, day.high_c))
        if len(days) == 10:
            break
    metrics = []
    probabilities = [h.precipitation_probability for h, _ in visible_hours
                     if h.precipitation_probability is not None]
    if observation and observation.nearby_rain:
        metrics.append(Metric("rain", "Rain nearby", "umbrella", observation.nearby_rain,
                              f"NWS · {observation.nearby_rain_station}"))
    elif probabilities:
        value = current.precipitation_probability
        if value is None:
            value = probabilities[0]
        metrics.append(Metric("rain", "Rain chance", "umbrella", f"{js_round(value)}%",
                              "Next hour · MET forecast"))
    elif current.precipitation_mm is not None and current.precipitation_mm > 0:
        metrics.append(Metric("rain", "Forecast rain", "umbrella",
                              precipitation_amount_text(current.precipitation_mm, units),
                              "Next hour · MET forecast"))
    if current.wind_mps is not None:
        imperial = units == UNITS_IMPERIAL
        value = current.wind_mps * (2.2369362921 if imperial else 3.6)
        where = f"from the {cardinal(current.wind_degrees)}" if current.wind_degrees is not None else ""
        metrics.append(Metric("wind", "Wind", "wind", str(js_round(value)),
                              f"{'mph' if imperial else 'km/h'} {where}".strip(), current.wind_degrees))
    if current.uv_index is not None:
        metrics.append(Metric("uv", "UV index", "sun", f"{current.uv_index:g}",
                              uv_word(current.uv_index).capitalize(), current.uv_index, 11))
    if sunrise is not None and sunset is not None and sunrise < sunset:
        target, previous = (sunrise, sunset) if night else (sunset, sunrise)
        progress = None if night else max(0, min(1, (moment - sunrise) / (sunset - sunrise)))
        metrics.append(Metric("sun", "Sunrise" if night else "Sunset", "sunrise" if night else "sunset",
                              clock_text(target, twelve_hour=True),
                              f"{'Sunset' if night else 'Sunrise'} {'was' if previous <= moment else 'at'} "
                              f"{clock_text(previous, twelve_hour=True)}", progress))
    if current.humidity is not None:
        # MET's current model does not supply dew point. Never invent one.
        metrics.append(Metric("humidity", "Humidity", "droplets", f"{js_round(current.humidity)}%"))
    # No air-quality source exists in the live backend. Fixture data alone has it.
    tiles = []
    window = [(h, at) for h, at in visible_hours[:24] if at is not None]
    chances = [h.precipitation_probability for h, _ in window if h.precipitation_probability is not None]
    if chances:
        wet = [at for h, at in window if (h.precipitation_probability or 0) >= 30]
        tiles.append(Tile("rain", "Rain", "umbrella", f"{js_round(max(chances))}%",
                          f"{short_time(wet[0], minutes=False)} to {short_time(wet[-1] + timedelta(hours=1), minutes=False)}"
                          if wet else "None today" if not max(chances) else ""))
    if current.wind_mps is not None:
        imperial = units == UNITS_IMPERIAL
        speed = js_round(current.wind_mps * (2.2369362921 if imperial else 3.6))
        tiles.append(Tile("wind", "Wind", "wind", f"{speed} {'mph' if imperial else 'km/h'}",
                          f"from the {cardinal(current.wind_degrees)}" if current.wind_degrees is not None else ""))
    if current.uv_index is not None:
        tiles.append(Tile("uv", "UV", "sun", f"{current.uv_index:g}", uv_word(current.uv_index).capitalize()))
    if sunrise is not None and sunset is not None and sunrise < sunset:
        tiles.append(Tile("sun", "Sunrise" if night else "Sunset", "sunrise" if night else "sunset",
                          clock_text(sunrise if night else sunset, twelve_hour=True)))
    if current.humidity is not None:
        tiles.append(Tile("humidity", "Humidity", "droplets", f"{js_round(current.humidity)}%"))
    changes = _changes(hours, moment, sunrise, sunset, night)
    observed_at = to_local(place, observation.observed_at) if observation else None
    fetched_at_local = to_local(place, forecast.fetched_at)
    provenance = (f"NWS observed · {observation.station_name} · {clock_text(observed_at, twelve_hour=True)}"
                  if observation and observed_at else
                  f"MET forecast · updated {clock_text(fetched_at_local, twelve_hour=True)}"
                  if fetched_at_local else "MET forecast · update time unavailable")
    if observation:
        provenance += " · Other details: MET forecast"
    elif forecast.age_seconds(now) is None or forecast.age_seconds(now) > 2 * 60 * 60:
        provenance = "Cached " + provenance
    return Snapshot(place, clock_text(moment, twelve_hour=True), temperature_text(current.temperature_c, units),
                    observation.description if observation else condition.text,
                    "ncloud" if night and condition.glyph == "partly" else glyph_at(condition.glyph, night), sky,
                    temperature_text(current.high_c, units), temperature_text(current.low_c, units),
                    summary_sentence(condition,
                                     tuple(replace(hour, time=at.isoformat())
                                           for hour, at in visible_hours if at is not None),
                                     moment, twelve_hour=True, include_first=not covered),
                    tuple(hours), tuple(days), tuple(metrics), current_value=current.temperature_c,
                    provenance=provenance, alerts=tuple(alerts), changes=changes, tiles=tuple(tiles),
                    clock=clock_text(moment, twelve_hour=True))
