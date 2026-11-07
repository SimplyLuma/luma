# SPDX-License-Identifier: Apache-2.0
"""Weather's TV presentation data: every day as 24 hours, with no GTK import.

The TV presentation (weather_tv) shows the same forecast as the desktop and
the phone, walked hour by hour: Left and Right move through 24 hours, Down
picks one of ten days. This module turns a CityForecast (or the fixture's
sample) into that shape:

* Today runs 24 hours from the current hour; any other day runs from midnight.
* MET is hourly for about two and a half days and six-hourly after that. A
  six-hour step's temperature and details are interpolated hour by hour to the
  next step, and its symbol and precipitation cover the six hours, as MET
  states them. Nothing past the end of the series is drawn.
* The sky's light at an hour (night, golden, rain or day, and the cloudy
  variants) colours the band and the screen. Sunrise and sunset are MET's for
  today and the NOAA solar equations for the other days.
* A figure we were not given is not a figure: tiles without a value drop out,
  and the next tile with a real value takes the place (air quality has no live
  source, so the rain chance stands in for it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Sequence

from .weather_model import (
    _ARRIVALS, SKY_CLEAR, UNITS_IMPERIAL, condition_for, parse_time, temperature_text, uv_word,
)

HOURS = 24
DAYS = 10

# The band's light (the desktop's bandCol), a top colour and the screen's darker floor.
LIGHT_NIGHT, LIGHT_GOLDEN, LIGHT_RAIN, LIGHT_DAY = "n", "g", "r", "d"
SKY_LIGHT = {
    LIGHT_NIGHT: ("#1c2350", "#070a1c"),
    LIGHT_GOLDEN: ("#e08a4f", "#3b2a5c"),
    LIGHT_RAIN: ("#5d7590", "#1f2c3d"),
    LIGHT_DAY: ("#3d86d6", "#123a7a"),
}
CLOUDY_LIGHT = {"part": ("#5b8fcb", "#1e3f72"), "cloud": ("#7d8ea3", "#2a3646"),
                "fog": ("#7d8ea3", "#2a3646")}
# Minutes either side of sunrise and sunset: night ends/starts 20 minutes out, golden light is 50.
NIGHT_MARGIN, GOLDEN_MARGIN = 20, 50
WET = frozenset({"rain", "snow", "thunder"})

# MET symbol (via condition_for().glyph) to the TV vocabulary, which is the glyph set's.
_GLYPH_TO_CONDITION = {"sun": "clear", "moon": "clear", "partly": "part", "cloud": "cloud",
                       "rain": "rain", "snow": "snow", "fog": "fog", "thunder": "thunder"}
CONDITION_TEXT = {"clear": "Clear", "part": "Partly cloudy", "cloud": "Cloudy", "rain": "Light rain",
                  "snow": "Snow", "fog": "Fog", "thunder": "Thunder"}


@dataclass(frozen=True)
class Tile:
    """One detail tile: `value` large, `unit` small beside it (`11` `mph W`)."""
    key: str
    title: str
    icon: str
    value: str
    unit: str = ""


@dataclass(frozen=True)
class TvHour:
    at: datetime
    temperature: str             # "62°"
    condition: str               # clear part cloud rain snow fog thunder
    text: str                    # "Partly cloudy"
    night: bool
    light: str                   # LIGHT_*: the band and the sky
    wet: bool = False            # hatched on the band
    precipitation: str = ""      # "40%"
    feels: str = ""
    wind: str = ""
    wind_unit: str = ""          # "mph W"
    humidity: str = ""
    uv: str = ""
    uv_word: str = ""

    @property
    def sky(self) -> tuple[str, str]:
        return sky_colours(self.light, self.condition)


@dataclass(frozen=True)
class TvDay:
    label: str                   # "Today", "Mon"
    name: str                    # "Monday" (the hero: "Oakland · Monday, 2 PM")
    high: str
    low: str
    condition: str
    sentence: str
    hours: tuple[TvHour, ...] = ()
    sun: tuple[tuple[datetime, str], ...] = ()   # (moment, "rise"|"set") inside or after the day's hours


@dataclass(frozen=True)
class TvForecast:
    name: str
    my_location: bool
    now: datetime
    days: tuple[TvDay, ...]
    now_tiles: tuple[Tile, ...] = ()      # the current reading's tiles (hour 0 of today)
    air: tuple[str, str] | None = None    # (index, word) where a source has it
    clock: str = ""

    def tiles(self, day: int, hour: int) -> tuple[Tile, ...]:
        """Six tiles for an hour: feels like, wind, humidity, UV, air quality, the next sunrise or sunset."""
        if day == 0 and hour == 0 and self.now_tiles:
            return self.now_tiles[:6]
        found = self.day(day)
        if not found.hours:
            return ()
        point = found.hours[min(hour, len(found.hours) - 1)]
        return hour_tiles(point, self.air, next_sun(self.days, day, point))

    def day(self, index: int) -> TvDay:
        return self.days[max(0, min(index, len(self.days) - 1))]


def sky_colours(light: str, condition: str) -> tuple[str, str]:
    if light == LIGHT_DAY and condition in CLOUDY_LIGHT:
        return CLOUDY_LIGHT[condition]
    return SKY_LIGHT[light]


def light_at(moment: datetime, condition: str, night: bool,
             sun: Sequence[tuple[datetime, str]]) -> str:
    """Night, golden, rain or day, from the sun's times when known, else the night flag."""
    if sun:
        nearest = min(abs((moment - at).total_seconds()) / 60 for at, _kind in sun)
        dark = night and nearest > NIGHT_MARGIN
        if dark:
            return LIGHT_NIGHT
        if nearest < GOLDEN_MARGIN:
            return LIGHT_GOLDEN
    elif night:
        return LIGHT_NIGHT
    return LIGHT_RAIN if condition in WET else LIGHT_DAY


def hour_tiles(point: TvHour, air: tuple[str, str] | None,
               sun: tuple[datetime, str] | None) -> tuple[Tile, ...]:
    tiles = []
    if point.feels:
        tiles.append(Tile("feels", "Feels like", "thermometer", point.feels))
    if point.wind:
        tiles.append(Tile("wind", "Wind", "wind", point.wind, point.wind_unit))
    if point.humidity:
        tiles.append(Tile("humidity", "Humidity", "droplets", point.humidity))
    if point.uv:
        tiles.append(Tile("uv", "UV index", "sun", point.uv, point.uv_word))
    if air:
        tiles.append(Tile("air", "Air quality", "wind", air[0], air[1]))
    elif point.precipitation:
        tiles.append(Tile("rain", "Rain chance", "umbrella", point.precipitation))
    if sun:
        at, kind = sun
        tiles.append(Tile("sun", "Sunrise" if kind == "rise" else "Sunset",
                          "sunrise" if kind == "rise" else "sunset", short_time(at)))
    return tuple(tiles[:6])


def next_sun(days: Sequence[TvDay], day: int, point: TvHour) -> tuple[datetime, str] | None:
    """The next sunrise (at night) or sunset (by day) after an hour."""
    want = "rise" if point.night else "set"
    events = sorted({event for d in days[day:day + 2] for event in d.sun})
    return next(((at, kind) for at, kind in events if kind == want and at > point.at), None)


def short_time(moment: datetime, *, minutes: bool = True) -> str:
    """"2 PM", "10:30 AM" (v71 `hm`); minutes=False is "10 AM" (`hh`)."""
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {suffix}" if minutes and moment.minute else f"{hour} {suffix}"


def band_hour(moment: datetime) -> str:
    """The band's hour labels: "12 AM", "Noon", "3 PM"."""
    if moment.hour == 12:
        return "Noon"
    return short_time(moment, minutes=False)


# ── The day's sentence ───────────────────────────────────────────────────

def day_sentence(hours: Sequence[TvHour], high: str) -> str:
    """Another day's sentence: what changes in the daylight, then its high.

    Today's sentence is the desktop's (summary_sentence); another day has no
    "now", so it says what the day does: "Rain arriving around 2 PM. A high of 61°."
    """
    daylight = [h for h in hours if 8 <= h.at.hour < 20] or list(hours)
    if not daylight:
        return f"A high of {high}." if high else ""
    first = daylight[0]
    skies = {_sky(h.condition) for h in daylight}
    tail = f" A high of {high}." if high else ""
    if len(skies) == 1:
        return f"{first.text} all day.{tail}" if first.condition != "clear" else f"Clear all day.{tail}"
    for hour in daylight[1:]:
        if _sky(hour.condition) != _sky(first.condition):
            verb = _ARRIVALS.get(_sky(hour.condition))
            if verb:
                return f"{verb} around {short_time(hour.at, minutes=False)}.{tail}"
            break
    return f"{first.text}, changing through the day.{tail}"


def _sky(condition: str) -> str:
    return {"clear": SKY_CLEAR, "part": "partly", "cloud": "cloudy", "fog": "cloudy",
            "rain": "rain", "snow": "rain", "thunder": "rain"}[condition]


# ── The sun ──────────────────────────────────────────────────────────────

def sun_times(latitude: float, longitude: float, day: date, zone) -> tuple[datetime | None, datetime | None]:
    """Sunrise and sunset in `zone` (NOAA solar equations; None in polar day or night)."""
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    lat = math.radians(latitude)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(decl))
              - math.tan(lat) * math.tan(decl))
    if not -1 <= cos_ha <= 1:
        return None, None
    ha = math.degrees(math.acos(cos_ha))
    midnight = datetime.combine(day, time(0), tzinfo=timezone.utc)
    rise = midnight + timedelta(minutes=720 - 4 * (longitude + ha) - eqtime)
    down = midnight + timedelta(minutes=720 - 4 * (longitude - ha) - eqtime)
    return rise.astimezone(zone), down.astimezone(zone)


# ── From a live forecast ─────────────────────────────────────────────────

@dataclass
class _Step:
    at: datetime
    temperature_c: float
    symbol: str
    precipitation_probability: float | None = None
    apparent_c: float | None = None
    humidity: float | None = None
    wind_mps: float | None = None
    wind_degrees: float | None = None
    uv_index: float | None = None
    extras: dict = field(default_factory=dict)


def _hourly(forecast, zone) -> list[_Step]:
    """The series as one step per hour: six-hour steps interpolated to the next step."""
    points = [(parse_time(p.time), p) for p in forecast.timeline]
    points = [(at.astimezone(zone), p) for at, p in points if at is not None]
    if not points:
        points = [(at.astimezone(zone), h) for at, h in ((parse_time(h.time), h) for h in forecast.hours)
                  if at is not None]
    steps: list[_Step] = []
    for index, (at, point) in enumerate(points):
        span = max(1, int(getattr(point, "span_hours", 1) or 1))
        following = points[index + 1] if index + 1 < len(points) else None
        for offset in range(span):
            moment = at + timedelta(hours=offset)
            if following and moment >= following[0]:
                break
            if offset and not following:
                break
            share = offset / span if following else 0.0
            nxt = following[1] if following else point
            steps.append(_Step(
                moment,
                _mix(point.temperature_c, nxt.temperature_c, share),
                point.symbol,
                point.precipitation_probability,
                _mix(getattr(point, "apparent_c", None), getattr(nxt, "apparent_c", None), share),
                _mix(getattr(point, "humidity", None), getattr(nxt, "humidity", None), share),
                _mix(getattr(point, "wind_mps", None), getattr(nxt, "wind_mps", None), share),
                getattr(point, "wind_degrees", None),
                _mix(getattr(point, "uv_index", None), getattr(nxt, "uv_index", None), share),
            ))
    return steps


def _mix(a: float | None, b: float | None, share: float) -> float | None:
    if a is None:
        return None
    if b is None or not share:
        return a
    return a + (b - a) * share


def _round(value: float) -> int:
    return math.floor(value + .5)


def _cardinal8(degrees: float | None) -> str:
    if degrees is None:
        return ""
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[_round(degrees / 45) % 8]


def _condition(symbol: str) -> str:
    return _GLYPH_TO_CONDITION.get(condition_for(symbol).glyph, "cloud")


def _sun_down(moment: datetime, sun: Sequence[tuple[datetime, str]]) -> bool | None:
    """Whether the sun is down at `moment`, from that day's sunrise and sunset; None without them."""
    rises = [at for at, kind in sun if kind == "rise" and at.date() == moment.date()]
    sets = [at for at, kind in sun if kind == "set" and at.date() == moment.date()]
    if rises and sets:
        return moment < rises[0] or moment > sets[0]
    return None


def _night_flag(symbol: str, moment: datetime, sun: Sequence[tuple[datetime, str]]) -> bool:
    """Night for the glyph: MET's own day/night symbol first, then the sun, then the 20:00-06:00 rule."""
    if symbol.endswith("_night"):
        return True
    if symbol.endswith("_day"):
        return False
    down = _sun_down(moment, sun)
    return down if down is not None else (moment.hour >= 20 or moment.hour < 6)


def tv_from_forecast(forecast, snapshot, units: str, *, now: datetime | None = None) -> TvForecast:
    """The TV's ten days from a live forecast, with today's opening hour read from `snapshot`."""
    from .weather_backend import local_now, local_zone, to_local
    place = forecast.place
    zone = local_zone(place)
    moment = local_now(place, now)
    imperial = units == UNITS_IMPERIAL
    steps = _hourly(forecast, zone)
    today = moment.date()
    sun_by_day: dict[date, list[tuple[datetime, str]]] = {}
    for k in range(DAYS + 1):
        when = today + timedelta(days=k)
        rise, down = sun_times(place.latitude, place.longitude, when, zone)
        if k == 0:
            rise = to_local(place, forecast.current.sunrise) or rise
            down = to_local(place, forecast.current.sunset) or down
        sun_by_day[when] = [(at, kind) for at, kind in ((rise, "rise"), (down, "set")) if at is not None]
    all_sun = [event for events in sun_by_day.values() for event in events]

    def make(step: _Step) -> TvHour:
        condition = _condition(step.symbol)
        night = _night_flag(step.symbol, step.at, all_sun)
        near = [e for e in all_sun if abs((e[0] - step.at).total_seconds()) < 36 * 3600]
        speed = step.wind_mps * (2.2369362921 if imperial else 3.6) if step.wind_mps is not None else None
        middle = step.at + timedelta(minutes=30)
        down = _sun_down(middle, all_sun)
        return TvHour(
            step.at, temperature_text(step.temperature_c, units), condition, CONDITION_TEXT[condition],
            night, light_at(middle, condition, night if down is None else down, near), condition in WET,
            f"{_round(step.precipitation_probability)}%" if step.precipitation_probability is not None else "",
            temperature_text(step.apparent_c, units) if step.apparent_c is not None else "",
            str(_round(speed)) if speed is not None else "",
            f"{'mph' if imperial else 'km/h'} {_cardinal8(step.wind_degrees)}".strip() if speed is not None else "",
            f"{_round(step.humidity)}%" if step.humidity is not None else "",
            f"{_round(step.uv_index)}" if step.uv_index is not None else "",
            uv_word(step.uv_index).capitalize() if step.uv_index is not None else "",
        )

    start_hour = moment.replace(minute=0, second=0, microsecond=0)
    days: list[TvDay] = []
    for k, day in enumerate(forecast_days(forecast, today)):
        if k == 0:
            begin = start_hour
        else:
            begin = datetime.combine(day.date_value, time(0), tzinfo=zone)
        end = begin + timedelta(hours=HOURS)
        hours = tuple(make(s) for s in steps if begin <= s.at < end)
        if k == 0 and hours and snapshot is not None:
            hours = (_with_now(hours[0], snapshot),) + hours[1:]
        events = tuple(sorted(e for e in all_sun if begin <= e[0] < end + timedelta(hours=12)))
        high, low = temperature_text(day.high_c, units), temperature_text(day.low_c, units)
        if k == 0 and snapshot is not None:
            # Today's range is the snapshot's: it includes the reading now (weather_data).
            high, low = snapshot.high or high, snapshot.low or low
        condition = _condition(day.symbol)
        sentence = snapshot.sentence if k == 0 and snapshot is not None else day_sentence(hours, high)
        days.append(TvDay("Today" if k == 0 else day.date_value.strftime("%a"),
                          day.date_value.strftime("%A"), high, low, condition, sentence, hours, events))
    now_tiles = ()
    if days and days[0].hours:
        now_tiles = _now_tiles(snapshot, days[0].hours[0], days, units) if snapshot is not None else ()
    return TvForecast(place.name, bool(snapshot and snapshot.my_location), moment, tuple(days), now_tiles,
                      None, snapshot.clock if snapshot is not None else "")


@dataclass(frozen=True)
class _Day:
    date_value: date
    symbol: str
    high_c: float
    low_c: float


def forecast_days(forecast, today: date) -> list[_Day]:
    days = []
    for day in forecast.days:
        try:
            when = date.fromisoformat(day.date)
        except ValueError:
            continue
        if when < today:
            continue
        days.append(_Day(when, day.symbol, day.high_c, day.low_c))
    return days[:DAYS]


def _with_now(hour: TvHour, snapshot) -> TvHour:
    """Today's first hour is the current reading the desktop shows (observation included)."""
    from dataclasses import replace
    condition = {"sun": "clear", "moon": "clear", "partly": "part", "ncloud": "part"}.get(snapshot.glyph, snapshot.glyph)
    condition = condition if condition in CONDITION_TEXT else hour.condition
    return replace(hour, temperature=snapshot.temperature or hour.temperature, condition=condition,
                   text=snapshot.condition or hour.text, wet=condition in WET)


def _now_tiles(snapshot, hour: TvHour, days: Sequence[TvDay], units: str) -> tuple[Tile, ...]:
    """The current reading's tiles from the snapshot's metrics, where it has them; the hour fills the rest."""
    metrics = {m.key: m for m in snapshot.metrics}
    from dataclasses import replace
    wind = metrics.get("wind")
    uv = metrics.get("uv")
    humidity = metrics.get("humidity")
    current = getattr(snapshot.forecast, "current", None)
    apparent = getattr(current, "apparent_c", None)
    point = replace(
        hour,
        feels=hour.feels or (temperature_text(apparent, units) if apparent is not None else ""),
        wind=wind.value if wind else hour.wind,
        wind_unit=_wind_unit(wind.description) if wind else hour.wind_unit,
        uv=uv.value if uv else hour.uv,
        uv_word=uv.description if uv else hour.uv_word,
        humidity=humidity.value if humidity else hour.humidity,
    )
    air = metrics.get("air")
    return hour_tiles(point, (air.value, air.description.split(".")[0]) if air else None,
                      next_sun(days, 0, point))


def _wind_unit(description: str) -> str:
    """"mph from the WSW" reads "mph WSW" on a tile."""
    return description.replace(" from the ", " ").strip()
