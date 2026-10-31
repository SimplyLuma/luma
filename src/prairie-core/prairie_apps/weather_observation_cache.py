# SPDX-License-Identifier: Apache-2.0
"""One private station-reading cache shared by Weather and its Home widgets."""
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile

from .weather_model import cache_directory, cache_path, parse_time
from .weather_observation import Observation, MAX_AGE_SECONDS, fetch_observation

REFRESH_SECONDS = 15 * 60


def _path(place, root):
    return cache_path(place.uid + '-observation', root)


def _read(place, root):
    try:
        fd = os.open(_path(place, root), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return {}
        data = json.loads(raw)
        return data if data.get('version') == 1 and data.get('place_uid') == place.uid else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _observation(data, now):
    try:
        value = Observation(**data['observation'])
        stamp = parse_time(value.observed_at)
        degrees = value.temperature_c
        if (stamp is not None and 0 <= (now - stamp).total_seconds() < MAX_AGE_SECONDS
                and not isinstance(degrees, bool) and isinstance(degrees, (int, float))
                and math.isfinite(degrees) and -100 <= degrees <= 100
                and isinstance(value.symbol, str) and isinstance(value.description, str)):
            return value
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return None


def cached_observation(place, *, cache_root=None, now=None):
    root = Path(cache_root) if cache_root is not None else cache_directory()
    return _observation(_read(place, root), now or datetime.now(timezone.utc))


def current_observation(place, *, cache_root=None, now=None, fetch=None):
    """Coalesce app/timer requests; reuse fresh data, including while offline."""
    root = Path(cache_root) if cache_root is not None else cache_directory()
    moment = now or datetime.now(timezone.utc)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(str(_path(place, root)) + '.lock',
                   os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return cached_observation(place, cache_root=root, now=moment)
        data = _read(place, root)
        previous = _observation(data, moment)
        checked = parse_time(data.get('checked_at', ''))
        if checked is not None and 0 <= (moment - checked).total_seconds() < REFRESH_SECONDS:
            return previous
        try:
            value = (fetch or fetch_observation)(place)
            # An unavailable provider must not erase a still-fresh reading.
            value = value or previous
        except (OSError, ValueError, KeyError, TypeError):
            value = previous
        payload = {'version': 1, 'place_uid': place.uid, 'checked_at': moment.isoformat(),
                   'observation': asdict(value) if value else None}
        fd, temporary = tempfile.mkstemp(prefix='.observation-', dir=root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, _path(place, root))
        finally:
            Path(temporary).unlink(missing_ok=True)
        return _observation(payload, moment)
    finally:
        os.close(lock)
