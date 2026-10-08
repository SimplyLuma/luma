# SPDX-License-Identifier: Apache-2.0
"""Studio v71's WP2/LPLACES sample, read-only on disk and mutable in memory.

v71 adds the phone's data: WXAL's warnings and Calendar's day (dayEvs), both
read from the fixture file; What's next, Your day and the tiles are computed
from the same formulas as wxBodyPhone, so they cannot disagree with the hours.

No PlaceStore, cache, settings, libgweather, network or live data is opened.
The sample's formulas are copied from wxBody, not used for real forecasts.
LUMA_WEATHER_FIXTURE_MINUTE pins Studio's local clock for conformance states.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
import os
from pathlib import Path

from .weather_data import Alert, Change, Day, Hour, Metric, Plan, Snapshot, Tile, js_round
from .weather_model import Place


def hm(minute: int, *, minutes: bool = True) -> str:
    hour, remainder = divmod(minute % 1440, 60)
    suffix = f":{remainder:02}" if minutes and remainder else ""
    return f"{hour % 12 or 12}{suffix} {'AM' if hour < 12 else 'PM'}"


def temperature_at(place: dict, minute: int) -> float:
    rain = place.get("rain")
    return place["base"] + place["amp"] * math.sin((minute / 60 - 9) / 24 * 2 * math.pi) - (
        4 if rain and rain[0] <= minute / 60 < rain[1] else 0)


def condition_at(place: dict, minute: int) -> str:
    rain, hour = place.get("rain"), minute / 60 % 24
    if rain and rain[0] - 1 <= hour < rain[0]:
        return "cloud"
    if rain and rain[0] <= hour < rain[1]:
        return "rain"
    if place["id"] == "tok" and 10 <= hour < 16:
        return "part"
    return "clear"


def rain_at(place: dict, minute: int) -> int:
    rain, hour = place.get("rain"), minute / 60 % 24
    return js_round(40 + 40 * math.sin((hour - rain[0] + .5) / (rain[1] - rain[0]) * math.pi)) \
        if rain and rain[0] <= hour < rain[1] else 0


CONDITION_TEXT = {"clear": "Clear", "part": "Partly cloudy", "cloud": "Cloudy", "rain": "Light rain"}
DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def night_at(place: dict, minute: int) -> bool:
    return minute < place["rise"] or minute > place["set"]


def sky_at(place: dict, minute: int) -> str:
    if condition_at(place, minute) in ("rain", "cloud"):
        return "rain"
    if minute < place["rise"] - 25 or minute > place["set"] + 25:
        return "night"
    if min(abs(minute - place["rise"]), abs(minute - place["set"])) < 75:
        return "dusk"
    return "day"


@dataclass(frozen=True)
class SearchPlace:
    name: str
    subtitle: str
    uid: str
    sample: dict


class FixtureSource:
    def __init__(self, path: Path, *, minute: int | None = None, selected: str | None = None) -> None:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        self.minute = int(document["minute"] if minute is None else minute)
        self.today = date.fromisoformat(document["date"])
        self.selected = selected or document["selected"]
        self.unit = document.get("unit", "F")
        self.samples = {raw["id"]: raw.copy() for raw in document["places"]}
        self.alerts = {key: tuple(Alert(a["t"], a["when"], a["d"], a["src"], a.get("starts", ""), a.get("lvl", "warn"))
                                  for a in value) for key, value in document.get("alerts", {}).items()}
        self.events = tuple(document.get("events", ()))
        self.search_places = tuple(SearchPlace(f"{p['n']}, {p['r']}", p["zip"], p["id"], p)
                                   for p in document["search"])

    def load(self) -> tuple[Place, ...]:
        return tuple(self._place(p) for p in self.samples.values())

    @staticmethod
    def _place(p: dict) -> Place:
        # Coordinates are not consumed in fixture mode.
        return Place(p["id"], p["n"], p["r"])

    def search(self, query: str) -> tuple[SearchPlace, ...]:
        text = query.strip().casefold()
        return tuple(p for p in self.search_places
                     if text and text in f"{p.name} {p.subtitle}".casefold())[:5]

    def add(self, result: SearchPlace) -> Place:
        g = result.sample
        if result.uid not in self.samples:
            h = g["h"]
            self.samples[result.uid] = dict(id=g["id"], n=g["n"], r=g["r"], off=g["off"],
                base=60 + h % 17, amp=8, rain=None if h % 3 else [15, 18], rise=400 + h % 60,
                set=1120 + h % 50, aq=20 + h % 30, uv=3 + h % 5, wind=[4 + h % 9, h], hum=40 + h % 35)
        self.selected = result.uid
        return self._place(self.samples[result.uid])

    def snapshot(self, place: Place) -> Snapshot:
        p = self.samples[place.uid]
        now = (self.minute + p["off"] * 60 + 1440) % 1440
        start = now // 60 * 60
        def deg(value: float) -> str:
            return f"{js_round(value if self.unit == 'F' else (value - 32) * 5 / 9)}°"
        temps = [temperature_at(p, k * 60) for k in range(24)]
        high, low = max(temps), min(temps)
        rain, night = p.get("rain"), night_at(p, now)
        if rain and now / 60 < rain[1]:
            sentence = (f"Dry for now. Light rain from around {hm(rain[0] * 60)}, gone by {hm(rain[1] * 60)}."
                        if now / 60 < rain[0] else f"Light rain until about {hm(rain[1] * 60)}, then clearing.")
        else:
            sentence = "A clear, quiet night. Cool by morning." if night else "Clear and dry all day. A cool evening."
        hours = []
        for i in range(25):
            m = (start + i * 60) % 1440
            r = rain_at(p, m)
            hours.append(Hour("Now" if not i else hm(m, minutes=False).replace(" ", ""),
                              deg(temperature_at(p, m)), condition_at(p, m), night_at(p, m), f"{r}%" if r else "",
                              CONDITION_TEXT[condition_at(p, m)], "Now" if not i else hm(m, minutes=False)))
        conditions = {
            "oak": ["rain", "part", "clear", "clear", "cloud", "clear", "clear", "part", "rain", "clear"],
            "tok": ["rain", "rain", "part", "cloud", "clear", "clear", "part", "rain", "cloud", "clear"],
        }.get(p["id"], ["clear", "clear", "part", "clear", "clear", "clear", "part", "clear", "clear", "cloud"])
        days = []
        for k, shift in enumerate((0, 3, -2, -4, 1, 5, 6, 2, -1, 0)):
            lo, hi = p["base"] - p["amp"] + shift - 1, p["base"] + p["amp"] + shift
            r = (40, 70, 55)[k % 3] if conditions[k] == "rain" else 0
            days.append(Day("Today" if not k else (self.today + timedelta(days=k)).strftime("%a"),
                            deg(lo), deg(hi), conditions[k], lo, hi, f"{r}%" if r else ""))
        directions = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        sun_title, sun_time, previous = ("Sunrise", p["rise"], p["set"]) if night else ("Sunset", p["set"], p["rise"])
        metrics = (
            Metric("rain", "Rain", "umbrella", f"{max(rain_at(p, k * 60) for k in range(24))}%",
                   f"Light rain {hm(rain[0] * 60)} to {hm(rain[1] * 60)}." if rain else "None expected today."),
            Metric("wind", "Wind", "wind", str(p["wind"][0]),
                   f"mph from the {directions[js_round(p['wind'][1] / 45) % 8]}", p["wind"][1]),
            Metric("uv", "UV index", "sun", str(p["uv"]), "High around midday" if p["uv"] >= 6 else "Moderate", p["uv"], 11),
            Metric("sun", sun_title, sun_title.lower(), hm(sun_time),
                   f"{'Sunset' if night else 'Sunrise'} was {hm(previous)}",
                   None if night else max(0, min(1, (now - p["rise"]) / (p["set"] - p["rise"])))),
            Metric("humidity", "Humidity", "droplets", f"{p['hum']}%", f"Dew point {deg(p['base'] - 12)}"),
            Metric("air", "Air quality", "leaf", str(p["aq"]), "Good. Fine for everyone." if p["aq"] < 50 else "Moderate", p["aq"], 150),
        )
        hm_short = lambda m: hm(m, minutes=False)  # noqa: E731  v71 `hh`
        # What's next (v71 `nx`): sky changes in the next 12 hours, then the sun; the first three by time.
        upcoming, prev = [], condition_at(p, now)
        for i in range(1, 13):
            m = (start + i * 60) % 1440
            c = condition_at(p, m)
            if c != prev:
                upcoming.append((m, c, i))
                prev = c
        until = ((p["rise"] if night else p["set"]) - now + 1440) % 1440
        if until < 720:
            upcoming.append(((now + until) % 1440, "rise" if night else "set", js_round(until / 60)))
        upcoming.sort(key=lambda item: item[2])
        changes = tuple(Change("Soon" if h < 1 else f"In {h} h",
                               {"rise": "Sunrise", "set": "Sunset"}.get(c, CONDITION_TEXT.get(c, "")), hm(m),
                               "" if c in ("rise", "set") else c, night_at(p, m),
                               {"rise": "sunrise", "set": "sunset"}.get(c, ""))
                        for m, c, h in upcoming[:3])
        alerts = self.alerts.get(p["id"], ())
        plans = tuple(Plan(e["t"], hm(e["s"]), condition_at(p, e["s"]), night_at(p, e["s"]),
                           deg(temperature_at(p, e["s"])), condition_at(p, e["s"]) == "rain")
                      for e in sorted(self.events, key=lambda e: e["s"])
                      if p.get("me") and e["e"] > now)[:3]
        wettest = max(rain_at(p, k * 60) for k in range(24))
        tiles = (
            Tile("rain", "Rain", "umbrella", f"{wettest}%" if rain else "0%",
                 f"{hm_short(rain[0] * 60)} to {hm_short(rain[1] * 60)}" if rain else "None today"),
            Tile("wind", "Wind", "wind", f"{p['wind'][0]} mph", f"from the {DIRECTIONS[js_round(p['wind'][1] / 45) % 8]}"),
            Tile("uv", "UV", "sun", str(p["uv"]), "High at midday" if p["uv"] >= 6 else "Moderate"),
            Tile("air", "Air", "leaf", str(p["aq"]), "Good" if p["aq"] < 50 else "Moderate"),
            Tile("sun", "Sunrise" if night else "Sunset", "sunrise" if night else "sunset",
                 hm(p["rise"] if night else p["set"])),
            Tile("humidity", "Humidity", "droplets", f"{p['hum']}%", f"Dew point {deg(p['base'] - 12)}"),
        )
        kind = condition_at(p, now)
        glyph = ("moon" if night else "sun") if kind == "clear" else "ncloud" if kind == "part" and night else kind
        return Snapshot(place, "My location" if p.get("me") else hm(now), deg(temperature_at(p, now)),
                        CONDITION_TEXT[kind],
                        glyph, sky_at(p, now), deg(high), deg(low), sentence, tuple(hours), tuple(days), metrics,
                        bool(p.get("me")), temperature_at(p, now), alerts=alerts, changes=changes,
                        tiles=tiles, plans=plans, clock=hm(now))


def fixture_from_environment() -> FixtureSource | None:
    path = os.environ.get("LUMA_WEATHER_FIXTURE")
    if not path:
        return None
    minute = os.environ.get("LUMA_WEATHER_FIXTURE_MINUTE")
    return FixtureSource(Path(path), minute=int(minute) if minute is not None else None,
                         selected=os.environ.get("LUMA_WEATHER_SELECTED"))
