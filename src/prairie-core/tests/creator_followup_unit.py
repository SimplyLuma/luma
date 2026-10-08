#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Reported natural-language order and installed setup persistence boundaries."""
from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prairie_apps.setup_location import read_setup_location
from prairie_apps.setup_location import SetupLocation
from prairie_apps.tasks_parse import parse_task
from prairie_apps.weather_backend import PlaceStore
from prairie_apps.clock_backend import ClockStore


class FollowupTests(unittest.TestCase):
    def test_setup_failure_never_publishes_partial_file_or_replaces_concurrent_owner(self):
        location = SetupLocation('Europe/Paris', {'name': 'Paris', 'region': '', 'country': 'France',
            'latitude': 48.866, 'longitude': 2.333, 'timezone': 'Europe/Paris'})
        with tempfile.TemporaryDirectory() as root:
            store = PlaceStore(Path(root) / 'places.json')
            with patch('prairie_apps.weather_backend.json.dump', side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):
                    store.seed_setup_place(location)
            self.assertFalse(store.path.exists())
            self.assertEqual(list(Path(root).iterdir()), [])
            def competing_owner(*_args):
                store.path.write_text('[]')
                raise FileExistsError()
            with patch('prairie_apps.weather_backend.os.link', side_effect=competing_owner):
                self.assertFalse(store.seed_setup_place(location))
            self.assertEqual(store.path.read_text(), '[]')
            self.assertEqual(store.list(), ())
    def test_time_before_and_after_day_follow_calendar_bare_hour(self):
        for clock, hour in [('3', 15), ('3:00', 15), ('3:00 PM', 15), ('03:00 AM', 3), ('15:00', 15)]:
            for phrase in [f'Call John at {clock} tomorrow', f'Call John tomorrow at {clock}']:
                with self.subTest(phrase=phrase):
                    parsed = parse_task(phrase, today=date(2026, 10, 5))
                    self.assertEqual(parsed.title, 'Call John')
                    self.assertIsInstance(parsed.due, datetime)
                    self.assertEqual(parsed.due.date(), date(2026, 10, 6))
                    self.assertEqual((parsed.due.hour, parsed.due.minute), (hour, 0))

    def test_invalid_time_and_unqualified_quantities_remain_in_title(self):
        for phrase in ['Buy 3 apples tomorrow', 'Call John at 25:00 tomorrow', 'Call John at 3:99 tomorrow']:
            parsed = parse_task(phrase, today=date(2026, 10, 5))
            self.assertEqual(parsed.title, phrase.replace(' tomorrow', ''))
            self.assertNotIsInstance(parsed.due, datetime)

    def test_installer_identity_seeds_weather_and_clock_once(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            identity = root / 'setup.json'
            identity.write_text(json.dumps({'schema': 1, 'source': 'atlas', 'timezone': 'Europe/Paris',
                'place': {'name': 'Paris', 'region': '', 'country': 'France', 'latitude': 48.866,
                          'longitude': 2.333, 'timezone': 'Europe/Paris'}}))
            location = read_setup_location(identity)
            weather = PlaceStore(root / 'weather.json')
            self.assertTrue(weather.seed_setup_place(location))
            place, = weather.list()
            self.assertEqual((place.name, place.timezone, place.latitude), ('Paris', 'Europe/Paris', 48.866))
            weather.remove(place.uid)
            self.assertFalse(PlaceStore(weather.path).seed_setup_place(location))
            self.assertEqual(PlaceStore(weather.path).list(), ())
            clocks = ClockStore(root / 'clock.json')
            self.assertTrue(clocks.seed_home_clock(location.timezone))
            card, = clocks.world_clocks()
            self.assertEqual((card.label, card.zone), ('Paris', 'Europe/Paris'))

    def test_setup_bad_coordinates_do_not_invent_city_or_overwrite_existing_store(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            identity = root / 'setup.json'
            for coordinate in [float('nan'), float('inf'), True, 91, '48']:
                identity.write_text(json.dumps({'schema': 1, 'source': 'atlas', 'timezone': 'UTC',
                    'place': {'name': 'Invalid', 'latitude': coordinate, 'longitude': 0, 'timezone': 'UTC'}}))
                setup = read_setup_location(identity)
                self.assertIsNone(setup.place)
                store = PlaceStore(root / 'weather.json')
                self.assertFalse(store.seed_setup_place(setup))
                self.assertFalse(store.path.exists())
            identity.write_text('{broken')
            self.assertIsNone(read_setup_location(identity))


if __name__ == '__main__':
    unittest.main()
