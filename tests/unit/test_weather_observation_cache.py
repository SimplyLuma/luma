# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import fcntl
import os
import unittest
from unittest.mock import Mock
from prairie_apps.weather_model import Place
from prairie_apps.weather_observation import Observation
from prairie_apps.weather_observation_cache import current_observation, cached_observation

class ObservationCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = datetime.now(timezone.utc)
        self.place = Place('city', 'City', latitude=39., longitude=-94., timezone='America/Chicago')
        self.reading = Observation('STATION', 'Station', self.now.isoformat(), 19., 'Clear', 'clearsky_day', 1.)

    def test_separate_app_and_timer_share_one_request(self):
        fetch = Mock(return_value=self.reading)
        for _ in range(2):
            self.assertEqual(current_observation(self.place, cache_root=self.root, now=self.now, fetch=fetch), self.reading)
        fetch.assert_called_once()
        self.assertEqual(cached_observation(self.place, cache_root=self.root, now=self.now), self.reading)

    def test_offline_refresh_preserves_fresh_observation_and_backs_off(self):
        current_observation(self.place, cache_root=self.root, now=self.now, fetch=lambda _: self.reading)
        later = self.now + timedelta(minutes=16)
        fetch = Mock(side_effect=OSError('offline'))
        for _ in range(2):
            self.assertEqual(current_observation(self.place, cache_root=self.root, now=later, fetch=fetch), self.reading)
        fetch.assert_called_once()

    def test_expired_observation_is_not_current(self):
        current_observation(self.place, cache_root=self.root, now=self.now, fetch=lambda _: self.reading)
        self.assertIsNone(cached_observation(self.place, cache_root=self.root, now=self.now + timedelta(minutes=91)))

    def test_empty_result_is_cached_to_avoid_repeated_requests(self):
        fetch = Mock(return_value=None)
        for _ in range(2):
            self.assertIsNone(current_observation(self.place, cache_root=self.root, now=self.now, fetch=fetch))
        fetch.assert_called_once()

    def test_inflight_timer_does_not_trigger_another_app_request(self):
        current_observation(self.place, cache_root=self.root, now=self.now, fetch=lambda _: self.reading)
        fd = os.open(self.root/'city-observation.json.lock', os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fetch = Mock()
            self.assertEqual(current_observation(self.place, cache_root=self.root,
                             now=self.now + timedelta(minutes=16), fetch=fetch), self.reading)
            fetch.assert_not_called()
        finally:
            os.close(fd)
