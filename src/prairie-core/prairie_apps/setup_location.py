# SPDX-License-Identifier: Apache-2.0
"""Read Atlas's installed, offline location identity without changing user state."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class SetupLocation:
    timezone: str
    place: dict | None


def read_setup_location(path: Path = Path('/etc/luma/setup-location.json')) -> SetupLocation | None:
    try:
        if path.stat().st_size > 16384:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or type(data.get('schema')) is not int or
                data.get('schema') != 1 or data.get('source') != 'atlas'):
            return None
        zone = data['timezone']
        if not isinstance(zone, str) or not zone:
            return None
        ZoneInfo(zone)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, ZoneInfoNotFoundError):
        return None
    place = data.get('place')
    if not isinstance(place, dict):
        return SetupLocation(zone, None)
    try:
        lat, lon = place['latitude'], place['longitude']
        if (isinstance(lat, bool) or isinstance(lon, bool) or
                not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or
                not math.isfinite(lat) or not math.isfinite(lon) or
                not -90 <= lat <= 90 or not -180 <= lon <= 180 or
                place.get('timezone') != zone):
            return SetupLocation(zone, None)
        fields = {key: place.get(key, '') for key in ('name', 'region', 'country')}
        if not fields['name'] or any(not isinstance(value, str) or len(value) > 256 for value in fields.values()):
            return SetupLocation(zone, None)
        return SetupLocation(zone, {**fields, 'latitude': lat, 'longitude': lon, 'timezone': zone})
    except (KeyError, TypeError):
        return SetupLocation(zone, None)
