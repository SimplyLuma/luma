"""Actual Weather UI control through Ari's AT-SPI adapter, private server only."""
import json,os,subprocess,sys,time,logging
logging.basicConfig(level=logging.WARNING)
from pathlib import Path
from ari.tools import accessibility as a
out=Path(os.environ['DEV_ARTIFACT_DIR'])
if '--weather' in sys.argv:
 import gi
 gi.require_foreign('cairo')
 gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
 from gi.repository import GLib
 from prairie_apps import weather
 weather.fetch_observation=lambda *args,**kwargs:None
 weather.fetch_alerts=lambda *args,**kwargs:()
 from datetime import datetime,timedelta,timezone
 from prairie_apps.weather_model import CityForecast,Current,HourPoint,DayPoint,write_cache,cache_directory
 def sample_forecast(place,**kwargs):
  now=datetime.now(timezone.utc)
  forecast=CityForecast(place,now.isoformat(),Current(22,'clearsky_day',humidity=60,wind_mps=3),
   tuple(HourPoint((now+timedelta(hours=i)).isoformat(),22+i%5,'clearsky_day') for i in range(48)),
   tuple(DayPoint((now+timedelta(days=i)).date().isoformat(),'clearsky_day',27,17) for i in range(10)))
  write_cache(forecast,cache_directory())
  if kwargs.get('on_forecast'):kwargs['on_forecast'](forecast)
  return forecast
 weather.fetch_forecast=sample_forecast
 if os.environ.get('ARI_ACCESSIBILITY_POPULATED') == '1':
  from prairie_apps.weather_backend import PlaceStore,LocationSearch,new_place
  search=LocationSearch();search.load()
  place=PlaceStore().add(new_place(search.search('Boston')[0]))
  sample_forecast(place)
 sys.exit(weather.main())
client = None
if os.environ.get('ARI_ACCESSIBILITY_MCP') == '1':
 from ari.mcp import Client
 from types import SimpleNamespace
 backend = a
 client = Client('accessibility',[sys.executable,str(Path(a.__file__))])
 client.start()
 a = SimpleNamespace(api=backend.api,
  desktop_applications=lambda args:client.call('desktop_applications',args),
  inspect_application=lambda args:client.call('inspect_application',args),
  act_in_application=lambda args:client.call('act_in_application',args))
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio,GLib
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
bus.call_sync('org.a11y.Bus','/org/a11y/bus','org.freedesktop.DBus.Properties','Set',
 GLib.Variant('(ssv)',('org.a11y.Status','IsEnabled',GLib.Variant('b',True))),None,Gio.DBusCallFlags.NONE,5000,None)
with (out/'weather.log').open('w') as log:
 process=subprocess.Popen([sys.executable,__file__,'--weather'],stdout=log,stderr=log)
 try:
  def wait(fn,seconds=15):
   deadline=time.monotonic()+seconds
   while time.monotonic()<deadline:
    value=fn()
    if value:return value
    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
    time.sleep(.15)
   debug=[]
   def dump(node,depth=0):
    if depth>48 or len(debug)>500:return
    try:
     debug.append({'role':node.get_role().value_nick,'name':node.get_name(),'states':[v.value_nick for v in node.get_state_set().get_states()], 'interfaces':node.get_interfaces()})
     for i in range(min(node.get_child_count(),50)):
      child=node.get_child_at_index(i)
      if child:dump(child,depth+1)
    except Exception as error: debug.append({'error':str(error)})
   desktop=a.api().get_desktop(0)
   for i in range(desktop.get_child_count()):dump(desktop.get_child_at_index(i))
   (out/'raw-controls.json').write_text(json.dumps(debug,indent=2))
   raise AssertionError('Timed out: '+str(fn))
  app=wait(lambda:next(iter(a.desktop_applications({'name':'weather'})['data']),None))
  if os.environ.get('ARI_ACCESSIBILITY_AGENT') == '1':
   from native_agent_accessibility import exercise
   exercise(client,out)
   sys.exit(0)
  token=app['application']
  def inspect():
   data=a.inspect_application({'application':token})['data']
   assert len(json.dumps(data,ensure_ascii=False)) < 10000, 'Controls exceed the agent result budget'
   (out/'controls.json').write_text(json.dumps(data,indent=2))
   return data['controls']
  field=wait(lambda:next((row for row in inspect() if row['editable']),None))
  operation={'application':token,'target':field['target'],'operation':'set_text','text':'Chicago'}
  assert a.act_in_application({**operation,'preview':True})['ok']
  assert a.act_in_application(operation)['ok']
  try:
   refused=a.act_in_application(operation)
   assert not refused.get('ok'),'Reused a stale control'
  except ValueError:pass
  def find_result():
   return next((row for row in inspect() if 'chicago' in row['name'].casefold() and row['actions']),None)
  result=wait(find_result)
  print('Result',result,flush=True)
  operation={'application':token,'target':result['target'],'operation':'action','action':result['actions'][0]}
  assert a.act_in_application({**operation,'preview':True})['ok']
  assert a.act_in_application(operation)['ok']
  from prairie_apps.weather_backend import PlaceStore
  places=wait(lambda:PlaceStore().list())
  assert any(p.name.casefold()=='chicago' for p in places),places
  assert any('chicago' in row['name'].casefold() for row in inspect())
  print('Actual Weather accessibility search, stale-target refusal, result activation, persistent city and visible verification PASS',flush=True)
  # Removal must use the app's existing exported command. No Weather-specific
  # dispatch or datastore mutation exists in Ari's adapter.
  snapshot=a.inspect_application({'application':token})['data']
  command=next(row for row in snapshot['commands'] if row['name']=='weather-remove')
  operation={'application':token,'target':command['target'],'operation':'command'}
  assert a.act_in_application({**operation,'preview':True})['ok']
  command_started=time.monotonic()
  result=a.act_in_application(operation)
  command_elapsed=time.monotonic()-command_started
  assert result['ok'] and result['verification_required'] and not result['completed']
  wait(lambda:not any(p.name.casefold()=='chicago' for p in PlaceStore().list()))
  assert any(p.name.casefold()=='boston' for p in PlaceStore().list())
  assert not any(row['name'].casefold()=='chicago' for row in inspect())
  print(f'Generic native command removed exactly Chicago in {command_elapsed:.3f}s; Boston preserved; real store and visible verification PASS',flush=True)
  snapshot=a.inspect_application({'application':token})['data']
  undo=next(row for row in snapshot['commands'] if row['name']=='weather-undo')
  assert a.act_in_application({'application':token,'target':undo['target'],'operation':'command'})['ok']
  wait(lambda:any(p.name.casefold()=='chicago' for p in PlaceStore().list()))
  print('Existing native undo command restored Chicago PASS',flush=True)
 finally:
  if client is not None:client.stop()
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
  log.flush()
  logged=(out/'weather.log').read_text()
  assert not any(error in logged for error in ('Traceback (most recent call last)','TypeError:','AttributeError:')), 'Unexpected Weather callback failure'
