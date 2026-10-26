# SPDX-License-Identifier: Apache-2.0
"""Isolated real stores for native Home widget integration; never installed."""
from datetime import datetime, timedelta, timezone
import sys
from prairie_apps.weather_backend import PlaceStore
from prairie_apps.weather_model import Place, Current, CityForecast, cache_directory, write_cache
from prairie_apps import calendar_backend
now = datetime.now(timezone.utc)
if sys.argv[1] == 'seed':
    store = PlaceStore()
    place = Place('widget-integration', 'Widget integration city', '', '', 40.0, -90.0, 'UTC')
    store.add(place); store.set_units('metric')
    write_cache(CityForecast(place, now.isoformat(), Current(20.0, 'clearsky_day', high_c=22.0, low_c=12.0)), cache_directory())
    source = calendar_backend.create_calendar('Widget integration calendar', uid='widget-integration-calendar')
    calendar_backend.save_event(source, 'Widget integration event', now + timedelta(minutes=5), now + timedelta(minutes=35))
elif sys.argv[1] == 'imperial':
    PlaceStore().set_units('imperial')
elif sys.argv[1] == 'remove':
    store = PlaceStore()
    for place in store.list(): store.remove(place.uid)
