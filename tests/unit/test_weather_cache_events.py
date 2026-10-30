# SPDX-License-Identifier: Apache-2.0
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from prairie_apps.weather_model import Place, cache_path

source = ast.parse((Path(__file__).parents[2]/'src/prairie-core/prairie_apps/weather.py').read_text())
names = {'_weather_cache_changed', '_refresh_changed_weather'}
methods = [n for cls in source.body if isinstance(cls, ast.ClassDef) for n in cls.body
           if isinstance(n, ast.FunctionDef) and n.name in names]
Events = SimpleNamespace(CHANGES_DONE_HINT=1, MOVED_IN=2, RENAMED=3, CREATED=4)
GLib = SimpleNamespace(timeout_add=Mock(return_value=7))
namespace = {'Gio':SimpleNamespace(FileMonitorEvent=Events), 'GLib':GLib,
             'cache_directory':lambda:Path('/cache'), 'cache_path':cache_path}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])), '<weather event handlers>', 'exec'),namespace)
class View:
    _weather_cache_changed = namespace['_weather_cache_changed']
    _refresh_changed_weather = namespace['_refresh_changed_weather']
    def __init__(self):
        self.closed=False;self.places=[Place('city','City')];self.generation=2
        self._cache_refresh_source=0;self._cache_changed_uids=set();self._queue_refresh=Mock()

class CacheEventTests(unittest.TestCase):
    def setUp(self):
        GLib.timeout_add.reset_mock();self.view=View()
    def event(self,name,event=2):
        self.view._weather_cache_changed(None,SimpleNamespace(get_basename=lambda:name),None,event)
    def test_atomic_station_update_is_coalesced_and_updates_current_view(self):
        self.event('city-observation.json');self.event('city-observation.json')
        GLib.timeout_add.assert_called_once()
        self.view._refresh_changed_weather()
        self.view._queue_refresh.assert_called_once_with(2,self.view.places[0])
        self.assertFalse(self.view._cache_changed_uids)
    def test_other_cities_temporary_writes_and_closed_view_do_not_refresh(self):
        self.event('other-observation.json');self.event('.observation-tmp')
        self.view.closed=True;self.event('city-observation.json')
        GLib.timeout_add.assert_not_called()
    def test_forecast_updates_also_reach_the_application(self):
        self.event('city.json',3);self.view._refresh_changed_weather()
        self.view._queue_refresh.assert_called_once()
