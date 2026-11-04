# SPDX-License-Identifier: Apache-2.0
"""Today's high and low include the reading now and the hours already gone, on every presentation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src/prairie-core'), str(ROOT / 'src/luma-platform/appkit')]

from prairie_apps import weather_backend  # noqa: E402
from prairie_apps.weather_backend import keep_today_range, parse_forecast  # noqa: E402
from prairie_apps.weather_data import snapshot_from_forecast  # noqa: E402
from prairie_apps.weather_model import Place, UNITS_METRIC, read_cache, write_cache  # noqa: E402
from prairie_apps.weather_observation import Observation  # noqa: E402
try:  # TV Mode's presentation model; checked where it exists
    from prairie_apps.weather_tv_model import tv_from_forecast  # noqa: E402
except ImportError:
    tv_from_forecast = None

CHICAGO = Place('chi', 'Chicago', 'Illinois', 'United States', 41.88, -87.63, 'America/Chicago')


def entry(moment, temperature):
    return {'time': moment.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'data': {'instant': {'details': {'air_temperature': temperature}},
                     'next_1_hours': {'summary': {'symbol_code': 'cloudy'}, 'details': {}}}}


def evening_payload(start):
    # 6 PM onwards, cooling from 15 °C: MET's series no longer holds the afternoon.
    return {'properties': {'timeseries': [entry(start + timedelta(hours=h), 15 - h * .5) for h in range(30)]}}


class TodayRange(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 11, 2, 0, tzinfo=timezone.utc)        # 6 PM in Chicago
        self.now = self.start + timedelta(minutes=10)
        self.forecast = parse_forecast(evening_payload(self.start), CHICAGO, now=self.now)

    def test_an_observed_reading_above_the_forecast_raises_the_high(self):
        observed = Observation(station='KMDW', station_name='Chicago Midway', observed_at=self.now.isoformat(),
                               temperature_c=16.2, description='Cloudy', symbol='cloudy', distance_km=12.0)
        snapshot = snapshot_from_forecast(self.forecast, UNITS_METRIC, now=self.now, observation=observed)
        self.assertEqual(snapshot.temperature, '16°')
        self.assertEqual(snapshot.high, '16°')
        self.assertEqual(snapshot.days[0].high, '16°')
        if tv_from_forecast is not None:
            tv = tv_from_forecast(self.forecast, snapshot, UNITS_METRIC, now=self.now)
            self.assertEqual((tv.days[0].high, tv.days[0].low), (snapshot.high, snapshot.low))

    def test_the_high_never_sits_below_now(self):
        snapshot = snapshot_from_forecast(self.forecast, UNITS_METRIC, now=self.now)
        current = int(snapshot.temperature.rstrip('°'))
        self.assertGreaterEqual(int(snapshot.high.rstrip('°')), current)
        self.assertLessEqual(int(snapshot.low.rstrip('°')), current)

    def test_a_refresh_keeps_the_hours_already_gone(self):
        afternoon = replace(self.forecast, days=(replace(self.forecast.days[0], high_c=19.0, low_c=6.0),)
                            + self.forecast.days[1:])
        kept = keep_today_range(self.forecast, afternoon)
        self.assertEqual((kept.days[0].high_c, kept.days[0].low_c), (19.0, 6.0))
        self.assertEqual((kept.current.high_c, kept.current.low_c), (19.0, 6.0))
        self.assertEqual(kept.days[1:], self.forecast.days[1:])
        other_day = replace(afternoon, days=(replace(afternoon.days[0], date='2026-10-31'),))
        self.assertIs(keep_today_range(self.forecast, other_day), self.forecast)
        self.assertIs(keep_today_range(self.forecast, None), self.forecast)

    def test_fetch_widens_from_the_cache(self):
        with tempfile.TemporaryDirectory() as root:
            earlier = replace(self.forecast, days=(replace(self.forecast.days[0], high_c=21.0),) + self.forecast.days[1:])
            write_cache(earlier, Path(root))
            with patch.object(weather_backend, '_request', side_effect=[(evening_payload(self.start), ''), OSError()]):
                fetched = weather_backend.fetch_forecast(CHICAGO, cache_root=Path(root), now=self.now)
            self.assertEqual(fetched.days[0].high_c, 21.0)
            self.assertEqual(read_cache(CHICAGO.uid, Path(root)).days[0].high_c, 21.0)


if __name__ == '__main__':
    unittest.main()
