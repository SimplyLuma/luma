# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
from pathlib import Path
root = tempfile.TemporaryDirectory()
os.environ['XDG_DATA_HOME'] = str(Path(root.name)/'data')
os.environ['XDG_CACHE_HOME'] = str(Path(root.name)/'cache')
from gi.repository import GLib
import gi
gi.require_version('LumaWidgets', '1.0')
from gi.repository import LumaWidgets
import json
from datetime import datetime, timedelta, timezone
places = Path(os.environ['XDG_DATA_HOME'])/'prairie/weather/places.json'
cache = Path(os.environ['XDG_CACHE_HOME'])/'prairie-weather/place.json'
places.parent.mkdir(parents=True)
cache.parent.mkdir(parents=True)

def snapshot():
    model = LumaWidgets.Model.new('org.gnome.Shell.CalendarServer')
    data = json.loads(model.dup_snapshot())
    model.run_dispose()
    return data

assert snapshot()['weather']['value'] == '—'
places.write_text(json.dumps({'places': [{'uid': 'place', 'name': 'Saved city'}], 'preferences': {'units': 'metric'}}))
payload = {'version': 1, 'place': {'uid': 'place'}, 'fetched_at': datetime.now(timezone.utc).isoformat(),
           'current': {'temperature_c': 20.0, 'symbol': 'clearsky_day', 'high_c': 22, 'low_c': 12}}
cache.write_text(json.dumps(payload))
data = snapshot()
assert data['weather']['title'] == 'Saved city' and data['weather']['value'] == '20°'
assert data['weather']['desktop_id'] == 'org.projectluma.Weather.desktop'
assert data['calendar']['desktop_id'] == 'org.projectluma.Calendar.desktop'
assert all(data[id]['columns'] == data[id]['rows'] == 2 for id in ('weather','calendar'))
places.write_text(places.read_text().replace('metric', 'imperial'))
assert snapshot()['weather']['value'] == '68°'
payload['fetched_at'] = (datetime.now(timezone.utc)-timedelta(hours=7)).isoformat()
cache.write_text(json.dumps(payload))
assert snapshot()['weather']['value'] == '—', 'expired forecast must not look current'
payload['fetched_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
cache.write_text(json.dumps(payload))
assert snapshot()['weather']['value'] == '—', 'future timestamp accepted'
payload['fetched_at'] = datetime.now(timezone.utc).isoformat()
payload['current']['temperature_c'] = 200
cache.write_text(json.dumps(payload))
assert snapshot()['weather']['value'] == '—', 'invalid provider value accepted'
cache.write_text('x'*(512*1024+1))
assert snapshot()['weather']['value'] == '—', 'unbounded cache read'
cache.unlink(); cache.symlink_to(places)
assert snapshot()['weather']['value'] == '—', 'followed cache symlink'
print('PASS shared native widget definitions, saved cache/units, expiry, numeric bounds, size cap and no symlink reads')

# The installed launcher's sandbox owns Weather state on Flatpak desktops.
# Exercise a fresh process so GDesktopAppInfo sees the new exported launcher.
import subprocess,sys
flat_home = Path(root.name)/'flatpak-home'
export = flat_home/'data/applications/org.projectluma.Weather.desktop'
export.parent.mkdir(parents=True)
export.write_text('[Desktop Entry]\nType=Application\nName=Weather\nExec=true\nX-Flatpak=org.projectluma.Weather\n')
flat_places=flat_home/'.var/app/org.projectluma.Weather/data/prairie/weather/places.json'
flat_cache=flat_home/'.var/app/org.projectluma.Weather/cache/prairie-weather/place.json'
flat_places.parent.mkdir(parents=True);flat_cache.parent.mkdir(parents=True)
flat_places.write_text(json.dumps({'places':[{'uid':'place','name':'Sandbox city'}],'preferences':{'units':'metric'}}))
flat_cache.write_text(json.dumps({**payload, 'current': {**payload['current'], 'temperature_c': 20}}))
environment={**os.environ,'HOME':str(flat_home),'XDG_DATA_HOME':str(flat_home/'data'),'XDG_CACHE_HOME':str(flat_home/'cache')}
subprocess.run([sys.executable,'-c', '''
import gi,json
from gi.repository import GLib
gi.require_version('LumaWidgets','1.0')
from gi.repository import LumaWidgets
model=LumaWidgets.Model.new('org.gnome.Shell.CalendarServer')
weather=json.loads(model.dup_snapshot())['weather']
assert weather['title']=='Sandbox city',weather
assert weather['value']=='20°',weather
model.run_dispose()
'''],env=environment,check=True)
print('PASS installed Flatpak Weather launcher selects its real sandbox state')
