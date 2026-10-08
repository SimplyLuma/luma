#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A usable MET forecast precedes slow optional sunrise without changing defaults."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prairie_apps import weather_backend as backend
from prairie_apps.weather_model import Place, read_cache

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
PLACE = Place('test-city', 'Test City', latitude=39, longitude=-94, timezone='UTC')
PAYLOAD = {'properties': {'timeseries': [
    {'time': NOW.isoformat(), 'data': {
        'instant': {'details': {'air_temperature': 20}},
        'next_1_hours': {'summary': {'symbol_code': 'cloudy'}},
    }},
]}}
SUN = {'properties': {'sunrise': {'time': '2026-10-02T06:00:00Z'},
                      'sunset': {'time': '2026-10-02T18:00:00Z'}}}


class ForecastProgressTests(unittest.TestCase):
    def test_progress_and_cache_are_ready_before_optional_sun_request(self):
        published = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def request(url):
                if url.startswith(backend.FORECAST_ENDPOINT):
                    return PAYLOAD, ''
                self.assertEqual(len(published), 1, 'sunrise must not delay the essential forecast')
                self.assertEqual(published[0].current.temperature_c, 20)
                self.assertFalse(published[0].current.sunrise)
                self.assertEqual(read_cache(PLACE.uid, root).current.temperature_c, 20)
                return SUN, ''
            with patch.object(backend, '_request', side_effect=request):
                final = backend.fetch_forecast(PLACE, cache_root=root, now=NOW,
                                              on_forecast=published.append)
            self.assertEqual(final.current.sunrise, SUN['properties']['sunrise']['time'])
            self.assertEqual(read_cache(PLACE.uid, root).current.sunrise, final.current.sunrise)

    def test_default_call_still_returns_and_caches_sunrise(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(backend, '_request', side_effect=[(PAYLOAD, ''), (SUN, '')]) as request:
                final = backend.fetch_forecast(PLACE, cache_root=root, now=NOW)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(final.current.sunrise, SUN['properties']['sunrise']['time'])
            self.assertEqual(read_cache(PLACE.uid, root), final)

    def test_optional_sun_failure_preserves_published_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            published = []
            with patch.object(backend, '_request', side_effect=[(PAYLOAD, ''), TimeoutError('sun stalled')]):
                final = backend.fetch_forecast(PLACE, cache_root=Path(directory), now=NOW,
                                              on_forecast=published.append)
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0].current, final.current)
            self.assertEqual(published[0].hours, final.hours)
            self.assertEqual(final.current.temperature_c, 20)


if __name__ == '__main__':
    unittest.main()
