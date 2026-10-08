import os,time,tempfile
from pathlib import Path
os.environ.update(GSETTINGS_BACKEND='memory',LUMA_FORM_FACTOR='phone')
_private=tempfile.TemporaryDirectory(prefix='maps-phone-')
for key in ('XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_STATE_HOME','XDG_CACHE_HOME'):
 os.environ[key]=str(Path(_private.name)/key)
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk,GLib

def settle(seconds=.4):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  time.sleep(.005)
from unittest.mock import patch
# Suppress Shumate import; the live window below uses a mocked map adapter.
os.environ['LUMA_MAPS_FIXTURE']='private-test-no-file'
from luma_maps import application as maps
from luma_maps.providers import Providers,TileProvider,SearchProvider,RoutingProvider

def descendants(widget):
 c=widget.get_first_child()
 while c:
  yield c;yield from descendants(c);c=c.get_next_sibling()
def tap(window,text):
 for b in descendants(window):
  if isinstance(b,Gtk.Button) and b.get_mapped() and (b.get_tooltip_text()==text or any(isinstance(c,Gtk.Label) and c.get_label()==text for c in descendants(b))):b.emit('clicked');settle();return
 raise AssertionError('missing mapped real-provider button '+text)
provider=TileProvider('private','Test offline tiles','raster',True,'Private test',url_template='file:///nonexistent/{z}/{x}/{y}.png')
providers=Providers(user_agent='private',tiles=(provider,),search=SearchProvider('','','',0,0,True),routing=RoutingProvider('none','',(),True,True),default_tile_id='private')
app=maps.MapsApplication();app.set_application_id('org.projectluma.Maps.LiveControls');assert app.register(None)
for width in (360,402):
 with patch.object(maps,'from_environment',return_value=None),patch.object(maps.provider_config,'load',return_value=providers),patch.object(maps.MapsWindow,'_build_map',return_value=Gtk.DrawingArea()),patch.object(maps.MapsWindow,'_apply_tile_provider') as apply,patch('urllib.request.urlopen',side_effect=AssertionError('no network')):
  w=maps.MapsWindow(app);w.set_default_size(width,874);w.present();settle(.8)
  assert w.fixture is None and not hasattr(w,'fixture_map')
  tap(w,'Map style');tap(w,'Test offline tiles');apply.assert_called_once_with(provider)
  from luma_maps.store import Place
  with patch.object(w,'_toast') as toast:
   w._open_place(Place('Private place','Cafe','Test Street',None,None));settle()
   tap(w,'Call');toast.assert_called_once_with('Calling is unavailable for this place')
  print('PASS live phone provider and honest unavailable Call',width,flush=True)
  w.close();settle()
app.quit()
