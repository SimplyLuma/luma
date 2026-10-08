#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Weather's arithmetic, without a display.

Everything here is what the application decides before it draws anything: what
a provider code means, whether it is night *at the city*, what a figure reads
as in a person's units, where a day's range sits on the week's scale, which
rows survive a provider that did not send them, and what the cache keeps.

No GTK, no network: the forecast payload is a fixture in MET Norway's own
shape, captured from `locationforecast/2.0/complete`.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prairie_apps.weather_backend import (  # noqa: E402
    PlaceStore, local_now, parse_coordinates, parse_forecast, needs_refresh, new_place,
)
from prairie_apps.weather_model import (  # noqa: E402
    CityForecast, Current, GLYPH_MOON, GLYPH_SUN, HourPoint, PALETTE_INK,
    PRECIP_AMOUNT, PRECIP_PROBABILITY, Place, SKY_CLEAR, SKY_NIGHT, SKY_RAIN,
    UNITS_AUTOMATIC, UNITS_IMPERIAL, UNITS_METRIC, cardinal, column_split,
    condition_for, curve_points, detail_rows, distance_text, glyph_at,
    hour_label, is_night, label_stride, measurement_from_locale,
    palette_for_surface, precipitation_amount_text, precipitation_bar,
    precipitation_label, precipitation_series, pressure_text, pressure_trend,
    range_track, read_cache, resolve_units, sky_at, summary_sentence,
    temperature_text, uv_text, uv_word, week_range, wind_text, write_cache,
)

checks: list[str] = []


def check(name: str) -> None:
    checks.append(name)
    print(f"  ok  {name}")


# ── Condition mapping ────────────────────────────────────────────────────

assert condition_for("clearsky_day").sky == SKY_CLEAR
assert condition_for("clearsky_night").text == "Clear"
assert condition_for("clearsky_polartwilight").glyph == GLYPH_SUN
assert condition_for("partlycloudy_day").sky == "partly"
assert condition_for("cloudy").sky == "cloudy"
# Fog is a cloudy sky with its own glyph, never a sixth gradient.
assert condition_for("fog").sky == "cloudy" and condition_for("fog").glyph == "fog"
# Every precipitation code is the rain sky; snow and thunder differ by glyph.
for code in ("lightrain", "rain", "heavyrainshowers_day", "sleet", "lightsnow",
             "snowshowers_night", "rainandthunder"):
    assert condition_for(code).sky == SKY_RAIN, code
assert condition_for("lightsnow").glyph == "snow"
assert condition_for("rainandthunder").glyph == "thunder"
assert condition_for("rainshowersandthunder_day").text == "Thundery Showers"
assert condition_for("lightrain").text == "Light Rain"
# Anything we have never seen is the quietest wrong answer, not a crash.
assert condition_for("meteor_shower").sky == "cloudy"
assert condition_for("").glyph == "cloud"
check("condition mapping: five skies, seven glyphs, unknown codes are cloudy")


# ── Night, at the city, not at the host ──────────────────────────────────

london = Place(uid="l", name="London", latitude=51.5074, longitude=-0.1278,
               timezone="Europe/London")
cupertino = Place(uid="c", name="Cupertino", latitude=37.323, longitude=-122.0322,
                  timezone="America/Los_Angeles")
tokyo = Place(uid="t", name="Tokyo", latitude=35.6762, longitude=139.6503,
              timezone="Asia/Tokyo")

instant = datetime(2026, 9, 11, 22, 0, tzinfo=timezone.utc)  # 23:00 London, 15:00 Cupertino
assert is_night(local_now(london, instant)) is True
assert is_night(local_now(cupertino, instant)) is False
assert is_night(local_now(tokyo, instant)) is False  # 07:00 the next day
assert local_now(tokyo, instant).hour == 7 and not is_night(local_now(tokyo, instant))
check("night is local to the city: London dark while Cupertino is not, same instant")

clear = condition_for("clearsky_day")
assert sky_at(clear.sky, True) == SKY_NIGHT
assert glyph_at(clear.glyph, True) == GLYPH_MOON
assert sky_at("partly", True) == SKY_NIGHT
# A cloudy night is still a cloud, and a rainy one still rain.
assert sky_at("cloudy", True) == "cloudy" and sky_at(SKY_RAIN, True) == SKY_RAIN
assert glyph_at("cloud", True) == "cloud" and glyph_at("rain", True) == "rain"
# The words never change. A clear night reads "Clear".
assert clear.text == "Clear"
check("night changes the sky and the glyph, never the condition text")

# A place with no time zone still gets a plausible local clock from its
# longitude rather than silently using the host's.
unzoned = Place(uid="u", name="Somewhere", latitude=0.0, longitude=-120.0)
assert local_now(unzoned, instant).hour == 14
check("a place with no time zone falls back to solar time, not the host clock")


# ── Units ────────────────────────────────────────────────────────────────

assert resolve_units(UNITS_AUTOMATIC, UNITS_IMPERIAL) == UNITS_IMPERIAL
assert resolve_units(UNITS_METRIC, UNITS_IMPERIAL) == UNITS_METRIC
assert resolve_units(UNITS_IMPERIAL, UNITS_METRIC) == UNITS_IMPERIAL
assert measurement_from_locale("en_US.UTF-8") == UNITS_IMPERIAL
assert measurement_from_locale("en_GB.UTF-8") == UNITS_METRIC
assert measurement_from_locale("C") == UNITS_METRIC
check("units: automatic follows the system, the other two override it")

assert temperature_text(23.3, UNITS_IMPERIAL) == "74°"
assert temperature_text(23.3, UNITS_METRIC) == "23°"
assert temperature_text(-0.4, UNITS_METRIC) == "0°"
assert temperature_text(None, UNITS_METRIC) == ""
# The degree sign never carries a letter, and never a decimal place.
for units in (UNITS_METRIC, UNITS_IMPERIAL):
    for value in (0.0, -17.8, 35.55, 99.4):
        text = temperature_text(value, units)
        assert text.endswith("°") and "." not in text and "C" not in text and "F" not in text
assert wind_text(2.7, 315, UNITS_IMPERIAL) == "6 mph NW"
assert wind_text(2.7, 315, UNITS_METRIC) == "10 km/h NW"
assert wind_text(None, 315, UNITS_METRIC) == ""
assert cardinal(0) == "N" and cardinal(180) == "S" and cardinal(350) == "N"
assert pressure_text(1018.7, UNITS_IMPERIAL) == "30.08 inHg"
assert pressure_text(1018.7, UNITS_METRIC) == "1019 hPa"
assert distance_text(16093.4, UNITS_IMPERIAL) == "10 mi"
assert distance_text(16093.4, UNITS_METRIC) == "16 km"
assert precipitation_amount_text(2.0, UNITS_METRIC) == "2 mm"
assert precipitation_amount_text(0.4, UNITS_METRIC) == "0.4 mm"
assert precipitation_amount_text(2.54, UNITS_IMPERIAL) == "0.1 in"
check("conversions: 74° / 23°, 6 mph NW, 30.08 inHg, 10 mi, 2 mm")

assert hour_label(datetime(2026, 9, 11, 15, 0), twelve_hour=True) == "3PM"
assert hour_label(datetime(2026, 9, 11, 9, 0), twelve_hour=True) == "9AM"
assert hour_label(datetime(2026, 9, 11, 15, 0), twelve_hour=False) == "15"
check("hour labels: 9AM, 3PM, no minutes, 24-hour outside an imperial locale")


# ── UV wording ───────────────────────────────────────────────────────────

assert [uv_word(value) for value in (0, 1, 2)] == ["Low", "Low", "Low"]
assert [uv_word(value) for value in (3, 4, 5)] == ["Moderate"] * 3
assert [uv_word(value) for value in (6, 7)] == ["High", "High"]
assert [uv_word(value) for value in (8, 9, 10)] == ["Very High"] * 3
assert [uv_word(value) for value in (11, 15)] == ["Extreme", "Extreme"]
assert uv_word(2.4) == "Low" and uv_word(2.6) == "Moderate"
assert uv_text(7.0) == "7 · High"
assert uv_word(None) == "" and uv_text(None) == ""
check("UV wording: Low <=2, Moderate 3-5, High 6-7, Very High 8-10, Extreme >=11")


# ── The shared-scale range tracks ────────────────────────────────────────

lows = (10.0, 8.0, 2.0, 9.0, 11.0, 12.0, 10.0)
highs = (18.0, 17.0, 6.0, 16.0, 19.0, 20.0, 18.0)
week_low, week_high = week_range(lows, highs)
assert (week_low, week_high) == (2.0, 20.0)
tracks = [range_track(low, high, week_low, week_high) for low, high in zip(lows, highs)]
# The cold day sits visibly left of every other day, which is the only reason
# the graphic exists.
assert tracks[2][0] == 0.0
assert all(track[0] > tracks[2][0] for index, track in enumerate(tracks) if index != 2)
first_left, first_width = tracks[0]
assert abs(first_left - (10 - 2) / 18 * 100) < 1e-9
assert abs(first_width - (18 - 10) / 18 * 100) < 1e-9
# A day whose high equals its low keeps a visible 6% bar...
floor_left, floor_width = range_track(20.0, 20.0, 2.0, 20.0)
assert floor_width == 6.0
# ...and the floor never pushes a bar past the end of the track.
assert floor_left + floor_width == 100.0
assert range_track(5.0, 5.0, 5.0, 5.0) == (0.0, 100.0)
check("range tracks: one shared scale, a 6% floor, clamped inside the track")


# ── The curve ────────────────────────────────────────────────────────────

values = [14.0, 15.0, 17.0, 19.0, 20.0, 19.0, 18.0, 17.0, 16.0, 15.0, 14.0, 13.0]
points = curve_points(values, 660.0, 116.0, 30.0, 10.0)
assert len(points) == 12
assert points[0][0] == 0.0 and abs(points[-1][0] - 660.0) < 1e-9
# The scale is this city's own minimum to maximum: the warmest hour sits at the
# top padding and the coldest on the bottom one.
assert abs(points[4][1] - 30.0) < 1e-9
assert abs(points[-1][1] - 106.0) < 1e-9
assert label_stride(12, 55.0) == 2 and label_stride(12, 26.0) == 3
check("curve: twelve points across the card, scaled to the city's own range")


# ── The precipitation series ─────────────────────────────────────────────

with_probability = (HourPoint("2026-09-11T15:00:00+01:00", 12.0, "rain",
                              precipitation_probability=40.0, precipitation_mm=0.4),)
amount_only = (HourPoint("2026-09-11T15:00:00-07:00", 22.0, "clearsky_day",
                         precipitation_probability=None, precipitation_mm=0.0),)
assert precipitation_series(with_probability)[0] == PRECIP_PROBABILITY
assert precipitation_series(amount_only)[0] == PRECIP_AMOUNT
assert precipitation_label(PRECIP_PROBABILITY, 40.0, "3PM", UNITS_METRIC) == \
    "3PM, 40% chance of precipitation"
assert precipitation_label(PRECIP_AMOUNT, 0.0, "3PM", UNITS_METRIC) == \
    "3PM, no precipitation expected"
# A zero hour is a visible stub, so the row reads as a scale, not as no data.
assert precipitation_bar(0.0, 100.0) == (4.0, True)
assert precipitation_bar(None, 100.0) == (4.0, True)
assert precipitation_bar(50.0, 100.0) == (50.0, False)
assert precipitation_bar(2.0, 100.0)[0] == 4.0  # never below the stub
check("precipitation: probability where the provider has it, amount where it does not")


# ── Missing figures drop their row ───────────────────────────────────────

full = Current(
    temperature_c=23.3, symbol="clearsky_day", apparent_c=24.4, humidity=44.0,
    wind_mps=2.7, wind_degrees=315.0, uv_index=7.0, visibility_m=16093.4,
    pressure_hpa=1018.7, pressure_trend="Rising", precipitation_probability=20.0,
    high_c=25.6, low_c=16.1, sunrise="2026-09-12T06:47:00-07:00",
    sunset="2026-09-12T19:20:00-07:00",
)
sunrise = datetime(2026, 9, 12, 6, 47)
sunset = datetime(2026, 9, 12, 19, 20)
rows = detail_rows(full, UNITS_IMPERIAL, twelve_hour=True, sunrise=sunrise, sunset=sunset)
assert [row.key for row in rows] == [
    "Feels like", "Humidity", "Wind", "Precipitation", "UV index", "Visibility",
    "Pressure", "Sunrise", "Sunset"]
assert [row.value for row in rows][:7] == [
    "76°", "44%", "6 mph NW", "20%", "7 · High", "10 mi", "30.08 inHg · Rising"]
assert rows[7].value == "6:47 AM" and rows[8].value == "7:20 PM"
assert rows[7].glyph == "sunrise" and rows[8].glyph == "sunset"
assert column_split(9) == 5

# What our provider actually leaves out: no visibility at all, and no UV in
# some payloads. The rows go; no em dash, no zero.
thin = Current(temperature_c=12.0, symbol="cloudy", humidity=80.0, wind_mps=4.0,
               wind_degrees=270.0, pressure_hpa=1004.0, pressure_trend="Falling",
               precipitation_mm=0.0)
thin_rows = detail_rows(thin, UNITS_METRIC, twelve_hour=False)
# A dry hour in amount mode has no Precipitation row: "0.00 in" is a zero
# wearing a measurement's clothes.
assert [row.key for row in thin_rows] == ["Humidity", "Wind", "Pressure"]
wet = Current(temperature_c=12.0, symbol="lightrain", precipitation_mm=2.0)
assert [row.value for row in detail_rows(wet, UNITS_METRIC, twelve_hour=False)] == ["2 mm"]
dry_forecast = Current(temperature_c=12.0, symbol="cloudy", precipitation_probability=0.0)
assert [row.value for row in detail_rows(dry_forecast, UNITS_METRIC, twelve_hour=False)] == ["0%"]
assert all(row.value and "—" not in row.value for row in thin_rows)
# The border rule is computed from the rows actually rendered, not from nine.
assert column_split(len(thin_rows)) == 2
assert column_split(8) == 4 and column_split(7) == 4 and column_split(0) == 0
# A feels-like the provider did not send is never invented.
assert not any(row.key == "Feels like" for row in thin_rows)
check("missing figures drop their row, and the border rule follows the rows drawn")

assert pressure_trend(1010.0, 1012.0) == "Rising"
assert pressure_trend(1010.0, 1008.5) == "Falling"
assert pressure_trend(1010.0, 1010.4) == "Steady"
assert pressure_trend(None, 1010.0) == ""
check("pressure trend: rising, steady, falling on a hectopascal over three hours")


# ── Glyph palettes come from the surface ─────────────────────────────────

assert palette_for_surface("condition-tile")["cloud"] == "#ffffff"
assert palette_for_surface("selected-city-row")["cloud"] == "#ffffff"
for surface in ("city-row", "daily-row", "add-city-row", "empty-state"):
    assert palette_for_surface(surface) is PALETTE_INK, surface
    # The ink cloud is the visible one on chrome; the white one vanishes.
    assert palette_for_surface(surface)["cloud"] == "#9aa6b5"
    assert palette_for_surface(surface)["rain"] == "#5b8fd0"
try:
    palette_for_surface("wherever")
except KeyError:
    pass
else:  # pragma: no cover
    raise AssertionError("an unknown surface must not silently pick a palette")
check("glyph palette is chosen by the surface, and an unknown surface is an error")


# ── The sentence under the temperature ───────────────────────────────────

clear_hours = tuple(
    HourPoint(f"2026-09-11T{hour:02d}:00:00-07:00", 20.0, "clearsky_day")
    for hour in range(9, 21))
sentence = summary_sentence(condition_for("clearsky_day"), clear_hours,
                            datetime(2026, 9, 11, 9, 0), twelve_hour=True)
assert sentence == "Clear for the rest of the morning."
turning = clear_hours[:3] + tuple(
    HourPoint(f"2026-09-11T{hour:02d}:00:00-07:00", 18.0, "lightrain")
    for hour in range(12, 21))
assert summary_sentence(condition_for("clearsky_day"), turning,
                        datetime(2026, 9, 11, 9, 0),
                        twelve_hour=True) == "Rain arriving around 12:00 PM."
# At nine at night it does not say "for the rest of the day".
night_sentence = summary_sentence(condition_for("clearsky_day"), clear_hours,
                                  datetime(2026, 9, 11, 21, 0), twelve_hour=True)
assert night_sentence == "Clear overnight."
assert summary_sentence(condition_for("cloudy"), (), datetime(2026, 9, 11, 9, 0),
                        twelve_hour=True) == ""
check("the summary says what changes, and says nothing when there is nothing to say")


# ── The provider payload ─────────────────────────────────────────────────
# MET Norway's own shape, from locationforecast/2.0/complete.

def entry(time, temperature, *, symbol="clearsky_day", pressure=1011.2,
          probability=None, amount=0.0, maximum=None, minimum=None):
    instant = {"air_temperature": temperature, "air_pressure_at_sea_level": pressure,
               "relative_humidity": 48.3, "wind_from_direction": 357.8,
               "wind_speed": 3.1, "apparent_air_temperature": temperature - 0.5,
               "ultraviolet_index_clear_sky": 0.4, "cloud_area_fraction": 0.0}
    details = {"precipitation_amount": amount}
    if probability is not None:
        details["probability_of_precipitation"] = probability
    data = {"instant": {"details": instant},
            "next_1_hours": {"summary": {"symbol_code": symbol}, "details": details}}
    if maximum is not None:
        data["next_6_hours"] = {"summary": {"symbol_code": symbol},
                                "details": {"air_temperature_max": maximum,
                                            "air_temperature_min": minimum}}
    return {"time": time, "data": data}


series = []
for index in range(30):
    hour = 1 + index
    day, clock = divmod(hour, 24)
    series.append(entry(f"2026-09-{12 + day:02d}T{clock:02d}:00:00Z",
                        20.0 + index % 5,
                        pressure=1011.2 + index * 0.5,
                        amount=0.2 if index % 4 == 0 else 0.0,
                        maximum=25.0, minimum=15.0))
payload = {"properties": {"timeseries": series}}
sun = {"properties": {"sunrise": {"time": "2026-09-12T06:47-07:00"},
                      "sunset": {"time": "2026-09-12T19:20-07:00"}}}
now = datetime(2026, 9, 12, 3, 30, tzinfo=timezone.utc)
forecast = parse_forecast(payload, cupertino, now=now, sun=sun)
assert len(forecast.hours) == 25
assert forecast.hours[0].time.startswith("2026-09-11T20:00")  # local, not UTC
assert forecast.current.apparent_c is not None
assert forecast.current.uv_index == 0.4
# MET's forecast carries no visibility, so the row will not be drawn.
assert forecast.current.visibility_m is None
assert forecast.current.pressure_trend == "Rising"
assert forecast.current.sunrise.startswith("2026-09-12T06:47")
assert 1 <= len(forecast.days) <= 10
assert all(day.high_c >= day.low_c for day in forecast.days)
assert precipitation_series(forecast.hours)[0] == PRECIP_AMOUNT
nordic = parse_forecast({"properties": {"timeseries": [
    entry("2026-09-12T03:00:00Z", 12.0, symbol="lightrain", probability=40.0, amount=0.4),
    entry("2026-09-12T04:00:00Z", 12.5, symbol="lightrain", probability=55.0, amount=0.6),
]}}, london, now=now)
assert precipitation_series(nordic.hours)[0] == PRECIP_PROBABILITY
assert nordic.current.precipitation_probability == 40.0
check("MET payload: twenty-five local hours, daily groups, UV, no visibility, both precip modes")


# ── The cache ────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory(prefix="luma-weather-test-") as directory:
    root = Path(directory)
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ["HOME"] = str(root / "home")
    cache = root / "cache"

    written = write_cache(forecast, cache)
    assert written.exists() and (written.stat().st_mode & 0o777) == 0o600
    restored = read_cache(cupertino.uid, cache)
    assert restored is not None
    assert restored.place == forecast.place
    assert restored.current == forecast.current
    assert restored.hours == forecast.hours and restored.days == forecast.days
    assert read_cache("never-fetched", cache) is None
    # A half-written or corrupted file is nothing, not a crash on launch.
    (cache / f"{cupertino.uid}.json").write_text("{oh no", encoding="utf-8")
    assert read_cache(cupertino.uid, cache) is None
    check("cache: a full payload round-trips, privately, and damage reads as nothing")

    stale = CityForecast(place=cupertino, fetched_at="2020-01-01T00:00:00+00:00",
                         current=full)
    assert needs_refresh(None) and needs_refresh(stale)
    fresh = CityForecast(place=cupertino,
                         fetched_at=datetime.now(timezone.utc).isoformat(),
                         current=full)
    assert not needs_refresh(fresh)
    assert fresh.age_seconds() is not None and fresh.age_seconds() < 5
    check("staleness is known to the model, whether or not anything renders it")

    store = PlaceStore(root / "places.json")
    first = store.add(new_place(Place(uid="", name="Cupertino", latitude=37.323,
                                      longitude=-122.0322, timezone="America/Los_Angeles")))
    second = store.add(new_place(Place(uid="", name="London", latitude=51.5074,
                                       longitude=-0.1278, timezone="Europe/London")))
    third = store.add(new_place(Place(uid="", name="Tokyo", latitude=35.6762,
                                      longitude=139.6503, timezone="Asia/Tokyo")))
    assert [place.name for place in store.list()] == ["Cupertino", "London", "Tokyo"]
    assert store.contains(37.323, -122.0322) and not store.contains(0.0, 0.0)
    assert store.move(third.uid, -1)
    assert [place.name for place in store.list()] == ["Cupertino", "Tokyo", "London"]
    assert not store.move(first.uid, -1) and not store.move(second.uid, 1)
    index, removed = store.remove(third.uid)
    assert index == 1 and removed.name == "Tokyo"
    # Undo puts it back at the index it came from, not at the end.
    store.insert(index, removed)
    assert [place.name for place in store.list()] == ["Cupertino", "Tokyo", "London"]
    # The order survives a restart, because it is the person's order.
    assert [place.name for place in PlaceStore(root / "places.json").list()] == \
        ["Cupertino", "Tokyo", "London"]
    check("places: order kept, reordering bounded, removal returns its index for Undo")

assert parse_coordinates("37.32, -122.03") == (37.32, -122.03)
assert parse_coordinates("91, 0") is None and parse_coordinates("Cupertino") is None
check("the search field still accepts coordinates where there is no location database")

print(f"PASS: prairie-weather unit — {len(checks)} checks")
