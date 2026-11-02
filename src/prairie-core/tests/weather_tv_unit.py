# SPDX-License-Identifier: Apache-2.0
"""Weather's TV data: ten days of 24 hours from the same forecast, with no GTK."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src/prairie-core'), str(ROOT / 'src/luma-platform/appkit')]

from prairie_apps.weather_backend import parse_forecast  # noqa: E402
from prairie_apps.weather_data import snapshot_from_forecast  # noqa: E402
from prairie_apps.weather_fixture import FixtureSource  # noqa: E402
from prairie_apps.weather_model import CityForecast, Place, UNITS_IMPERIAL  # noqa: E402
from prairie_apps.weather_tv_model import (  # noqa: E402
    LIGHT_GOLDEN, LIGHT_NIGHT, band_hour, day_sentence, sun_times, tv_from_forecast,
)

CHICAGO = Place('chi', 'Chicago', 'Illinois', 'United States', 41.88, -87.63, 'America/Chicago')


def entry(moment, temperature, *, symbol='clearsky_day', six=False, probability=None):
    instant = {'air_temperature': temperature, 'relative_humidity': 60.0, 'wind_speed': 4.0,
               'wind_from_direction': 270.0, 'apparent_air_temperature': temperature - 1,
               'ultraviolet_index_clear_sky': 3.0}
    block = {'summary': {'symbol_code': symbol}, 'details': {'precipitation_amount': 0.0}}
    if probability is not None:
        block['details']['probability_of_precipitation'] = probability
    data = {'instant': {'details': instant}, ('next_6_hours' if six else 'next_1_hours'): block}
    return {'time': moment.strftime('%Y-%m-%dT%H:%M:%SZ'), 'data': data}


def payload(start):
    """MET's shape: hourly for 60 hours, then six-hourly to day ten."""
    series = [entry(start + timedelta(hours=h), 10 + h % 8,
                    symbol='rain' if 20 <= h < 23 else 'partlycloudy_day', probability=70 if 20 <= h < 23 else 5)
              for h in range(60)]
    series += [entry(start + timedelta(hours=60 + 6 * k), 12 + k % 4, six=True) for k in range(30)]
    return {'properties': {'timeseries': series}}


class LiveForecast(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 15, 20, tzinfo=timezone.utc)   # 9:20 AM in Chicago
        self.forecast = parse_forecast(payload(datetime(2026, 11, 2, 15, tzinfo=timezone.utc)), CHICAGO, now=self.now)
        self.snapshot = snapshot_from_forecast(self.forecast, UNITS_IMPERIAL, now=self.now)
        self.tv = tv_from_forecast(self.forecast, self.snapshot, UNITS_IMPERIAL, now=self.now)

    def test_the_series_is_kept_whole_and_survives_the_cache(self):
        self.assertEqual(len(self.forecast.hours), 25)
        self.assertEqual(len(self.forecast.timeline), 90)
        self.assertEqual(self.forecast.timeline[-1].span_hours, 6)
        again = CityForecast.from_dict(self.forecast.to_dict())
        self.assertEqual(again.timeline, self.forecast.timeline)
        legacy = self.forecast.to_dict()
        del legacy['timeline']
        self.assertEqual(CityForecast.from_dict(legacy).timeline, ())   # an older cache reads as no series
        legacy['timeline'] = [{**self.forecast.to_dict()['timeline'][0], 'later_field': 1}]
        self.assertEqual(len(CityForecast.from_dict(legacy).timeline), 1)   # a newer writer's extra field is ignored

    def test_today_runs_from_now_and_other_days_from_midnight(self):
        today, tomorrow = self.tv.days[0], self.tv.days[1]
        self.assertEqual(len(today.hours), 24)
        self.assertEqual(today.hours[0].at.hour, 9)
        self.assertEqual(tomorrow.hours[0].at.hour, 0)
        self.assertEqual(tomorrow.hours[0].at.date(), date(2026, 11, 3))
        self.assertEqual(self.tv.days[0].label, 'Today')
        self.assertEqual(self.tv.days[0].sentence, self.snapshot.sentence)

    def test_six_hour_steps_fill_every_hour_and_stop_at_the_end(self):
        late = self.tv.days[5]
        self.assertEqual(len(late.hours), 24)
        self.assertEqual([h.at.hour for h in late.hours], list(range(24)))
        self.assertTrue(all(h.feels and h.wind and h.humidity for h in late.hours))
        last = self.tv.days[-1]
        self.assertLessEqual(len(last.hours), 24)

    def test_rain_hours_are_wet_and_the_first_hour_is_the_reading(self):
        wet = [h for h in self.tv.days[0].hours if h.wet]
        self.assertEqual([h.at.hour for h in wet], [5, 6, 7])   # 20-22 h after 15:00 UTC
        self.assertEqual(self.tv.days[0].hours[0].temperature, self.snapshot.temperature)

    def test_six_tiles_with_no_invented_air_quality(self):
        tiles = self.tv.tiles(0, 0)
        keys = [t.key for t in tiles]
        self.assertNotIn('air', keys)
        self.assertEqual(keys[:4], ['feels', 'wind', 'humidity', 'uv'])
        later = [t.key for t in self.tv.tiles(1, 12)]
        self.assertIn('sun', later)
        self.assertEqual(self.tv.tiles(1, 12)[-1].title, 'Sunset')

    def test_night_and_golden_light_follow_the_sun(self):
        tomorrow = self.tv.days[1]
        rise = next(at for at, kind in tomorrow.sun if kind == 'rise')
        self.assertEqual(tomorrow.hours[2].light, LIGHT_NIGHT)
        self.assertEqual(tomorrow.hours[rise.hour].light, LIGHT_GOLDEN)


class Sun(unittest.TestCase):
    def test_chicago_in_november(self):
        from zoneinfo import ZoneInfo
        rise, down = sun_times(41.88, -87.63, date(2026, 11, 2), ZoneInfo('America/Chicago'))
        self.assertEqual((rise.hour, down.hour), (6, 16))
        self.assertTrue(20 <= rise.minute <= 35 and 35 <= down.minute <= 50)

    def test_polar_night_has_no_sunrise(self):
        self.assertEqual(sun_times(78.2, 15.6, date(2026, 12, 21), timezone.utc), (None, None))


class Words(unittest.TestCase):
    def test_band_hours_and_day_sentences(self):
        at = datetime(2026, 11, 3, 0)
        self.assertEqual([band_hour(at + timedelta(hours=h)) for h in (0, 9, 12, 15)], ['12 AM', '9 AM', 'Noon', '3 PM'])
        tv = FixtureSource(ROOT / 'tests/fixtures/weather-v70.json', minute=640).tv(
            next(p for p in FixtureSource(ROOT / 'tests/fixtures/weather-v70.json').load() if p.uid == 'oak'))
        rainy = tv.days[8]
        self.assertEqual(rainy.sentence, day_sentence(rainy.hours, rainy.high))
        self.assertTrue(rainy.sentence.startswith('Rain arriving around 1 PM.'))


class FixtureTV(unittest.TestCase):
    def test_the_fixture_gives_every_place_ten_days(self):
        source = FixtureSource(ROOT / 'tests/fixtures/weather-v70.json', minute=640)
        for place in source.load():
            tv = source.tv(place)
            self.assertEqual(len(tv.days), 10)
            self.assertTrue(all(len(d.hours) == 24 for d in tv.days))
            self.assertEqual(len(tv.tiles(0, 0)), 6)
        oakland = source.tv(next(p for p in source.load() if p.uid == 'oak'))
        self.assertTrue(oakland.my_location)
        self.assertEqual([t.key for t in oakland.tiles(0, 0)], ['feels', 'wind', 'humidity', 'uv', 'air', 'sun'])


if __name__ == '__main__':
    unittest.main()
