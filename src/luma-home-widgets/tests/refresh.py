# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
spec = importlib.util.spec_from_file_location('refresh', Path(__file__).parents[1]/'weather-refresh.py')
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)

class RefreshTests(unittest.TestCase):
    def backend(self, places, stale=True):
        return SimpleNamespace(PlaceStore=lambda: SimpleNamespace(list=lambda: places),
            cached_forecast=Mock(return_value='cache'), needs_refresh=Mock(return_value=stale),
            fetch_forecast=Mock())

    def test_no_city_means_no_request(self):
        backend=self.backend([])
        self.assertFalse(refresh.refresh_saved_city(backend))
        backend.fetch_forecast.assert_not_called()

    def test_fresh_cache_respects_provider_refresh_policy(self):
        backend=self.backend(['city'],False)
        self.assertFalse(refresh.refresh_saved_city(backend))
        backend.needs_refresh.assert_called_once_with('cache')
        backend.fetch_forecast.assert_not_called()

    def test_widget_city_refreshes_without_opening_app(self):
        backend=self.backend(['widget city','other city'])
        self.assertTrue(refresh.refresh_saved_city(backend))
        backend.fetch_forecast.assert_called_once_with('widget city')

    def test_failure_keeps_existing_cache_under_provider_ownership(self):
        backend=self.backend(['city'])
        backend.fetch_forecast.side_effect=OSError('offline')
        with self.assertRaises(OSError):refresh.refresh_saved_city(backend)
        backend.cached_forecast.assert_called_once_with('city')

unittest.main()
