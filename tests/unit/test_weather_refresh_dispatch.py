# SPDX-License-Identifier: Apache-2.0
"""Late and duplicate weather results must preserve the displayed forecast."""
import ast
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src/prairie-core'))
from prairie_apps.weather_data import Snapshot
from prairie_apps.weather_model import Place

window = next(n for n in ast.parse((ROOT/'src/prairie-core/prairie_apps/weather.py').read_text()).body
              if isinstance(n, ast.ClassDef) and n.name == 'WeatherWindow')
method = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == '_refreshed')
namespace = {}
exec(ast.unparse(method), namespace)
refreshed = namespace['_refreshed']

class RefreshDispatchTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = Snapshot(Place('city', 'City', 'Region'), '10:00 AM', '75°',
                                 'Clear', 'sun', 'day', '78°', '59°', 'Clear', (), (), ())
        self.window = SimpleNamespace(closed=False, generation=3, _refresh_serial={'city':2},
            selected='city', snapshots={'city':self.snapshot}, _render_sidebar=Mock(), _render=Mock())

    def test_equal_result_preserves_rows_and_content(self):
        self.assertFalse(refreshed(self.window, 3, 'city', replace(self.snapshot), 2))
        self.window._render_sidebar.assert_not_called()
        self.window._render.assert_not_called()
        self.assertIs(self.window.snapshots['city'], self.snapshot)

    def test_changed_forecast_updates_selected_content(self):
        updated = replace(self.snapshot, temperature='76°')
        refreshed(self.window, 3, 'city', updated, 2)
        self.assertIs(self.window.snapshots['city'], updated)
        self.window._render_sidebar.assert_called_once_with()
        self.window._render.assert_called_once_with()

    def test_city_clock_still_advances(self):
        refreshed(self.window, 3, 'city', replace(self.snapshot, clock='10:01 AM'), 2)
        self.window._render.assert_called_once_with()

    def test_optional_weather_details_still_update(self):
        refreshed(self.window, 3, 'city', replace(self.snapshot, provenance='NWS observed'), 2)
        self.window._render.assert_called_once_with()

    def test_nonselected_city_does_not_rebuild_selected_content(self):
        self.window.selected = 'other'
        refreshed(self.window, 3, 'city', replace(self.snapshot, temperature='76°'), 2)
        self.window._render_sidebar.assert_called_once_with()
        self.window._render.assert_not_called()

    def test_failed_refresh_preserves_usable_forecast(self):
        refreshed(self.window, 3, 'city', None, 2)
        self.assertIs(self.window.snapshots['city'], self.snapshot)
        self.window._render_sidebar.assert_not_called()
        self.window._render.assert_not_called()

    def test_initial_failure_still_reports_unavailable(self):
        self.window.snapshots.clear()
        refreshed(self.window, 3, 'city', None, 2)
        self.window._render.assert_called_once_with(message='Forecast unavailable. Try again when connected.')

    def test_late_results_cannot_replace_newer_data(self):
        for generation, serial in ((2,2),(3,1)):
            refreshed(self.window, generation, 'city', replace(self.snapshot, temperature='90°'), serial)
        self.assertIs(self.window.snapshots['city'], self.snapshot)
        self.window._render_sidebar.assert_not_called()
        self.window._render.assert_not_called()
