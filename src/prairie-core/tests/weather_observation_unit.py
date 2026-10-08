# SPDX-License-Identifier: Apache-2.0
"""Live observation provenance and honest rain reporting without network."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prairie_apps.weather_data import snapshot_from_forecast
from prairie_apps.weather_model import CityForecast, Current, HourPoint, Place, UNITS_IMPERIAL
from prairie_apps.weather_observation import fetch_observation


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 26, 21, 40, tzinfo=timezone.utc)
        self.place = Place('kc', 'Kansas City', 'MO', 'US', 39.0997, -94.5786, 'America/Chicago')

    def _payload(self, *, downtown_age=10):
        stations = [{'properties': {'stationIdentifier': code, 'name': name},
                     'geometry': {'coordinates': [lon, lat]}}
                    for code, name, lon, lat in (
                        ('KMKC', 'Kansas City Downtown Airport', -94.59694, 39.12083),
                        ('KLXT', "Lee's Summit Regional Airport", -94.37167, 38.95972),
                        ('KMCI', 'Kansas City International Airport', -94.73056, 39.29722))]
        def reading(age, temperature, description, weather):
            return {'properties': {'timestamp': (self.now - timedelta(minutes=age)).isoformat(),
                                   'temperature': {'unitCode': 'wmoUnit:degC', 'value': temperature},
                                   'textDescription': description, 'presentWeather': weather,
                                   'precipitationLastHour': {'value': None}}}
        return {
            'points': {'properties': {'observationStations':
                                      'https://api.weather.gov/gridpoints/EAX/44,51/stations'}},
            'stations': {'features': stations},
            'KMKC': reading(downtown_age, 23, 'Mostly Cloudy', []),
            'KLXT': reading(15, 23, 'Mostly Cloudy', []),
            'KMCI': reading(5, 22, 'Light Rain', [{'weather': 'rain'}]),
        }

    def test_nearest_temperature_and_separate_nearby_rain(self):
        payload = self._payload()
        def source(url):
            return payload['points' if '/points/' in url else
                           'stations' if url.endswith('/stations') else
                           url.split('/stations/')[1].split('/')[0]]
        with patch('prairie_apps.weather_observation._json', side_effect=source):
            observed = fetch_observation(self.place, now=self.now)
        self.assertEqual((observed.station, observed.temperature_c, observed.description),
                         ('KMKC', 23, 'Mostly Cloudy'))
        self.assertEqual((observed.nearby_rain, observed.nearby_rain_station),
                         ('Light Rain', 'Kansas City International Airport'))
        forecast = CityForecast(self.place, self.now.isoformat(),
                                Current(25.7, 'clearsky_day', precipitation_mm=0),
                                (HourPoint(self.now.isoformat(), 25.7, 'clearsky_day',
                                           precipitation_mm=0),))
        snapshot = snapshot_from_forecast(forecast, UNITS_IMPERIAL, now=self.now,
                                          observation=observed)
        self.assertEqual(snapshot.temperature, '73°')
        self.assertEqual(snapshot.condition, 'Mostly Cloudy')
        self.assertIn('Kansas City Downtown Airport', snapshot.provenance)
        self.assertEqual((snapshot.metrics[0].title, snapshot.metrics[0].value),
                         ('Rain nearby', 'Light Rain'))
        self.assertNotIn('0.00', str(snapshot.metrics))

    def test_stale_station_is_rejected_and_fallback_is_labeled_forecast(self):
        payload = self._payload(downtown_age=120)
        payload['KLXT']['properties']['timestamp'] = (self.now - timedelta(hours=2)).isoformat()
        payload['KMCI']['properties']['timestamp'] = (self.now - timedelta(hours=2)).isoformat()
        def source(url):
            return payload['points' if '/points/' in url else
                           'stations' if url.endswith('/stations') else
                           url.split('/stations/')[1].split('/')[0]]
        with patch('prairie_apps.weather_observation._json', side_effect=source):
            self.assertIsNone(fetch_observation(self.place, now=self.now))
        forecast = CityForecast(self.place, self.now.isoformat(), Current(25.7, 'clearsky_day',
                                                                         precipitation_mm=0))
        snapshot = snapshot_from_forecast(forecast, UNITS_IMPERIAL, now=self.now)
        self.assertEqual(snapshot.temperature, '78°')
        self.assertIn('MET forecast', snapshot.provenance)
        self.assertEqual(snapshot.metrics, ())
        self.assertNotIn('0.00', str(snapshot.metrics))


if __name__ == '__main__':
    unittest.main()
