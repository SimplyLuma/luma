#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Weather's store, its parser and its offline contract.

The application renders from the cache and lets the network replace it, so the
test that matters most here is the one where the network is gone: the payload
on disk is still readable and still complete.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sys
import json
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))
from prairie_apps.weather_backend import (  # noqa: E402
    LocationSearch, PlaceStore, cached_forecast, fetch_forecast, needs_refresh, new_place, parse_forecast,
)
from prairie_apps.weather_model import Place, read_cache, write_cache  # noqa: E402


CHICAGO = Place(uid="", name="Chicago", region="Illinois", country="United States",
                latitude=41.8781, longitude=-87.6298, timezone="America/Chicago")

# MET Norway's own shape, from locationforecast/2.0/complete.
PAYLOAD = {"properties": {"timeseries": [
    {"time": "2026-08-13T12:00:00Z", "data": {
        "instant": {"details": {"air_temperature": 21.4, "relative_humidity": 62,
                                "wind_speed": 3.2, "wind_from_direction": 200.0,
                                "apparent_air_temperature": 20.8,
                                "air_pressure_at_sea_level": 1012.0,
                                "ultraviolet_index_clear_sky": 4.2}},
        "next_1_hours": {"summary": {"symbol_code": "partlycloudy_day"},
                         "details": {"precipitation_amount": 0.1}}}},
    {"time": "2026-08-13T13:00:00Z", "data": {
        "instant": {"details": {"air_temperature": 22.0,
                                "air_pressure_at_sea_level": 1014.0}},
        "next_1_hours": {"summary": {"symbol_code": "lightrain"},
                         "details": {"precipitation_amount": 0.4}}}},
]}}
NOON = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


class WeatherBackendTests(unittest.TestCase):
    def test_temperature_preference_survives_restart_without_losing_sync_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'places.json'
            store = PlaceStore(path)
            city = store.add(new_place(CHICAGO))
            original = json.loads(path.read_text())
            path.write_text(json.dumps({'places': original, 'sync': {'cursor': 'retained'},
                                        'preferences': {'other': True}}))
            store.set_units('imperial')
            self.assertEqual(PlaceStore(path).unit_setting(), 'imperial')
            store.set_units('metric')
            restored = PlaceStore(path)
            self.assertEqual(restored.unit_setting(), 'metric')
            self.assertEqual(restored.list(), (city,))
            payload = json.loads(path.read_text())
            self.assertEqual(payload['sync'], {'cursor': 'retained'})
            self.assertEqual(payload['preferences'], {'other': True, 'units': 'metric'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): restored.set_units('invalid')
            self.assertEqual(PlaceStore(path).unit_setting(), 'metric')

    def test_invalid_preference_file_does_not_crash_or_overwrite_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'places.json'
            path.write_text('unfinished synced document')
            original = path.read_bytes()
            store = PlaceStore(path)
            self.assertEqual(store.unit_setting(), 'automatic')
            with self.assertRaises(ValueError): store.set_units('metric')
            self.assertEqual(path.read_bytes(), original)

    def test_us_zip_missing_from_city_index_resolves_postal_place(self):
        search = object.__new__(LocationSearch)
        search._index = [CHICAGO, Place(uid='', name='Kansas City', region='Missouri',
                                        latitude=39.0997, longitude=-94.5786,
                                        timezone='America/Chicago')]
        postal = {'country': 'United States', 'places': [
            {'place name': 'Shawnee Mission', 'state': 'Kansas',
             'latitude': '39.0215', 'longitude': '-94.7055'}]}
        with mock.patch('prairie_apps.weather_backend._request', return_value=(postal, '')) as request:
            places = search.lookup('66203')
        self.assertEqual(len(places), 1)
        self.assertEqual((places[0].name, places[0].region, places[0].timezone),
                         ('Shawnee Mission', 'Kansas', 'America/Chicago'))
        self.assertAlmostEqual(places[0].latitude, 39.0215)
        self.assertIn('/us/66203', request.call_args.args[0])

    def test_saved_place_lifecycle_is_private(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "places.json"
            store = PlaceStore(path)
            record = store.add(new_place(CHICAGO))
            self.assertEqual(store.list(), (record,))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(store.contains(41.8781, -87.6298))
            index, removed = store.remove(record.uid)
            self.assertEqual((index, removed), (0, record))
            self.assertEqual(store.list(), ())
            # Undo puts a city back where it was, which is why removal reports
            # the index it took it from.
            store.insert(index, removed)
            self.assertEqual(store.list(), (record,))

    def test_complete_forecast_parses_current_conditions(self):
        forecast = parse_forecast(PAYLOAD, new_place(CHICAGO), now=NOON)
        self.assertEqual(forecast.current.symbol, "partlycloudy_day")
        self.assertEqual(forecast.current.temperature_c, 21.4)
        self.assertEqual(forecast.current.apparent_c, 20.8)
        self.assertEqual(forecast.current.uv_index, 4.2)
        self.assertEqual(forecast.current.pressure_trend, "Rising")
        # MET's forecast carries no visibility, so that row is never drawn.
        self.assertIsNone(forecast.current.visibility_m)
        # The hours are the city's local time, not UTC.
        self.assertTrue(forecast.hours[0].time.startswith("2026-08-13T07:00"))

    def test_empty_response_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_forecast({}, new_place(CHICAGO))

    def test_a_failed_refresh_leaves_the_cache_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            place = new_place(CHICAGO)
            write_cache(parse_forecast(PAYLOAD, place, now=NOON), root)
            with mock.patch("prairie_apps.weather_backend.urlopen",
                            side_effect=OSError("offline")):
                with self.assertRaises(OSError):
                    fetch_forecast(place, cache_root=root, now=NOON)
            # The application never shows the failure; it shows this.
            offline = cached_forecast(place, cache_root=root)
            self.assertIsNotNone(offline)
            self.assertEqual(offline.current.temperature_c, 21.4)
            self.assertEqual(len(offline.hours), 2)
            self.assertEqual(read_cache(place.uid, root).place, place)

    def test_staleness_is_known_even_though_nothing_renders_it(self):
        forecast = parse_forecast(PAYLOAD, new_place(CHICAGO), now=NOON)
        self.assertFalse(needs_refresh(forecast))
        self.assertTrue(needs_refresh(None))
        self.assertIsNotNone(forecast.age_seconds())


if __name__ == "__main__":
    unittest.main()
