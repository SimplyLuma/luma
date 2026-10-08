# SPDX-License-Identifier: Apache-2.0

"""Weather's backend: the places you keep, and where the figures come from.

**What the forecast comes from, and why.** libgweather is the obvious starting
point and it is not enough here. The version Fedora 44 ships, 4.6.0, exposes
temperature, apparent temperature, dew point, humidity, wind, pressure,
visibility, sunrise and sunset — and no precipitation figure of any kind and
no UV index (see `gweather_info_get_*` in libgweather 4.6.0). The design needs
both: the precipitation bars are a card of their own and the UV index is one
of the nine detail rows. libgweather's MET.no provider also asks for
`locationforecast/2.0/classic`, whose XML carries a precipitation *amount* but
no probability and no UV.

So the numbers come straight from MET Norway's `locationforecast/2.0/complete`,
which carries apparent temperature, relative humidity, wind, pressure, an
ultraviolet index and, where MET's probabilistic model reaches, a probability
of precipitation. Sunrise and sunset come from MET's Sunrise 3.0 service
rather than from a formula of our own.

What libgweather is still the right answer for is the **location database**:
translated city names, coordinates and, crucially, each city's IANA time zone,
which is what makes night local to the city being shown. It is used for the
Add City search when its typelib is present and the application degrades to
coordinate entry when it is not.

Attribution for MET's data belongs in About and nowhere else (§11).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Callable, Sequence
import urllib.parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from .weather_model import (
    CityForecast, Current, DayPoint, HourPoint, Place, cache_directory,
    forget_cache, parse_time, pressure_trend, read_cache, write_cache,
)

try:  # Python 3.9+, and tzdata is on every Fedora image.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - the platform always has it
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[misc,assignment]


# MET Norway's terms require an identifying User-Agent with a way to reach us,
# and visible attribution. The attribution line is in About; this is the
# identification. Anything generic gets the whole project rate-limited.
USER_AGENT = "ProjectLuma-Weather/1.0 (+https://project-luma.local/contact)"
FORECAST_ENDPOINT = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
SUN_ENDPOINT = "https://api.met.no/weatherapi/sunrise/3.0/sun"
ATTRIBUTION = "Forecast data from the Norwegian Meteorological Institute."
ATTRIBUTION_URL = "https://www.met.no/"

# How old a payload has to be before a refresh is worth a request. MET asks
# callers not to poll faster than their Expires header, which is ~30 minutes.
REFRESH_AFTER_SECONDS = 30 * 60
HOURS_SHOWN = 25
DAYS_SHOWN = 10
REQUEST_TIMEOUT = 12.0


_LOAD_LOCK = threading.Lock()

class ForecastUnavailable(RuntimeError):
    """No network, and nothing cached for this city."""


# ── Where a city is, in time ─────────────────────────────────────────────

def local_zone(place: Place):
    """The city's own time zone.

    A real IANA zone when the location database gave us one. Otherwise the
    zone the sun keeps: fifteen degrees of longitude to the hour. That is
    within an hour of civil time nearly everywhere, which is enough for a rule
    whose only boundary is 20:00, and it is honest about being a fallback.
    """
    if place.timezone and ZoneInfo is not None:
        try:
            return ZoneInfo(place.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    offset = round(place.longitude / 15.0)
    offset = max(-12, min(14, offset))
    return timezone(timedelta(hours=offset))


def local_now(place: Place, now: datetime | None = None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(local_zone(place))


def to_local(place: Place, value: str) -> datetime | None:
    moment = parse_time(value)
    return moment.astimezone(local_zone(place)) if moment else None


# ── The places you keep ──────────────────────────────────────────────────

class PlaceStore:
    """The saved cities, in the order you put them in.

    Order is the person's, so the file keeps a list and never sorts it. Undo
    needs to put a city back where it came from, so removal returns both the
    record and its index and insertion takes one.
    """

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
            path = data / "prairie/weather/places.json"
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._places: list[Place] = self._read()
        # Undo restores the removed record, including fields owned by sync or
        # another provider. Keep these only in memory, never in a new store.
        self._removed_records: dict[str, dict] = {}

    # -- persistence ------------------------------------------------------
    def _read(self) -> list[Place]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(payload, (list, dict)):
            return []
        records = payload if isinstance(payload, list) else payload.get("places", [])
        result: list[Place] = []
        for item in records if isinstance(records, list) else ():
            try:
                result.append(Place(
                    uid=str(item["uid"]),
                    name=str(item["name"]),
                    region=str(item.get("region", "")),
                    country=str(item.get("country", "")),
                    latitude=float(item.get("latitude", 0.0)),
                    longitude=float(item.get("longitude", 0.0)),
                    timezone=str(item.get("timezone", "")),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def unit_setting(self):
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return 'automatic'
        if not isinstance(payload, dict): return 'automatic'
        preferences = payload.get('preferences', {})
        return preferences.get('units', 'automatic') if isinstance(preferences, dict) else 'automatic'

    def set_units(self, units):
        if units not in ('metric', 'imperial'):
            raise ValueError('Choose Celsius or Fahrenheit.')
        self._write(units=units)

    def _write(self, *, units=None) -> None:
        # Preserve sync metadata and fields this UI does not edit. Adding,
        # removing or reordering places changes list membership/order only.
        original = self.path.read_bytes() if self.path.exists() else None
        payload = json.loads(original) if original is not None else []
        if not isinstance(payload, (list, dict)):
            raise ValueError("The saved city file has an unsupported format")
        records = payload if isinstance(payload, list) else payload.get("places", [])
        if not isinstance(records, list):
            raise ValueError("The saved city list has an unsupported format")
        known = {str(item["uid"]): item for item in records
                 if isinstance(item, dict) and "uid" in item}
        changed = [known.get(place.uid, self._removed_records.get(place.uid, asdict(place)))
                   for place in self._places]
        # Records the parser cannot understand belong to their original owner.
        parsed_ids = {place.uid for place in self._read()}
        kept_ids = {place.uid for place in self._places}
        for uid in parsed_ids - kept_ids:
            if uid in known:
                self._removed_records[uid] = known[uid]
        changed.extend(item for item in records
                       if not isinstance(item, dict) or str(item.get("uid", "")) not in parsed_ids)
        document = changed if isinstance(payload, list) else {**payload, "places": changed}
        if units is not None:
            if isinstance(document, list): document = {'places': document}
            preferences = document.get('preferences', {})
            if not isinstance(preferences, dict):
                raise ValueError('The saved weather preferences are invalid. Nothing was changed.')
            document['preferences'] = {**preferences, 'units': units}
        if original is not None:
            backup = self.path.with_name(self.path.name + ".lumaui-backup")
            try:
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                except BaseException:
                    backup.unlink(missing_ok=True)
                    raise
        descriptor, temporary_name = tempfile.mkstemp(prefix=".places-", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    # -- the list ---------------------------------------------------------
    def reload(self) -> bool:
        """Re-read the file; True when the cities changed.

        Another process (Connect sync) may rewrite the file while the app is
        open, so every change starts from the file rather than from memory —
        otherwise the app's next write would silently undo the synced one.
        """
        places = self._read()
        changed = places != self._places
        self._places = places
        return changed

    def list(self) -> tuple[Place, ...]:
        return tuple(self._places)

    def seed_setup_place(self, location) -> bool:
        """Create the first city once; an existing empty list is a user choice.

        Exclusive creation also preserves a list created by sync or another
        process between construction and the first window's bootstrap.
        """
        if location is None or location.place is None:
            return False
        if self.path.exists():
            self.reload()
            return False
        record = Place(uid=uuid.uuid4().hex, **location.place)
        fd, name = tempfile.mkstemp(prefix='.setup-city-', dir=self.path.parent)
        temporary = Path(name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump([asdict(record)], stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Atomically publish the complete file only if no owner has
                # created one meanwhile; never replace synced or user state.
                os.link(temporary, self.path)
            except FileExistsError:
                self.reload()
                return False
        finally:
            temporary.unlink(missing_ok=True)
        self._places = [record]
        return True

    def index_of(self, uid: str) -> int:
        for index, place in enumerate(self._places):
            if place.uid == uid:
                return index
        return -1

    def contains(self, latitude: float, longitude: float) -> bool:
        return any(abs(place.latitude - latitude) < 1e-4
                   and abs(place.longitude - longitude) < 1e-4
                   for place in self._places)

    def add(self, place: Place) -> Place:
        self.reload()
        record = place if place.uid else Place(**{**asdict(place), "uid": uuid.uuid4().hex})
        self._places.append(record)
        self._write()
        return record

    def insert(self, index: int, place: Place) -> None:
        self.reload()
        self._places.insert(max(0, min(index, len(self._places))), place)
        self._write()

    def remove(self, uid: str) -> tuple[int, Place | None]:
        self.reload()
        index = self.index_of(uid)
        if index < 0:
            return -1, None
        place = self._places.pop(index)
        self._write()
        return index, place

    def move(self, uid: str, offset: int) -> bool:
        self.reload()
        index = self.index_of(uid)
        target = index + offset
        if index < 0 or not 0 <= target < len(self._places):
            return False
        self._places.insert(target, self._places.pop(index))
        self._write()
        return True


# ── The forecast ─────────────────────────────────────────────────────────

def _request(url: str, *, modified_since: str = "") -> tuple[dict, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if modified_since:
        headers["If-Modified-Since"] = modified_since
    with urlopen(Request(url, headers=headers), timeout=REQUEST_TIMEOUT) as response:
        return json.load(response), response.headers.get("Last-Modified", "")


def _number(details: dict, key: str) -> float | None:
    value = details.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _entry_details(entry: dict, section: str) -> dict:
    block = entry.get("data", {}).get(section, {})
    return block.get("details", {}) if isinstance(block, dict) else {}


def _entry_symbol(entry: dict) -> str:
    for section in ("next_1_hours", "next_6_hours", "next_12_hours"):
        block = entry.get("data", {}).get(section, {})
        code = block.get("summary", {}).get("symbol_code") if isinstance(block, dict) else None
        if code:
            return str(code)
    return ""


def parse_forecast(payload: dict, place: Place, *, now: datetime | None = None,
                   sun: dict | None = None) -> CityForecast:
    """Turn one MET Norway payload into the city the view renders.

    The series is hourly for about the first two and a half days and six-hourly
    after that, so the twelve-hour curve comes off the front of it and the
    seven days are grouped from the whole of it in the city's own local time.
    """
    series = payload.get("properties", {}).get("timeseries", [])
    if not isinstance(series, list) or not series:
        raise ValueError("the forecast response contains no observations")
    zone = local_zone(place)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    points: list[tuple[datetime, dict]] = []
    for entry in series:
        moment = parse_time(str(entry.get("time", "")))
        if moment is None:
            continue
        points.append((moment, entry))
    if not points:
        raise ValueError("the forecast response contains no usable times")

    # The current hour is the last point at or before now, so a payload that
    # is twenty minutes old still opens on the right hour rather than on a
    # forecast for later.
    current_index = 0
    for index, (moment, _entry) in enumerate(points):
        if moment <= reference:
            current_index = index
        else:
            break

    hours: list[HourPoint] = []
    for moment, entry in points[current_index:current_index + HOURS_SHOWN]:
        instant = _entry_details(entry, "instant")
        temperature = _number(instant, "air_temperature")
        if temperature is None:
            continue
        next_hour = _entry_details(entry, "next_1_hours")
        hours.append(HourPoint(
            time=moment.astimezone(zone).isoformat(),
            temperature_c=temperature,
            symbol=_entry_symbol(entry),
            precipitation_probability=_number(next_hour, "probability_of_precipitation"),
            precipitation_mm=_number(next_hour, "precipitation_amount"),
        ))

    days = _daily(points, zone)

    head_moment, head = points[current_index]
    instant = _entry_details(head, "instant")
    temperature = _number(instant, "air_temperature")
    if temperature is None:
        raise ValueError("the forecast response contains no current temperature")
    next_hour = _entry_details(head, "next_1_hours")
    later = points[min(current_index + 3, len(points) - 1)][1]
    today = days[0] if days else None
    sunrise, sunset = _sun_times(sun)

    current = Current(
        temperature_c=temperature,
        symbol=_entry_symbol(head),
        apparent_c=_number(instant, "apparent_air_temperature"),
        humidity=_number(instant, "relative_humidity"),
        wind_mps=_number(instant, "wind_speed"),
        wind_degrees=_number(instant, "wind_from_direction"),
        # MET publishes a clear-sky ultraviolet index; it is the provider's own
        # figure, so it is shown as given rather than adjusted by us.
        uv_index=_number(instant, "ultraviolet_index_clear_sky"),
        # MET's forecast carries no visibility at all. The row is dropped.
        visibility_m=None,
        pressure_hpa=_number(instant, "air_pressure_at_sea_level"),
        pressure_trend=pressure_trend(
            _number(instant, "air_pressure_at_sea_level"),
            _number(_entry_details(later, "instant"), "air_pressure_at_sea_level"),
        ),
        precipitation_probability=_number(next_hour, "probability_of_precipitation"),
        precipitation_mm=_number(next_hour, "precipitation_amount"),
        high_c=today.high_c if today else None,
        low_c=today.low_c if today else None,
        sunrise=sunrise,
        sunset=sunset,
    )
    del head_moment
    return CityForecast(
        place=place,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        current=current,
        hours=tuple(hours),
        days=tuple(days),
    )


def _daily(points: Sequence[tuple[datetime, dict]], zone) -> list[DayPoint]:
    buckets: dict[str, list[tuple[datetime, dict]]] = {}
    for moment, entry in points:
        local = moment.astimezone(zone)
        buckets.setdefault(local.date().isoformat(), []).append((local, entry))
    days: list[DayPoint] = []
    for day, entries in list(buckets.items())[:DAYS_SHOWN]:
        temperatures: list[float] = []
        for _local, entry in entries:
            instant = _entry_details(entry, "instant")
            value = _number(instant, "air_temperature")
            if value is not None:
                temperatures.append(value)
            for section in ("next_6_hours", "next_12_hours"):
                details = _entry_details(entry, section)
                for key in ("air_temperature_max", "air_temperature_min"):
                    extreme = _number(details, key)
                    if extreme is not None:
                        temperatures.append(extreme)
        if not temperatures:
            continue
        days.append(DayPoint(
            date=day,
            symbol=_day_symbol(entries),
            high_c=max(temperatures),
            low_c=min(temperatures),
        ))
    return days


def _day_symbol(entries: Sequence[tuple[datetime, dict]]) -> str:
    """A day's glyph is the middle of its daylight, never its night.

    A row that says "Thu" means the day, so a daily glyph is picked from the
    afternoon and the daily list is always drawn with daytime glyphs.
    """
    best = ""
    for local, entry in entries:
        symbol = _entry_symbol(entry)
        if not symbol:
            continue
        if not best:
            best = symbol
        if 11 <= local.hour <= 15:
            return symbol
    return best


def _sun_times(sun: dict | None) -> tuple[str, str]:
    if not isinstance(sun, dict):
        return "", ""
    properties = sun.get("properties", {})
    rise = properties.get("sunrise", {}).get("time", "") if isinstance(properties, dict) else ""
    down = properties.get("sunset", {}).get("time", "") if isinstance(properties, dict) else ""
    return str(rise or ""), str(down or "")


def _sun_url(place: Place, moment: datetime) -> str:
    offset = moment.utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    query = urllib.parse.urlencode({
        "lat": f"{place.latitude:.4f}",
        "lon": f"{place.longitude:.4f}",
        "date": moment.date().isoformat(),
        "offset": f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}",
    })
    return f"{SUN_ENDPOINT}?{query}"


def fetch_forecast(place: Place, *, cache_root: Path | None = None,
                   now: datetime | None = None,
                   on_forecast: Callable[[CityForecast], None] | None = None) -> CityForecast:
    """Fetch a city, write it to the cache, and return it.

    Failure here is not an error the person has to see: the caller falls back
    to whatever the cache holds, which is what makes the app usable with no
    network at all. A caller may receive the essential forecast before the
    optional sunrise request; the return value still includes sunrise when
    available, as it does for callers without a progress callback.
    """
    root = Path(cache_root) if cache_root is not None else cache_directory()
    query = urllib.parse.urlencode({
        "lat": f"{place.latitude:.4f}",
        "lon": f"{place.longitude:.4f}",
    })
    payload, _modified = _request(f"{FORECAST_ENDPOINT}?{query}")
    if on_forecast is not None:
        essential = parse_forecast(payload, place, now=now)
        try:
            write_cache(essential, root)
        except OSError:
            pass
        on_forecast(essential)
    sun: dict | None = None
    try:
        sun, _ = _request(_sun_url(place, local_now(place, now)))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        # Sunrise is one of nine rows. Losing it drops two rows; it does not
        # lose the forecast.
        sun = None
    forecast = parse_forecast(payload, place, now=now, sun=sun)
    try:
        write_cache(forecast, root)
    except OSError:
        pass
    return forecast


def cached_forecast(place: Place, *, cache_root: Path | None = None) -> CityForecast | None:
    root = Path(cache_root) if cache_root is not None else cache_directory()
    forecast = read_cache(place.uid, root)
    if forecast is None:
        return None
    # The place may have been renamed or re-timezoned since; the payload is
    # the weather, not the identity.
    return CityForecast(place=place, fetched_at=forecast.fetched_at,
                        current=forecast.current, hours=forecast.hours,
                        days=forecast.days)


def drop_cached(place: Place, *, cache_root: Path | None = None) -> None:
    root = Path(cache_root) if cache_root is not None else cache_directory()
    forget_cache(place.uid, root)


def needs_refresh(forecast: CityForecast | None, *, now: datetime | None = None) -> bool:
    if forecast is None:
        return True
    age = forecast.age_seconds(now)
    return age is None or age >= REFRESH_AFTER_SECONDS


# ── Finding a city ───────────────────────────────────────────────────────

class LocationSearch:
    """Add City's search, over libgweather's location database.

    The database ships with libgweather, is translated, is offline, and knows
    each city's time zone, which is exactly what this application needs and
    what no geocoding service would give us for free. When the typelib is not
    present the field still accepts coordinates, so the application works —
    it just cannot look a name up.
    """

    def __init__(self) -> None:
        self._index: list[Place] | None = None
        self.available = False
        self._world = None
        try:
            import gi

            gi.require_version("GWeather", "4.0")
            from gi.repository import GWeather  # noqa: F401

            self._weather = GWeather
            self._world = GWeather.Location.get_world()
            self.available = self._world is not None
        except (ImportError, ValueError, AttributeError):
            self._weather = None

    def load(self) -> None:
        """Walk the database once. Call this off the main thread.

        Every search popover starts a loader, so two can race; libgweather's
        locations are not safe to walk from two threads at once, and a child
        reached through `next_child` could be finalized under the other walk
        (a use-after-free that crashed the UI smoke intermittently). One walk at
        a time, every location visited is held until the walk ends, and a
        city's country and region come from the path that reached it rather
        than from asking libgweather to walk back up through `get_parent`.
        """
        with _LOAD_LOCK:
            if self._index is not None or not self.available:
                return
            self._index = self._walk()

    def _walk(self) -> list[Place]:
        index: list[Place] = []
        levels = self._weather.LocationLevel
        world = self._world
        held = [world]
        # (location, country name, ancestor names nearest first)
        stack = [(world, "", ())]
        while stack:
            parent, country, ancestors = stack.pop()
            children = []
            child = parent.next_child(None)
            while child is not None:
                children.append(child)
                child = parent.next_child(child)
            held.extend(children)
            for child in children:
                level = child.get_level()
                name = child.get_name() or ""
                if level == levels.CITY and child.has_coords():
                    # get_coords() is (latitude, longitude) here. Older
                    # introspection data returned the gboolean alongside it, and
                    # assuming that shape raised inside the loading thread, which
                    # left the index None and every search silently empty.
                    coordinates = child.get_coords()
                    if len(coordinates) == 3:
                        _found, latitude, longitude = coordinates
                    else:
                        latitude, longitude = coordinates
                    index.append(Place(
                        uid="",
                        name=name,
                        region=_region_from(ancestors, country),
                        country=country,
                        latitude=latitude,
                        longitude=longitude,
                        timezone=child.get_timezone_str() or "",
                    ))
                else:
                    stack.append((child, name if level == levels.COUNTRY else country,
                                  (name, *ancestors)))
        del held
        return index

    def lookup(self, query: str, *, limit: int = 20) -> tuple[Place, ...]:
        """Online fallback for postal codes and locations absent from GWeather."""
        from urllib.parse import urlencode
        # Open-Meteo indexes city names and some postcodes, but omits valid US
        # ZIPs such as 66203. Resolve complete ZIPs through the postal index
        # before trying the worldwide place-name service.
        zip_code = re.fullmatch(r"\d{5}(?:-\d{4})?", query.strip())
        if zip_code:
            try:
                postal, _ = _request("https://api.zippopotam.us/us/" + zip_code.group()[:5])
                matches = tuple(Place(uid="", name=item["place name"],
                                      region=item.get("state", ""), country=postal.get("country", ""),
                                      latitude=float(item["latitude"]), longitude=float(item["longitude"]),
                                      timezone=self._nearby_timezone(float(item["latitude"]),
                                                                     float(item["longitude"])))
                                for item in postal.get("places", ())[:limit])
                if matches:
                    return matches
            except (OSError, ValueError, KeyError, TypeError):
                pass
        payload, _ = _request("https://geocoding-api.open-meteo.com/v1/search?" +
                              urlencode({"name": query, "count": limit, "language": "en"}))
        return tuple(Place(uid="", name=item["name"],
                           region=item.get("admin1", ""), country=item.get("country", ""),
                           latitude=float(item["latitude"]), longitude=float(item["longitude"]),
                           timezone=item.get("timezone", ""))
                     for item in payload.get("results", []))

    def _nearby_timezone(self, latitude: float, longitude: float) -> str:
        """Keep the local clock accurate when a postal record lacks a zone."""
        if not self._index:
            return ""
        cosine = math.cos(math.radians(latitude))
        zones = [place for place in self._index if place.timezone]
        if not zones:
            return ""
        nearest = min(zones, key=lambda place: (place.latitude - latitude) ** 2 +
                      ((place.longitude - longitude) * cosine) ** 2)
        distance = (nearest.latitude - latitude) ** 2 + ((nearest.longitude - longitude) * cosine) ** 2
        return nearest.timezone if distance < 1 else ""

    def search(self, query: str, *, limit: int = 40) -> tuple[Place, ...]:
        text = (query or "").strip().casefold()
        coordinates = parse_coordinates(query)
        if coordinates is not None:
            latitude, longitude = coordinates
            return (Place(uid="", name=f"{latitude:.3f}, {longitude:.3f}",
                          latitude=latitude, longitude=longitude),)
        if not text or self._index is None:
            return ()
        starts = [place for place in self._index if place.name.casefold().startswith(text)]
        contains = [place for place in self._index
                    if text in place.name.casefold() and place not in starts]
        return tuple((starts + contains)[:limit])



def _region_from(ancestors: tuple[str, ...], country: str) -> str:
    """The nearest enclosing name that is not the country itself."""
    for name in ancestors:
        if name and name != country:
            return name
    return ""


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """`37.32, -122.03` typed into the search field, for when there is no
    location database to search."""
    parts = [part.strip() for part in (text or "").replace(";", ",").split(",")]
    if len(parts) != 2:
        return None
    try:
        latitude, longitude = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def new_place(place: Place) -> Place:
    return Place(uid=uuid.uuid4().hex, name=place.name, region=place.region,
                 country=place.country, latitude=round(place.latitude, 4),
                 longitude=round(place.longitude, 4), timezone=place.timezone)
