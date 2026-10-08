# SPDX-License-Identifier: Apache-2.0
"""Fresh US station observations, kept separate from MET's city forecast.

NWS stations describe conditions *at the station*. A nearby rain report is
called out separately; it must not silently replace the closest station's
temperature or condition. NWS precipitation amounts can be missing or rounded
down, so this module never turns them into a measured zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from urllib.request import Request, urlopen

from .weather_model import Place, parse_time

NWS_API = 'https://api.weather.gov'
NWS_AGENT = 'ProjectLuma-Weather/1.0 (+https://project-luma.local/contact)'
MAX_AGE_SECONDS = 90 * 60
NEARBY_KM = 30


@dataclass(frozen=True)
class Observation:
    station: str
    station_name: str
    observed_at: str
    temperature_c: float
    description: str
    symbol: str
    distance_km: float
    nearby_rain: str = ''
    nearby_rain_station: str = ''
    nearby_rain_at: str = ''


def _json(url: str) -> dict:
    request = Request(url, headers={'User-Agent': NWS_AGENT,
                                    'Accept': 'application/geo+json'})
    with urlopen(request, timeout=12) as response:
        return json.load(response)


def _distance_km(place: Place, coordinates: list) -> float:
    lon, lat = map(math.radians, (coordinates[0], coordinates[1]))
    p_lat, p_lon = map(math.radians, (place.latitude, place.longitude))
    a = math.sin((lat - p_lat) / 2) ** 2 + math.cos(lat) * math.cos(p_lat) * math.sin((lon - p_lon) / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(a)))


def _symbol(description: str) -> str:
    words = description.lower()
    if 'thunder' in words:
        return 'rainandthunder'
    if 'snow' in words or 'ice' in words or 'sleet' in words:
        return 'snow'
    if 'rain' in words or 'drizzle' in words or 'shower' in words:
        return 'rain'
    if 'fog' in words or 'mist' in words or 'haze' in words:
        return 'fog'
    if 'partly' in words or 'few clouds' in words or 'scattered' in words:
        return 'partlycloudy_day'
    if 'cloud' in words or 'overcast' in words:
        return 'cloudy'
    return 'clearsky_day'


def _is_rain(properties: dict) -> bool:
    present = properties.get('presentWeather') or ()
    return any(str(item.get('weather', '')).lower() in {'rain', 'drizzle', 'showers'}
               for item in present if isinstance(item, dict)) or any(
                   word in str(properties.get('textDescription') or '').lower()
                   for word in ('rain', 'drizzle', 'shower'))


def fetch_observation(place: Place, *, now: datetime | None = None) -> Observation | None:
    """Return the closest fresh station with a temperature, plus nearby rain.

    A missing/old station reading is ignored. Failure is surfaced to the
    caller, which can label its existing MET forecast as a forecast instead.
    """
    reference = now or datetime.now(timezone.utc)
    point = _json(f'{NWS_API}/points/{place.latitude:.4f},{place.longitude:.4f}')
    stations_url = point.get('properties', {}).get('observationStations')
    if not isinstance(stations_url, str) or not stations_url.startswith(NWS_API + '/'):
        return None
    stations = _json(stations_url).get('features', ())
    candidates = []
    for feature in stations[:6]:
        properties = feature.get('properties', {})
        station = properties.get('stationIdentifier')
        coordinates = feature.get('geometry', {}).get('coordinates')
        if not station or not coordinates or len(coordinates) < 2:
            continue
        distance = _distance_km(place, coordinates)
        if distance > NEARBY_KM:
            continue
        try:
            payload = _json(f'{NWS_API}/stations/{station}/observations/latest')
        except (OSError, ValueError):
            continue
        reading = payload.get('properties', {})
        observed = parse_time(str(reading.get('timestamp') or ''))
        if observed is None or not 0 <= (reference - observed).total_seconds() <= MAX_AGE_SECONDS:
            continue
        temperature = reading.get('temperature') or {}
        value = temperature.get('value')
        if temperature.get('unitCode') != 'wmoUnit:degC' or not isinstance(value, (float, int)) or not math.isfinite(value):
            continue
        candidates.append((distance, str(station), str(properties.get('name') or station),
                           observed.isoformat(), float(value), str(reading.get('textDescription') or ''), reading))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    distance, station, name, observed_at, temperature, description, reading = candidates[0]
    rain = next((item for item in candidates if _is_rain(item[6])), None)
    return Observation(station, name, observed_at, temperature,
                       description or 'Conditions unavailable', _symbol(description), distance,
                       rain[5] if rain else '', rain[2] if rain else '', rain[3] if rain else '')


def _alert_when(onset: datetime | None, ends: datetime | None, local: datetime) -> str:
    """"Today, 3 PM to 9 PM", in the place's own clock (`local` carries its zone)."""
    from .weather_data import short_time

    if onset is None:
        return ''
    zone = local.tzinfo
    start = onset.astimezone(zone) if zone else onset
    day = ('Today' if start.date() == local.date() else
           'Tomorrow' if (start.date() - local.date()).days == 1 else start.strftime('%A'))
    if ends is None:
        return f'{day}, from {short_time(start)}'
    end = ends.astimezone(zone) if zone else ends
    return f'{day}, {short_time(start)} to {short_time(end)}'


def fetch_alerts(place: Place, local: datetime) -> tuple:
    """Active official warnings for the place (NWS, United States only). Read-only.

    Each becomes a Heads up card: the event, its hours, what to do (the
    instruction, else the description's first paragraph) and the issuing
    office. Outside NWS coverage the request fails and the caller shows none.
    """
    from .weather_data import Alert, short_time

    payload = _json(f'{NWS_API}/alerts/active?point={place.latitude:.4f},{place.longitude:.4f}')
    alerts = []
    for feature in payload.get('features', ())[:3]:
        properties = feature.get('properties', {}) if isinstance(feature, dict) else {}
        title = str(properties.get('event') or '').strip()
        if not title:
            continue
        onset = parse_time(str(properties.get('onset') or properties.get('effective') or ''))
        ends = parse_time(str(properties.get('ends') or properties.get('expires') or ''))
        text = str(properties.get('instruction') or properties.get('description') or '').strip()
        text = ' '.join(text.split('\n\n')[0].split())
        zone = local.tzinfo
        starts = short_time(onset.astimezone(zone) if zone else onset) if onset and onset > local else ''
        alerts.append(Alert(title, _alert_when(onset, ends, local), text,
                            str(properties.get('senderName') or 'National Weather Service'), starts))
    return tuple(alerts)
