# SPDX-License-Identifier: Apache-2.0
"""Fixture isolation, Studio arithmetic, live omissions, and safe saved cities."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src/prairie-core'))
from prairie_apps.weather_fixture import FixtureSource, hm, temperature_at, condition_at, rain_at, sky_at
from prairie_apps.weather_data import snapshot_from_forecast, js_round, weather_icon
from prairie_apps.weather_backend import PlaceStore, parse_forecast
from prairie_apps.weather_places import WeatherPlaces
from prairie_apps.weather_model import Place, CityForecast, Current, HourPoint, DayPoint, UNITS_IMPERIAL, UNITS_METRIC
from prairie_apps.weather_gradient import gradient_stops

FIXTURE = ROOT / 'tests/fixtures/weather-v70.json'
FIXTURE_V71 = ROOT / 'tests/fixtures/weather-v71.json'

class GradientTests(unittest.TestCase):
    def test_perceptual_midpoint_and_original_stop_positions(self):
        original = [(0, (0, 0, 0, 1)), (.6, (1, 1, 1, 1)), (1, (.2, .4, .8, 1))]
        expanded = gradient_stops(original)
        self.assertEqual(expanded[0], original[0])
        self.assertEqual(expanded[32], original[1])
        self.assertEqual(expanded[-1], original[-1])
        self.assertAlmostEqual(expanded[16][0], .3)
        for component in expanded[16][1][:3]:
            self.assertAlmostEqual(component, .38857286, places=6)
        self.assertTrue(all(0 <= component <= 1 for _, rgba in expanded for component in rgba))

class PhoneTests(unittest.TestCase):
    """v71's phone page (wxBodyPhone) at 10:40 in Oakland, as the simulator draws it."""

    def test_today_matches_v71(self):
        source = FixtureSource(FIXTURE_V71)
        oak = source.snapshot(source.load()[0])
        self.assertEqual(oak.phone_sentence, 'Dry for now. Light rain from around 6 PM, gone by 9 PM. '
                                             'Wind advisory from 3 PM.')
        self.assertEqual(oak.condition_line, 'Clear · H 71° L 53°')
        self.assertEqual([(c.after, c.label, c.time) for c in oak.changes],
                         [('In 7 h', 'Cloudy', '5 PM'), ('In 8 h', 'Light rain', '6 PM'), ('In 8 h', 'Sunset', '7:02 PM')])
        self.assertEqual([(a.title, a.when, a.source) for a in oak.alerts],
                         [('Wind advisory', 'Today, 3 PM to 9 PM', 'National Weather Service')])
        self.assertEqual([(p.title, p.time, p.temperature) for p in oak.plans],
                         [('Press call · The Verge', '10:30 AM', '65°'), ('Launch walkthrough', '2 PM', '71°'),
                          ('Call Dad', '5:30 PM', '69°')])
        self.assertEqual([(t.title, t.value, t.description) for t in oak.tiles],
                         [('Rain', '80%', '6 PM to 9 PM'), ('Wind', '11 mph', 'from the W'), ('UV', '5', 'Moderate'),
                          ('Air', '28', 'Good'), ('Sunset', '7:02 PM', ''), ('Humidity', '64%', 'Dew point 50°')])
        self.assertEqual([(h.time, h.description) for h in oak.hours[:2]], [('Now', 'Clear'), ('11 AM', 'Clear')])
        self.assertEqual(oak.clock, '10:40 AM')

    def test_only_your_place_has_plans_and_warnings(self):
        source = FixtureSource(FIXTURE_V71)
        lisbon = source.snapshot(source.load()[1])
        self.assertEqual((lisbon.plans, lisbon.alerts), ((), ()))
        self.assertEqual(lisbon.phone_sentence, lisbon.sentence)
        self.assertEqual([(c.after, c.label) for c in lisbon.changes], [('In 1 h', 'Sunset')])

    def test_plans_for_reads_the_hour_at_each_start(self):
        from types import SimpleNamespace
        from prairie_apps.weather_data import Hour, Snapshot, plans_for
        zone = timezone.utc
        at = lambda h, m=0: datetime(2026, 9, 23, h, m, tzinfo=zone)  # noqa: E731
        hours = tuple(Hour('Now' if h == 10 else f'{h}', f'{50 + h}°', 'rain' if h == 14 else 'sun', False,
                           at=at(h)) for h in range(10, 20))
        snap = Snapshot(Place('x', 'X'), '', '', '', 'sun', 'day', '', '', '', hours, (), ())
        events = [SimpleNamespace(summary='Done', start=at(8), end=at(9), all_day=False),
                  SimpleNamespace(summary='All day', start=at(0), end=at(23), all_day=True),
                  SimpleNamespace(summary='Walk', start=at(14, 30), end=at(15), all_day=False)]
        plans = plans_for(snap, events, at(10, 40))
        self.assertEqual([(p.title, p.time, p.temperature, p.jacket) for p in plans], [('Walk', '2:30 PM', '64°', True)])


class FixtureTests(unittest.TestCase):
    def test_provider_missing_temperatures_never_become_zero(self):
        def entry(hour, value):
            return {'time': f'2026-09-23T{hour:02d}:00:00Z',
                    'data': {'instant': {'details': {'air_temperature': value}}}}
        place = Place('x', 'Actual city', timezone='UTC')
        now = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)
        payload = {'properties': {'timeseries': [entry(10, 0), entry(11, None), entry(12, 2)]}}
        forecast = parse_forecast(payload, place, now=now)
        self.assertEqual(forecast.current.temperature_c, 0)
        self.assertEqual([h.temperature_c for h in forecast.hours], [0, 2])
        for invalid in (None, True, float('nan'), float('inf')):
            payload['properties']['timeseries'][0] = entry(10, invalid)
            with self.assertRaises(ValueError):
                parse_forecast(payload, place, now=now)

    def test_sample_matches_studio_at_1040(self):
        source = FixtureSource(FIXTURE)
        places = source.load()
        self.assertEqual([p.name for p in places], ['Oakland', 'Lisbon', 'Tokyo', 'Sacramento'])
        oak = source.snapshot(places[0])
        self.assertEqual((oak.temperature, oak.high, oak.low), ('66°', '71°', '53°'))
        self.assertEqual(oak.sentence, 'Dry for now. Light rain from around 6 PM, gone by 9 PM.')
        self.assertEqual(len(oak.hours), 25)
        self.assertEqual((oak.hours[0].label, oak.hours[0].temperature), ('Now', '64°'))
        self.assertEqual([h.precipitation for h in oak.hours[8:11]], ['60%', '80%', '60%'])
        self.assertEqual(len(oak.days), 10)
        self.assertEqual((oak.days[0].label, oak.days[0].low, oak.days[0].high), ('Today', '52°', '71°'))
        self.assertEqual(oak.days[1].label, 'Thu')
        self.assertEqual([m.value for m in oak.metrics], ['80%', '11', '5', '7:02 PM', '64%', '28'])
        self.assertEqual(source.snapshot(places[1]).temperature, '79°')
        self.assertEqual(source.snapshot(places[2]).temperature, '61°')
        self.assertEqual(source.snapshot(places[3]).temperature, '83°')

    def test_skies_and_condition_boundaries(self):
        p = FixtureSource(FIXTURE).samples['oak']
        self.assertEqual([sky_at(p,m) for m in (640,1110,400,1320)], ['day','rain','dusk','night'])
        self.assertEqual([condition_at(p,m) for m in (1019,1020,1080,1260)], ['clear','cloud','rain','clear'])
        self.assertEqual(rain_at(p,1140), 80)
        self.assertEqual(hm(1440), '12 AM')
        self.assertEqual(hm(1142), '7:02 PM')
        self.assertEqual(js_round(-1.5), -1)

    def test_fixture_never_opens_store_or_network(self):
        original = FIXTURE.read_bytes()
        with patch('prairie_apps.weather_backend.PlaceStore', side_effect=AssertionError('real store')), \
             patch('prairie_apps.weather_backend._request', side_effect=AssertionError('network')), \
             patch('prairie_apps.weather_backend.write_cache', side_effect=AssertionError('cache')):
            source = FixtureSource(FIXTURE)
            self.assertEqual(source.search('not-a-city'), ())
            resolved = source.search('64105')
            self.assertEqual((resolved[0].name,resolved[0].subtitle), ('Kansas City, MO','64105'))
            place = source.add(resolved[0])
            self.assertEqual((place.name,place.region), ('Kansas City','MO'))
            self.assertEqual(len(source.load()), 5)
            self.assertEqual(source.snapshot(place).place.uid, 'kc')
            source.add(resolved[0])
            self.assertEqual(len(source.load()), 5)
        self.assertEqual(FIXTURE.read_bytes(), original)

    def test_live_missing_values_are_not_made_up(self):
        p=Place('x','Actual city',timezone='UTC')
        f=CityForecast(p,'2026-09-23T10:40:00Z',Current(20,'clearsky_day'))
        snapshot=snapshot_from_forecast(f,UNITS_IMPERIAL,now=datetime(2026,9,23,10,40,tzinfo=timezone.utc))
        self.assertEqual(snapshot.temperature,'68°')
        self.assertEqual(snapshot.metrics,())
        self.assertEqual((snapshot.high,snapshot.low),('',''))
        self.assertFalse(snapshot.my_location)

    def test_invalid_sun_times_do_not_break_the_forecast(self):
        place = Place('x', 'Actual city', timezone='UTC')
        for sunset in ('2026-09-23T07:00:00Z', '2026-09-23T06:00:00Z'):
            current = Current(20, 'clearsky_day', sunrise='2026-09-23T07:00:00Z', sunset=sunset)
            forecast = CityForecast(place, '2026-09-23T10:40:00Z', current)
            snapshot = snapshot_from_forecast(forecast, UNITS_IMPERIAL,
                now=datetime(2026, 9, 23, 10, 40, tzinfo=timezone.utc))
            self.assertEqual(snapshot.temperature, '68°')
            self.assertNotIn('sun', [metric.key for metric in snapshot.metrics])

    def test_live_night_glyphs_preserve_clouds_and_do_not_show_the_sun(self):
        place = Place('x', 'Actual city', timezone='UTC')
        forecast = CityForecast(place, '2026-09-23T19:00:00Z', Current(20, 'partlycloudy_night'),
            hours=(HourPoint('2026-09-23T19:00:00Z', 20, 'clearsky_night'),
                   HourPoint('2026-09-23T20:00:00Z', 20, 'partlycloudy_night'),
                   HourPoint('2026-09-24T05:00:00Z', 20, 'clearsky_day')))
        snapshot = snapshot_from_forecast(forecast, UNITS_IMPERIAL,
            now=datetime(2026, 9, 23, 19, tzinfo=timezone.utc))
        self.assertEqual(weather_icon(snapshot.glyph), 'cloud-moon')
        self.assertEqual([weather_icon(h.condition, night=h.night) for h in snapshot.hours],
                         ['moon', 'cloud-moon', 'sun'])

    def test_live_sun_description_does_not_call_a_future_event_past(self):
        place = Place('x', 'Actual city', timezone='UTC')
        current = Current(20, 'clearsky_night', sunrise='2026-09-23T07:00:00Z', sunset='2026-09-23T19:00:00Z')
        forecast = CityForecast(place, '2026-09-23T05:00:00Z', current)
        before = snapshot_from_forecast(forecast, UNITS_IMPERIAL,
            now=datetime(2026, 9, 23, 5, tzinfo=timezone.utc))
        after = snapshot_from_forecast(forecast, UNITS_IMPERIAL,
            now=datetime(2026, 9, 23, 22, tzinfo=timezone.utc))
        self.assertEqual(before.metrics[0].description, 'Sunset at 7:00 PM')
        self.assertEqual(after.metrics[0].description, 'Sunset was 7:00 PM')

class CityTimeSummaryTests(unittest.TestCase):
    def test_first_future_hour_becomes_current_after_cache_rollover(self):
        forecast = CityForecast(Place('x', 'Tokyo', timezone='Asia/Tokyo'),
            '2026-09-23T23:40:00Z', Current(20, 'clearsky_day'),
            hours=(HourPoint('2026-09-24T00:00:00Z', 19, 'lightrain_day'),
                   HourPoint('2026-09-24T01:00:00Z', 21, 'clearsky_day')))
        original = forecast.to_dict()
        view = snapshot_from_forecast(forecast, UNITS_METRIC,
            now=datetime(2026, 9, 24, 0, 5, tzinfo=timezone.utc))
        self.assertEqual((view.temperature, view.condition), ('19°', 'Light Rain'))
        self.assertEqual(view.hours[0].label, 'Now')
        self.assertEqual(view.sentence, 'Clearing around 10:00 AM.')
        self.assertEqual(forecast.to_dict(), original)

    def test_first_future_hour_change_is_not_skipped(self):
        forecast = CityForecast(Place('x', 'Tokyo', timezone='Asia/Tokyo'),
            '2026-09-23T23:40:00Z', Current(20, 'clearsky_day'),
            hours=(HourPoint('2026-09-24T00:00:00Z', 19, 'lightrain_day'),))
        original = forecast.to_dict()
        view = snapshot_from_forecast(forecast, UNITS_METRIC,
            now=datetime(2026, 9, 23, 23, 40, tzinfo=timezone.utc))
        self.assertEqual((view.temperature, view.hours[0].label), ('20°', '9AM'))
        self.assertEqual(view.sentence, 'Rain arriving around 9:00 AM.')
        self.assertEqual(forecast.to_dict(), original)

    def test_arrival_uses_city_time_including_dst_and_fractional_offset(self):
        for zone, expected in (('Asia/Tokyo', '9:00 AM'),
                               ('America/Los_Angeles', '5:00 PM'),
                               ('Europe/Lisbon', '1:00 AM'),
                               ('Asia/Kathmandu', '5:45 AM'), ('UTC', '12:00 AM')):
            with self.subTest(zone=zone):
                forecast = CityForecast(Place('x', 'Actual city', timezone=zone),
                    '2026-09-23T23:40:00Z', Current(20, 'clearsky_day'),
                    hours=(HourPoint('2026-09-23T23:00:00Z', 20, 'clearsky_day'),
                           HourPoint('2026-09-24T00:00:00Z', 19, 'lightrain_day')))
                original = forecast.to_dict()
                view = snapshot_from_forecast(forecast, UNITS_METRIC,
                    now=datetime(2026, 9, 23, 23, 40, tzinfo=timezone.utc))
                self.assertEqual(view.sentence, f'Rain arriving around {expected}.')
                self.assertEqual(forecast.to_dict(), original)

    def test_cached_rollover_uses_next_change_in_city_time(self):
        forecast = CityForecast(Place('x', 'Tokyo', timezone='Asia/Tokyo'),
            '2026-09-23T23:40:00Z', Current(20, 'clearsky_day'),
            hours=(HourPoint('2026-09-23T23:00:00Z', 20, 'clearsky_day'),
                   HourPoint('2026-09-24T00:00:00Z', 19, 'lightrain_day'),
                   HourPoint('2026-09-24T01:00:00Z', 21, 'clearsky_day')))
        original = forecast.to_dict()
        view = snapshot_from_forecast(forecast, UNITS_METRIC,
            now=datetime(2026, 9, 24, 0, 5, tzinfo=timezone.utc))
        self.assertEqual((view.location, view.condition), ('9:05 AM', 'Light Rain'))
        self.assertEqual(view.sentence, 'Clearing around 10:00 AM.')
        self.assertEqual(forecast.to_dict(), original)

class CachedHourTests(unittest.TestCase):
    def test_elapsed_hour_is_removed_without_mutating_cached_forecast(self):
        place = Place('x', 'Actual city', timezone='UTC')
        forecast = CityForecast(place, '2026-09-23T10:40:00Z', Current(20, 'clearsky_day'),
            hours=(HourPoint('2026-09-23T10:00:00Z', 20, 'clearsky_day', 80),
                   HourPoint('2026-09-23T11:00:00Z', 21, 'lightrain_day', 60),
                   HourPoint('2026-09-23T12:00:00Z', 22, 'clearsky_day', 0)))
        original = forecast.to_dict()
        view = snapshot_from_forecast(forecast, UNITS_METRIC,
            now=datetime(2026, 9, 23, 11, 5, tzinfo=timezone.utc))
        self.assertEqual((view.temperature, view.sky), ('21°', 'rain'))
        self.assertEqual([(h.label, h.temperature) for h in view.hours], [('Now', '21°'), ('12PM', '22°')])
        self.assertEqual(next(m.value for m in view.metrics if m.key == 'rain'), '60%')
        self.assertEqual(forecast.to_dict(), original)

    def test_future_hour_does_not_become_now_or_replace_current_temperature(self):
        forecast = CityForecast(Place('x', 'Actual city', timezone='UTC'),
            '2026-09-23T10:40:00Z', Current(20, 'clearsky_day'),
            hours=(HourPoint('2026-09-23T11:00:00Z', 25, 'clearsky_day'),))
        view = snapshot_from_forecast(forecast, UNITS_METRIC,
            now=datetime(2026, 9, 23, 10, 40, tzinfo=timezone.utc))
        self.assertEqual(view.temperature, '20°')
        self.assertEqual((view.hours[0].label, view.hours[0].temperature), ('11AM', '25°'))

    def test_midnight_uses_current_day_range_and_drops_yesterday(self):
        forecast = CityForecast(Place('x', 'Actual city', timezone='UTC'),
            '2026-09-23T23:50:00Z', Current(20, 'clearsky_night', high_c=24, low_c=15),
            hours=(HourPoint('2026-09-23T23:00:00Z', 20, 'clearsky_night'),
                   HourPoint('2026-09-24T00:00:00Z', 18, 'clearsky_night')),
            days=(DayPoint('2026-09-23', 'clearsky_day', 24, 15),
                  DayPoint('2026-09-24', 'clearsky_day', 22, 12)))
        original = forecast.to_dict()
        view = snapshot_from_forecast(forecast, UNITS_METRIC,
            now=datetime(2026, 9, 24, 0, 5, tzinfo=timezone.utc))
        self.assertEqual((view.temperature, view.high, view.low), ('18°', '22°', '12°'))
        self.assertEqual([(day.label, day.high) for day in view.days], [('Today', '22°')])
        self.assertEqual(forecast.to_dict(), original)

class PlaceSourceTests(unittest.TestCase):
    def test_source_loads_lazily_and_tries_postal_lookup_after_local_index(self):
        provider = Mock()
        provider.search.return_value = ()
        provider.lookup.return_value = ('resolved postal place',)
        with patch('prairie_apps.weather_places.LocationSearch', return_value=provider) as create:
            source = WeatherPlaces()
            create.assert_not_called()
            self.assertEqual(source.search(''), ())
            create.assert_not_called()
            self.assertEqual(source.search('64105'), ('resolved postal place',))
            create.assert_called_once()
            provider.load.assert_called_once()
            provider.search.assert_called_once_with('64105')
            provider.lookup.assert_called_once_with('64105')
            provider.search.return_value = ('offline city',)
            self.assertEqual(source.search('Oakland'), ('offline city',))
            create.assert_called_once()
            provider.lookup.assert_called_once()

    def test_failed_postal_lookup_can_be_retried(self):
        provider = Mock()
        provider.search.return_value = ()
        provider.lookup.side_effect = [OSError('offline'), ('resolved',)]
        with patch('prairie_apps.weather_places.LocationSearch', return_value=provider):
            source = WeatherPlaces()
            with self.assertRaises(OSError):
                source.search('64105')
            self.assertEqual(source.search('64105'), ('resolved',))

class SavedPlacesTests(unittest.TestCase):
    def test_unsupported_saved_format_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'places.json'
            for original in ('null', '{"places":{"future":"format"}}', '3'):
                path.write_text(original)
                store = PlaceStore(path)
                self.assertEqual(store.list(), ())
                with self.assertRaises(ValueError):
                    store.add(Place('new', 'New city'))
                self.assertEqual(path.read_text(), original)

    def test_add_preserves_unknown_fields_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'places.json'
            place=Place('old','Existing city',latitude=12,longitude=34)
            raw={**asdict(place),'sync_id':'untouched','arbitrary':{'private':'keep'}}
            original=json.dumps({'places':[raw],'schema':8,'sync':{'cursor':'keep'}}).encode()
            path.write_bytes(original)
            store=PlaceStore(path)
            store.add(Place('new','New city'))
            payload=json.loads(path.read_text())
            self.assertEqual(payload['places'][0],raw)
            self.assertEqual(payload['sync'],{'cursor':'keep'})
            self.assertEqual(payload['schema'],8)
            backup=path.with_name(path.name+'.lumaui-backup')
            self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(backup.stat().st_mode & 0o777,0o600)
            store.add(Place('third','Third city'))
            self.assertEqual(backup.read_bytes(),original)

    def test_add_reads_latest_synced_list_and_preserves_unparsed_records(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'places.json'
            path.write_text('[]')
            store=PlaceStore(path)
            latest=[asdict(Place('synced','Arrived during session')),{'uid':'future','new_schema':True}]
            path.write_text(json.dumps(latest))
            store.add(Place('added','Added city'))
            payload=json.loads(path.read_text())
            self.assertEqual(payload[0],latest[0])
            self.assertIn(latest[1],payload)
            self.assertEqual(len(payload),3)

if __name__ == '__main__':
    unittest.main()
