# SPDX-License-Identifier: Apache-2.0
"""Fixture UI actions run once even when picking causes a second load."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src/prairie-core'), str(ROOT / 'src/luma-platform/appkit')]
from prairie_apps import weather
from prairie_apps.weather import WeatherWindow
from prairie_apps.weather_backend import PlaceStore
from prairie_apps.weather_model import Place
from gi.repository import Gtk


class PreviewIdentityTests(unittest.TestCase):
    def test_launcher_override_reaches_the_application_identity(self):
        production_id = 'org.projectluma.Weather'
        self.assertEqual(weather.APP_ID, production_id)
        self.assertEqual(weather.WeatherApplication().get_application_id(), production_id)
        # The Prairie preview runner mutates APP_ID before calling main().
        with patch.object(weather, 'APP_ID', production_id + '.LumaUIPreview'):
            application = weather.WeatherApplication()
            self.assertEqual(application.get_application_id(), production_id + '.LumaUIPreview')
            self.assertEqual(application.get_windows(), [])
            self.assertEqual(weather.ICON_NAME, production_id)
        self.assertEqual(weather.APP_ID, production_id)

class FixtureActionTests(unittest.TestCase):
    def test_every_desktop_state_is_captured_on_phone(self):
        # v71's phone has no sidebar, search or ☰ (Places is in the bar), so the sidebar's own
        # states and the compact window sizes are desktop-only; everything else is on the phone.
        scenario = json.loads((ROOT / 'tools/lumaui-conform/scenarios/weather.json').read_text())
        desktop_only = {'zip-search', 'no-match', 'added-city', 'places-drawer'}
        expected = {s['name'] for s in scenario['states'] if 'window_size' not in s} - desktop_only
        self.assertEqual(set(scenario['phone_states']), expected)
        self.assertIn('luma-next-71.html', scenario['spec']['url'])

    def test_fixture_select_activates_the_row_in_the_drawer(self):
        row = Mock()
        row.place.uid = 'lis'
        window = Mock(closed=False, selected='oak')
        window.get_width.return_value = 390
        window.toggle.shown = False
        window.sidebar.list.get_first_child.return_value = row
        def activate(_signal, selected):
            window.selected = selected.place.uid
        window.sidebar.list.emit.side_effect = activate
        with patch.dict('os.environ', {'LUMA_WEATHER_SELECT':'lis'}, clear=True):
            self.assertFalse(WeatherWindow._fixture_select(window))
        window.sidebar.list.select_row.assert_called_once_with(row)
        window.sidebar.list.emit.assert_called_once_with('row-activated', row)
        self.assertEqual(window.selected, 'lis')

    def test_add_reload_does_not_replay_the_search_and_pick(self):
        place = SimpleNamespace(uid='oak')
        window = Mock()
        window.generation = 1
        window.closed = False
        window.selected = 'oak'
        window.fixture = True
        window._fixture_started = False
        with patch.dict('os.environ', {'LUMA_WEATHER_QUERY':'64105', 'LUMA_WEATHER_PICK':'1'}, clear=True), \
             patch('prairie_apps.weather.GLib.timeout_add') as timer:
            WeatherWindow._loaded(window, 1, (place,), {}, '')
            self.assertEqual(timer.call_count, 1)
            window.foot.set_text.assert_called_once_with('64105')
            WeatherWindow._loaded(window, 1, (place,), {}, '')
            self.assertEqual(timer.call_count, 1)
            window.foot.set_text.assert_called_once_with('64105')


class MinuteRefreshTests(unittest.TestCase):
    def test_older_minute_completion_cannot_replace_newer_conditions(self):
        place = SimpleNamespace(uid='oak')
        window = SimpleNamespace(closed=False, fixture=False, generation=7,
                                 places=(place,), snapshots={}, selected='oak',
                                 _refresh_serial={},
                                 _refresh_place=Mock(), _render_sidebar=Mock(),
                                 _render=Mock())
        window._queue_refresh = lambda generation, place: WeatherWindow._queue_refresh(window, generation, place)
        scheduled = []
        with patch('prairie_apps.weather.threading.Thread',
                   side_effect=lambda **kwargs: scheduled.append(kwargs) or Mock()):
            self.assertTrue(WeatherWindow._refresh_minute(window))
            self.assertTrue(WeatherWindow._refresh_minute(window))
        self.assertEqual(len(scheduled), 2)
        first_generation, _, first_serial = scheduled[0]['args']
        second_generation, _, second_serial = scheduled[1]['args']
        WeatherWindow._refreshed(window, second_generation, place.uid, 'new', second_serial)
        WeatherWindow._refreshed(window, first_generation, place.uid, 'old', first_serial)
        self.assertEqual(window.snapshots[place.uid], 'new')


class ForecastProgressTests(unittest.TestCase):
    def test_forecast_is_published_before_slow_optional_observation(self):
        place = Place('city', 'Test City')
        essential, sunrise, completed = 'essential', 'sunrise', 'completed'
        window = SimpleNamespace(closed=False, fixture=False, generation=1,
                                 places=(place,), snapshots={}, selected=place.uid,
                                 units='metric', _refresh_serial={place.uid: 1},
                                 _render_sidebar=Mock(), _render=Mock(),
                                 _current_alerts=Mock(return_value=()),
                                 _with_plans=lambda _place, snapshot: snapshot)
        window._refreshed = lambda *args: WeatherWindow._refreshed(window, *args)
        published_before_sun = []
        def fetch(_place, *, on_forecast=None):
            if on_forecast:
                on_forecast(essential)
            published_before_sun.append(window.snapshots.get(place.uid))
            return sunrise
        def observation(_place):
            self.assertEqual(window.snapshots.get(place.uid), sunrise,
                             'optional observations must not hold back usable weather')
            return 'observation'
        window._current_observation = observation
        with patch.object(weather, 'cached_forecast', return_value=None), \
             patch.object(weather, 'needs_refresh', return_value=True), \
             patch.object(weather, 'fetch_forecast', side_effect=fetch), \
             patch.object(weather, 'snapshot_from_forecast',
                          side_effect=lambda forecast, _units, **extras: completed if extras.get('observation') else forecast), \
             patch.object(weather.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)):
            WeatherWindow._refresh_place(window, 1, place, 1)
        self.assertEqual(published_before_sun, [essential])
        self.assertEqual(window.snapshots[place.uid], completed)

    def test_cached_forecast_is_published_before_optional_observation(self):
        place = Place('city', 'Test City')
        window = SimpleNamespace(closed=False, fixture=False, generation=1,
                                 places=(place,), snapshots={}, selected=place.uid,
                                 units='metric', _refresh_serial={place.uid: 1},
                                 _render_sidebar=Mock(), _render=Mock(),
                                 _current_alerts=Mock(return_value=()),
                                 _with_plans=lambda _place, snapshot: snapshot)
        window._refreshed = lambda *args: WeatherWindow._refreshed(window, *args)
        def observation(_place):
            self.assertEqual(window.snapshots.get(place.uid), 'cached')
            return None
        window._current_observation = observation
        with patch.object(weather, 'cached_forecast', return_value='cached'), \
             patch.object(weather, 'needs_refresh', return_value=False), \
             patch.object(weather, 'snapshot_from_forecast', side_effect=lambda forecast, _units, **_extras: forecast), \
             patch.object(weather.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)):
            WeatherWindow._refresh_place(window, 1, place, 1)
        self.assertEqual(window.snapshots[place.uid], 'cached')


class CityContextTests(unittest.TestCase):
    def test_right_click_action_targets_its_own_row(self):
        place = Place('second', 'Second')
        window = SimpleNamespace(_remove=Mock())
        row = Gtk.ListBoxRow()
        WeatherWindow._install_city_menu(window, row, place)
        controllers = row.observe_controllers()
        self.assertTrue(any(isinstance(controllers.get_item(index), Gtk.GestureClick)
                            and controllers.get_item(index).get_button() == 3
                            for index in range(controllers.get_n_items())))
        self.assertTrue(row.activate_action('city.remove', None))
        window._remove.assert_called_once_with(place)
        row.emit('destroy')

    def test_remove_persists_fallback_and_undo_restores_selected_city(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Place('first', 'First'), Place('second', 'Second')
            store = PlaceStore(Path(folder) / 'places.json')
            store.add(first)
            store.add(second)
            window = SimpleNamespace(fixture=False, store=store,
                                     store_lock=threading.RLock(), places=(first, second),
                                     selected='second', _removed=None, generation=0,
                                     _render_sidebar=Mock(), _render=Mock(), _reload=Mock(),
                                     get_width=lambda: 1180, host=Mock())
            window._rebuild_rows_keeping_selection = lambda: WeatherWindow._rebuild_rows_keeping_selection(window)
            window.undo_removal = lambda: WeatherWindow.undo_removal(window)
            window._toast = Mock()
            with patch('prairie_apps.weather.Toast.show'):
                WeatherWindow._remove(window, second)
                self.assertEqual(window.selected, 'first')
                self.assertEqual([p.uid for p in PlaceStore(store.path).list()], ['first'])
                WeatherWindow.undo_removal(window)
            self.assertEqual(window.selected, 'second')
            self.assertEqual([p.uid for p in PlaceStore(store.path).list()], ['first', 'second'])

if __name__ == '__main__':
    unittest.main()
