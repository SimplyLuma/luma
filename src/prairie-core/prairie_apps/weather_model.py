# SPDX-License-Identifier: Apache-2.0

"""Weather's model: everything the application knows that is not a widget.

Weather is data, not decoration. The sky's colour lives in one 88x88 tile and
nowhere else, so almost all of this app is arithmetic: which of five skies a
provider code means, whether it is night *where the city is*, what a figure
reads as in the person's units, and where a day's range sits on the week's
scale. All of that is here, with no GTK import, because it is the part that
can be tested without a display.

Two rules in this file are easy to get wrong and are therefore stated once:

* **Night is local to the city being shown.** 20:00-05:59 by the city's own
  clock, never the host's. London at 23:00 shows a moon while Cupertino shows
  a sun, in the same window. Night changes the sky and the glyph; it never
  changes the words. A clear night still reads "Clear".
* **A figure we were not given is not a figure.** There is no em dash and no
  zero standing in for a missing value: the row is dropped, and the border
  rule is computed from the rows that were actually rendered.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Iterable, Sequence


# ── The five skies ───────────────────────────────────────────────────────
# These five are the whole vocabulary. Snow, fog and thunder map onto one of
# them and change the *glyph*, not the gradient. Adding a sixth is a design
# change, not an implementation one.

SKY_CLEAR = "clear"
SKY_PARTLY = "partly"
SKY_CLOUDY = "cloudy"
SKY_RAIN = "rain"
SKY_NIGHT = "night"

SKIES = (SKY_CLEAR, SKY_PARTLY, SKY_CLOUDY, SKY_RAIN, SKY_NIGHT)

# 165 degrees, exactly as the design draws them, as (offset, r, g, b) stops.
SKY_GRADIENTS: dict[str, tuple[tuple[float, str], ...]] = {
    SKY_CLEAR: ((0.0, "#3f8fd6"), (0.48, "#63a8df"), (1.0, "#a6cfea")),
    SKY_PARTLY: ((0.0, "#5389bf"), (0.50, "#7ba6d2"), (1.0, "#b3cbe0")),
    SKY_CLOUDY: ((0.0, "#71808f"), (0.55, "#93a0ad"), (1.0, "#bcc5cd")),
    SKY_RAIN: ((0.0, "#44566a"), (0.55, "#647889"), (1.0, "#93a2af")),
    SKY_NIGHT: ((0.0, "#182136"), (0.55, "#2a3a5c"), (1.0, "#42527a")),
}
GRADIENT_ANGLE_DEGREES = 165.0


# ── The glyph families ───────────────────────────────────────────────────
# Seven shapes, built from the same sun and the same cloud, drawn in two
# palettes. The palette is chosen by the surface, never by the condition.

GLYPH_SUN = "sun"
GLYPH_PARTLY = "partly"
GLYPH_CLOUD = "cloud"
GLYPH_RAIN = "rain"
GLYPH_SNOW = "snow"
GLYPH_FOG = "fog"
GLYPH_THUNDER = "thunder"
GLYPH_MOON = "moon"

GLYPHS = (GLYPH_SUN, GLYPH_PARTLY, GLYPH_CLOUD, GLYPH_RAIN,
          GLYPH_SNOW, GLYPH_FOG, GLYPH_THUNDER, GLYPH_MOON)

# The colour set. Its clouds are white: it belongs on the condition tile and
# on the selected city row, which are the only two tinted surfaces in the app.
PALETTE_COLOUR = {
    "sun": "#ffd66e",
    "cloud": "#ffffff",
    "cloud_alt": "#eef2f7",
    "moon": "#e9edf6",
    "rain": "#bfe0ff",
    "bolt": "#ffd66e",
}

# The ink set. Every light surface uses it: unselected rows, the daily list,
# the Add City card, the empty state. A white cloud on --chrome vanishes and
# reads as a rendering bug; this is the single easiest mistake to ship here.
PALETTE_INK = {
    "sun": "#e8a33d",
    "cloud": "#9aa6b5",
    "cloud_alt": "#9aa6b5",
    "moon": "#8b95a8",
    "rain": "#5b8fd0",
    "bolt": "#e8a33d",
}

SURFACE_TINTED = "tinted"
SURFACE_LIGHT = "light"

# Every place a glyph is drawn, and what kind of surface it is drawn on.
GLYPH_SURFACES = {
    "condition-tile": SURFACE_TINTED,
    "selected-city-row": SURFACE_TINTED,
    "city-row": SURFACE_LIGHT,
    "daily-row": SURFACE_LIGHT,
    "add-city-row": SURFACE_LIGHT,
    "empty-state": SURFACE_LIGHT,
}


def palette_for_surface(surface: str) -> dict[str, str]:
    """The glyph palette for a surface. The condition never chooses this."""
    kind = GLYPH_SURFACES.get(surface)
    if kind is None:
        raise KeyError(f"unknown glyph surface: {surface}")
    return PALETTE_COLOUR if kind is SURFACE_TINTED else PALETTE_INK


# ── Provider codes ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Condition:
    """What a provider code means, in the three terms the app needs."""

    sky: str
    glyph: str
    text: str


_SUFFIXES = ("_day", "_night", "_polartwilight")

# met.no's symbol vocabulary, which is the one our provider emits. Anything
# unknown falls back to cloudy, which is the quietest wrong answer.
_CONDITIONS: dict[str, Condition] = {
    "clearsky": Condition(SKY_CLEAR, GLYPH_SUN, "Clear"),
    "fair": Condition(SKY_CLEAR, GLYPH_SUN, "Fair"),
    "partlycloudy": Condition(SKY_PARTLY, GLYPH_PARTLY, "Partly Cloudy"),
    "cloudy": Condition(SKY_CLOUDY, GLYPH_CLOUD, "Cloudy"),
    "fog": Condition(SKY_CLOUDY, GLYPH_FOG, "Fog"),
    "lightrain": Condition(SKY_RAIN, GLYPH_RAIN, "Light Rain"),
    "rain": Condition(SKY_RAIN, GLYPH_RAIN, "Rain"),
    "heavyrain": Condition(SKY_RAIN, GLYPH_RAIN, "Heavy Rain"),
    "lightrainshowers": Condition(SKY_RAIN, GLYPH_RAIN, "Light Showers"),
    "rainshowers": Condition(SKY_RAIN, GLYPH_RAIN, "Showers"),
    "heavyrainshowers": Condition(SKY_RAIN, GLYPH_RAIN, "Heavy Showers"),
    "lightsleet": Condition(SKY_RAIN, GLYPH_RAIN, "Light Sleet"),
    "sleet": Condition(SKY_RAIN, GLYPH_RAIN, "Sleet"),
    "heavysleet": Condition(SKY_RAIN, GLYPH_RAIN, "Heavy Sleet"),
    "lightsleetshowers": Condition(SKY_RAIN, GLYPH_RAIN, "Light Sleet Showers"),
    "sleetshowers": Condition(SKY_RAIN, GLYPH_RAIN, "Sleet Showers"),
    "heavysleetshowers": Condition(SKY_RAIN, GLYPH_RAIN, "Heavy Sleet Showers"),
    "lightsnow": Condition(SKY_RAIN, GLYPH_SNOW, "Light Snow"),
    "snow": Condition(SKY_RAIN, GLYPH_SNOW, "Snow"),
    "heavysnow": Condition(SKY_RAIN, GLYPH_SNOW, "Heavy Snow"),
    "lightsnowshowers": Condition(SKY_RAIN, GLYPH_SNOW, "Light Snow Showers"),
    "snowshowers": Condition(SKY_RAIN, GLYPH_SNOW, "Snow Showers"),
    "heavysnowshowers": Condition(SKY_RAIN, GLYPH_SNOW, "Heavy Snow Showers"),
}

# The thunder variants are the same phenomena with a bolt, so they are built
# rather than listed: met.no spells every one of them "<base>andthunder".
_THUNDER_TEXT = {
    "lightrain": "Light Thundery Rain",
    "rain": "Thundery Rain",
    "heavyrain": "Heavy Thundery Rain",
    "lightrainshowers": "Light Thundery Showers",
    "rainshowers": "Thundery Showers",
    "heavyrainshowers": "Heavy Thundery Showers",
}
for _base, _condition in list(_CONDITIONS.items()):
    if _condition.sky == SKY_RAIN:
        _CONDITIONS[_base + "andthunder"] = Condition(
            SKY_RAIN, GLYPH_THUNDER,
            _THUNDER_TEXT.get(_base, "Thundery " + _condition.text),
        )

_UNKNOWN = Condition(SKY_CLOUDY, GLYPH_CLOUD, "Cloudy")


def condition_for(symbol_code: str) -> Condition:
    """Map a provider's symbol code onto the app's five skies and seven shapes."""
    code = (symbol_code or "").strip().lower()
    for suffix in _SUFFIXES:
        if code.endswith(suffix):
            code = code[: -len(suffix)]
            break
    return _CONDITIONS.get(code, _UNKNOWN)


# ── Night ────────────────────────────────────────────────────────────────

NIGHT_STARTS_HOUR = 20
NIGHT_ENDS_HOUR = 6


def is_night(moment: datetime) -> bool:
    """Night is 20:00-05:59 by the clock of whatever place `moment` is in.

    Pass the city's local time. Passing the host's is the bug the simulator
    has and the one thing §4.2 of the handoff asks us not to copy.
    """
    return moment.hour >= NIGHT_STARTS_HOUR or moment.hour < NIGHT_ENDS_HOUR


def sky_at(sky: str, night: bool) -> str:
    """Clear and partly become night after dark. Cloud and rain do not."""
    if night and sky in (SKY_CLEAR, SKY_PARTLY):
        return SKY_NIGHT
    return sky


def glyph_at(glyph: str, night: bool) -> str:
    """The crescent replaces the sun after dark. The cloud stays a cloud."""
    if night and glyph in (GLYPH_SUN, GLYPH_PARTLY):
        return GLYPH_MOON
    return glyph


# ── Units ────────────────────────────────────────────────────────────────

UNITS_AUTOMATIC = "automatic"
UNITS_METRIC = "metric"
UNITS_IMPERIAL = "imperial"
UNIT_CHOICES = (UNITS_AUTOMATIC, UNITS_METRIC, UNITS_IMPERIAL)

# The three countries whose measurement locale glibc reports as US customary.
_IMPERIAL_COUNTRIES = frozenset({"US", "LR", "MM"})


def measurement_from_locale(value: str) -> str:
    """Metric or imperial from a locale name, for when glibc cannot be asked."""
    name = (value or "").strip()
    if not name or name in ("C", "POSIX"):
        return UNITS_METRIC
    name = name.split(".")[0].split("@")[0]
    _, _, country = name.partition("_")
    return UNITS_IMPERIAL if country.upper() in _IMPERIAL_COUNTRIES else UNITS_METRIC


def resolve_units(setting: str, system: str) -> str:
    """Automatic follows the system measurement setting; the others override it."""
    if setting in (UNITS_METRIC, UNITS_IMPERIAL):
        return setting
    return system if system in (UNITS_METRIC, UNITS_IMPERIAL) else UNITS_METRIC


def temperature(celsius: float, units: str) -> int:
    """A whole degree. Never a decimal place, anywhere in this app."""
    value = celsius * 9.0 / 5.0 + 32.0 if units == UNITS_IMPERIAL else celsius
    return int(math.floor(value + 0.5))


def temperature_text(celsius: float | None, units: str) -> str:
    """`74°`. The degree sign never carries a letter: the unit is a setting."""
    if celsius is None:
        return ""
    return f"{temperature(celsius, units)}°"


_CARDINALS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

_CARDINAL_WORDS = {
    "N": "north", "NNE": "north-northeast", "NE": "northeast", "ENE": "east-northeast",
    "E": "east", "ESE": "east-southeast", "SE": "southeast", "SSE": "south-southeast",
    "S": "south", "SSW": "south-southwest", "SW": "southwest", "WSW": "west-southwest",
    "W": "west", "WNW": "west-northwest", "NW": "northwest", "NNW": "north-northwest",
}


def cardinal(degrees: float | None) -> str:
    """The sixteen-point compass name for the direction the wind comes from."""
    if degrees is None:
        return ""
    index = int((degrees % 360) / 22.5 + 0.5) % 16
    return _CARDINALS[index]


def wind_text(speed_mps: float | None, degrees: float | None, units: str) -> str:
    """`6 mph NW` or `10 km/h NW`."""
    if speed_mps is None:
        return ""
    if units == UNITS_IMPERIAL:
        value, unit = speed_mps * 2.2369362920544, "mph"
    else:
        value, unit = speed_mps * 3.6, "km/h"
    text = f"{int(math.floor(value + 0.5))} {unit}"
    point = cardinal(degrees)
    return f"{text} {point}" if point else text


def pressure_text(hpa: float | None, units: str) -> str:
    """`30.08 inHg` or `1018 hPa`."""
    if hpa is None:
        return ""
    if units == UNITS_IMPERIAL:
        return f"{hpa * 0.029529983071445:.2f} inHg"
    return f"{int(math.floor(hpa + 0.5))} hPa"


def distance_text(metres: float | None, units: str) -> str:
    """`10 mi` or `16 km`."""
    if metres is None:
        return ""
    if units == UNITS_IMPERIAL:
        return f"{int(math.floor(metres / 1609.344 + 0.5))} mi"
    return f"{int(math.floor(metres / 1000.0 + 0.5))} km"


def precipitation_amount_text(millimetres: float | None, units: str) -> str:
    """`0.1 in` or `2 mm`. A trace still reads as a trace, not as nothing."""
    if millimetres is None:
        return ""
    if units == UNITS_IMPERIAL:
        inches = millimetres / 25.4
        return f"{inches:.2f} in" if inches < 0.1 else f"{inches:.1f} in"
    if millimetres and millimetres < 1:
        return f"{millimetres:.1f} mm"
    return f"{int(math.floor(millimetres + 0.5))} mm"


def percent_text(fraction: float | None) -> str:
    """A whole percent, from a figure already expressed as a percentage."""
    if fraction is None:
        return ""
    return f"{int(math.floor(fraction + 0.5))}%"


# ── Wording ──────────────────────────────────────────────────────────────

UV_WORDS = (
    (2, "Low"),
    (5, "Moderate"),
    (7, "High"),
    (10, "Very High"),
)
UV_EXTREME = "Extreme"


def uv_word(index: float | None) -> str:
    """Low <=2 · Moderate 3-5 · High 6-7 · Very High 8-10 · Extreme >=11."""
    if index is None:
        return ""
    value = int(math.floor(index + 0.5))
    for limit, word in UV_WORDS:
        if value <= limit:
            return word
    return UV_EXTREME


def uv_text(index: float | None) -> str:
    """`7 · High`."""
    if index is None:
        return ""
    return f"{int(math.floor(index + 0.5))} · {uv_word(index)}"


TREND_RISING = "Rising"
TREND_STEADY = "Steady"
TREND_FALLING = "Falling"

# A barometer is called rising or falling on about a hectopascal over three
# hours; anything less is the same weather with noise on top.
TREND_THRESHOLD_HPA = 1.0


def pressure_trend(now_hpa: float | None, later_hpa: float | None) -> str:
    if now_hpa is None or later_hpa is None:
        return ""
    delta = later_hpa - now_hpa
    if delta >= TREND_THRESHOLD_HPA:
        return TREND_RISING
    if delta <= -TREND_THRESHOLD_HPA:
        return TREND_FALLING
    return TREND_STEADY


def clock_text(moment: datetime, *, twelve_hour: bool) -> str:
    """`6:14 AM` or `06:14`."""
    if twelve_hour:
        hour = moment.hour % 12 or 12
        suffix = "AM" if moment.hour < 12 else "PM"
        return f"{hour}:{moment.minute:02d} {suffix}"
    return f"{moment.hour:02d}:{moment.minute:02d}"


def hour_label(moment: datetime, *, twelve_hour: bool) -> str:
    """`9AM`, `3PM`, or `15`. No minutes, no space."""
    if twelve_hour:
        hour = moment.hour % 12 or 12
        return f"{hour}{'AM' if moment.hour < 12 else 'PM'}"
    return f"{moment.hour:02d}"


# ── Arithmetic the design asks for ───────────────────────────────────────

RANGE_TRACK_FLOOR_PERCENT = 6.0


def range_track(low: float, high: float, week_low: float,
                week_high: float) -> tuple[float, float]:
    """Where one day's range sits on the week's scale, as (left %, width %).

    Every one of the seven tracks shares one scale, which is the only reason a
    list of fourteen numbers deserves a graphic: the cold day is visibly left
    of the others without reading a digit. A per-row scale would look the same
    and mean nothing.

    The 6% floor keeps a day whose high equals its low from vanishing, and the
    result is clamped so that floor cannot push a bar past the end.
    """
    span = week_high - week_low
    if span <= 0:
        return 0.0, 100.0
    left = (low - week_low) / span * 100.0
    width = (high - low) / span * 100.0
    width = max(width, RANGE_TRACK_FLOOR_PERCENT)
    left = max(0.0, min(left, 100.0 - width))
    return left, width


def week_range(lows: Sequence[float], highs: Sequence[float]) -> tuple[float, float]:
    if not lows or not highs:
        return 0.0, 1.0
    return min(lows), max(highs)


def curve_points(values: Sequence[float], width: float, height: float,
                 pad_top: float, pad_bottom: float) -> tuple[tuple[float, float], ...]:
    """The temperature curve's points.

    The vertical scale is this city's own twelve-hour minimum to maximum, not
    an absolute range: the curve is about shape, not altitude.
    """
    count = len(values)
    if count == 0:
        return ()
    if count == 1:
        return ((0.0, pad_top),)
    low, high = min(values), max(values)
    span = max(1e-9, high - low)
    usable = height - pad_top - pad_bottom
    step = width / (count - 1)
    return tuple(
        (index * step, pad_top + (high - value) / span * usable)
        for index, value in enumerate(values)
    )


def label_stride(count: int, pixels_per_point: float) -> int:
    """How many points to skip between temperature labels.

    Twelve labels is a picket fence and six is readable, so every other point
    is the default. Where a point is narrower than a label the stride grows
    instead of the layout changing.
    """
    if count <= 0:
        return 1
    if pixels_per_point >= 30.0:
        return 2
    if pixels_per_point >= 20.0:
        return 3
    return 4


PRECIPITATION_STUB_PERCENT = 4.0


def precipitation_bar(value: float | None, scale_maximum: float) -> tuple[float, bool]:
    """A bar's height as a percentage of the row, and whether it is a stub.

    A zero hour draws a visible floor so the row reads as a scale rather than
    as missing data.
    """
    if not value or value <= 0 or scale_maximum <= 0:
        return PRECIPITATION_STUB_PERCENT, True
    return max(PRECIPITATION_STUB_PERCENT, min(100.0, value / scale_maximum * 100.0)), False


# ── What a city is ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Place:
    uid: str
    name: str
    region: str = ""
    country: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    timezone: str = ""

    @property
    def subtitle(self) -> str:
        return ", ".join(part for part in (self.region, self.country) if part)


@dataclass(frozen=True)
class HourPoint:
    time: str
    temperature_c: float
    symbol: str
    precipitation_probability: float | None = None
    precipitation_mm: float | None = None


@dataclass(frozen=True)
class DayPoint:
    date: str
    symbol: str
    high_c: float
    low_c: float


@dataclass(frozen=True)
class Current:
    temperature_c: float
    symbol: str
    apparent_c: float | None = None
    humidity: float | None = None
    wind_mps: float | None = None
    wind_degrees: float | None = None
    uv_index: float | None = None
    visibility_m: float | None = None
    pressure_hpa: float | None = None
    pressure_trend: str = ""
    precipitation_probability: float | None = None
    precipitation_mm: float | None = None
    high_c: float | None = None
    low_c: float | None = None
    sunrise: str = ""
    sunset: str = ""


@dataclass(frozen=True)
class CityForecast:
    place: Place
    fetched_at: str
    current: Current
    hours: tuple[HourPoint, ...] = ()
    days: tuple[DayPoint, ...] = ()

    def to_dict(self) -> dict:
        return {
            "place": asdict(self.place),
            "fetched_at": self.fetched_at,
            "current": asdict(self.current),
            "hours": [asdict(hour) for hour in self.hours],
            "days": [asdict(day) for day in self.days],
            "version": CACHE_VERSION,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CityForecast":
        return cls(
            place=Place(**payload["place"]),
            fetched_at=str(payload.get("fetched_at", "")),
            current=Current(**payload["current"]),
            hours=tuple(HourPoint(**hour) for hour in payload.get("hours", ())),
            days=tuple(DayPoint(**day) for day in payload.get("days", ())),
        )

    def age_seconds(self, now: datetime | None = None) -> float | None:
        """How old this payload is. The model keeps staleness honest; nothing
        in the design renders it yet, and nothing here should invent a place
        to put it."""
        moment = parse_time(self.fetched_at)
        if moment is None:
            return None
        reference = now or datetime.now(timezone.utc)
        return (reference - moment).total_seconds()


def parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ── The nine detail rows ─────────────────────────────────────────────────

DETAIL_SUNRISE = "sunrise"
DETAIL_SUNSET = "sunset"


@dataclass(frozen=True)
class DetailRow:
    key: str
    value: str
    glyph: str = ""


def detail_rows(current: Current, units: str, *, twelve_hour: bool,
                sunrise: datetime | None = None,
                sunset: datetime | None = None) -> tuple[DetailRow, ...]:
    """The detail grid, in order, with the rows we have no figure for dropped.

    Nine is the full set. Eight is fine. What is never fine is an em dash, a
    zero standing in for nothing, or a "feels like" invented from a formula
    the provider did not give us.
    """
    candidates: tuple[tuple[str, str, str], ...] = (
        ("Feels like", temperature_text(current.apparent_c, units), ""),
        ("Humidity", percent_text(current.humidity), ""),
        ("Wind", wind_text(current.wind_mps, current.wind_degrees, units), ""),
        ("Precipitation", _precipitation_value(current, units), ""),
        ("UV index", uv_text(current.uv_index), ""),
        ("Visibility", distance_text(current.visibility_m, units), ""),
        ("Pressure", _pressure_value(current, units), ""),
        ("Sunrise", clock_text(sunrise, twelve_hour=twelve_hour) if sunrise else "",
         DETAIL_SUNRISE),
        ("Sunset", clock_text(sunset, twelve_hour=twelve_hour) if sunset else "",
         DETAIL_SUNSET),
    )
    return tuple(DetailRow(key, value, glyph)
                 for key, value, glyph in candidates if value)


def _precipitation_value(current: Current, units: str) -> str:
    """A probability where the provider has one, otherwise an amount.

    A dry hour in amount mode has no row: "0.00 in" is a zero dressed up as a
    measurement, and this grid does not print zeros. A genuine 0% probability
    is a different thing — it is a forecast — and it stays.
    """
    if current.precipitation_probability is not None:
        return percent_text(current.precipitation_probability)
    if not current.precipitation_mm or current.precipitation_mm <= 0:
        return ""
    return precipitation_amount_text(current.precipitation_mm, units)


def _pressure_value(current: Current, units: str) -> str:
    text = pressure_text(current.pressure_hpa, units)
    if text and current.pressure_trend:
        return f"{text} · {current.pressure_trend}"
    return text


def column_split(count: int) -> int:
    """How many of `count` rows go in the left column, filling down.

    The first row of each column carries no top border, which is why this is a
    number the renderer has to be told rather than one it can assume.
    """
    return (count + 1) // 2


# ── The precipitation series ─────────────────────────────────────────────

PRECIP_PROBABILITY = "probability"
PRECIP_AMOUNT = "amount"


def precipitation_mode(hours: Iterable[HourPoint]) -> str:
    """Which figure the bars are carrying.

    Our provider publishes a probability of precipitation only where its
    probabilistic model reaches; elsewhere all it gives is an amount. The bars
    therefore say which one they are showing rather than printing "% chance"
    over a number that is not a percentage.
    """
    for hour in hours:
        if hour.precipitation_probability is not None:
            return PRECIP_PROBABILITY
    return PRECIP_AMOUNT


def precipitation_series(hours: Sequence[HourPoint]) -> tuple[str, tuple[float, ...]]:
    mode = precipitation_mode(hours)
    if mode == PRECIP_PROBABILITY:
        return mode, tuple(float(hour.precipitation_probability or 0.0) for hour in hours)
    return mode, tuple(float(hour.precipitation_mm or 0.0) for hour in hours)


def precipitation_label(mode: str, value: float, hour: str, units: str) -> str:
    """What a screen reader is told about one bar. It has no column to look at."""
    if mode == PRECIP_PROBABILITY:
        return f"{hour}, {percent_text(value)} chance of precipitation"
    if value <= 0:
        return f"{hour}, no precipitation expected"
    return f"{hour}, {precipitation_amount_text(value, units)} of precipitation expected"


# ── The one sentence under the temperature ───────────────────────────────

_PART_OF_DAY = ((11, "morning"), (17, "afternoon"), (21, "evening"))


def part_of_day(moment: datetime) -> str:
    for limit, word in _PART_OF_DAY:
        if moment.hour < limit:
            return word
    return "night"


_ARRIVALS = {
    SKY_RAIN: "Rain arriving",
    SKY_CLOUDY: "Clouding over",
    SKY_PARTLY: "Clouds breaking",
    SKY_CLEAR: "Clearing",
}


def summary_sentence(condition: Condition, hours: Sequence[HourPoint],
                     now: datetime, *, twelve_hour: bool, include_first: bool = False) -> str:
    """One sentence, written from the data, with the time of day in it.

    The simulator's placeholder says "Sunny for the rest of the day" at nine at
    night. This says what actually changes, and says nothing at all when there
    is nothing worth saying. include_first covers a forecast that begins with
    a future hour rather than the current hour.
    """
    if not hours:
        return ""
    for hour in hours if include_first else hours[1:]:
        moment = parse_time(hour.time)
        if moment is None:
            continue
        sky = condition_for(hour.symbol).sky
        if sky != condition.sky:
            verb = _ARRIVALS.get(sky)
            if not verb:
                return ""
            return f"{verb} around {clock_text(moment, twelve_hour=twelve_hour)}."
    word = part_of_day(now)
    if word == "night":
        return f"{condition.text} overnight."
    return f"{condition.text} for the rest of the {word}."


# ── The cache ────────────────────────────────────────────────────────────
# Launch is instant and the app is fully usable with no network, which means
# the cache is the source the view renders from and the network is what
# quietly replaces it.

CACHE_VERSION = 1


def cache_directory(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    root = Path(env.get("XDG_CACHE_HOME", "")) if env.get("XDG_CACHE_HOME") else \
        Path(env.get("HOME", str(Path.home()))) / ".cache"
    return root / "prairie-weather"


def cache_path(uid: str, root: Path) -> Path:
    safe = "".join(character for character in uid if character.isalnum() or character in "-_")
    return Path(root) / f"{safe or 'place'}.json"


def write_cache(forecast: CityForecast, root: Path) -> Path:
    """Write a city's whole payload, atomically, private to the person."""
    path = cache_path(forecast.place.uid, root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".forecast-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(forecast.to_dict(), stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def read_cache(uid: str, root: Path) -> CityForecast | None:
    """The last payload for a city, or nothing. A damaged file is nothing."""
    try:
        payload = json.loads(cache_path(uid, root).read_text(encoding="utf-8"))
        return CityForecast.from_dict(payload)
    except (OSError, KeyError, TypeError, ValueError):
        return None


def forget_cache(uid: str, root: Path) -> None:
    cache_path(uid, root).unlink(missing_ok=True)


def rename_place(forecast: CityForecast, place: Place) -> CityForecast:
    return replace(forecast, place=place)
