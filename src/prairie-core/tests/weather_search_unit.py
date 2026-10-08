# SPDX-License-Identifier: Apache-2.0
"""Postal lookup preserves disambiguation, coordinates, and time zones."""
import unittest
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prairie_apps.weather_backend import LocationSearch

class SearchTests(unittest.TestCase):
    def test_postcode_preserves_locations_and_timezone(self):
        search = LocationSearch()
        with patch('prairie_apps.weather_backend._request', return_value=({'results': [
            {'name':'Kansas City', 'admin1':'Missouri', 'country':'United States',
             'latitude':39.1, 'longitude':-94.58, 'timezone':'America/Chicago'},
            {'name':'Bayonne', 'country':'France', 'latitude':43.49, 'longitude':-1.47},
        ]}, '')) as request:
            places = search.lookup('64105')
        self.assertEqual(places[0].timezone, 'America/Chicago')
        self.assertEqual(places[0].subtitle, 'Missouri, United States')
        self.assertEqual(places[1].country, 'France')
        self.assertEqual(places[0].longitude, -94.58)
        self.assertEqual(parse_qs(urlparse(request.call_args.args[0]).query)['name'], ['64105'])

    def test_no_results_and_network_failure_are_distinct(self):
        with patch('prairie_apps.weather_backend._request', return_value=({}, '')):
            self.assertEqual(LocationSearch().lookup('not a place'), ())
        with patch('prairie_apps.weather_backend._request', side_effect=OSError('offline')):
            with self.assertRaises(OSError): LocationSearch().lookup('64105')

if __name__ == '__main__': unittest.main()
