# SPDX-License-Identifier: Apache-2.0
"""Private fixture-only four-width interaction and optional screenshot regression."""
import os,sys,time
sys.path[:0]=['/w/src/luma-platform/appkit','/w/src/luma-monitor']
os.environ['LUMA_MONITOR_FIXTURE']='/w/tests/fixtures/monitor-v70.json'
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk,GLib,Graphene
from luma_monitor.application import MonitorApplication,MonitorWindow,descendants
from luma_appkit import ModeSwitch

def settle():
 end=time.monotonic()+.4
 while time.monotonic()<end:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  time.sleep(.005)
def capture(name):
 settle();s=Gtk.Snapshot();Gtk.WidgetPaintable.new(w).snapshot(s,w.get_width(),w.get_height());node=s.to_node();assert node
 if os.environ.get('LUMA_REVIEW_SHOTS'):
  w.get_renderer().render_texture(node,Graphene.Rect().init(0,0,w.get_width(),w.get_height())).save_to_png(os.path.join(os.environ['LUMA_REVIEW_SHOTS'],name+'.png'))
 print('CAPTURE',name,flush=True)
def named(name):return next(x for x in descendants(w) if x.get_name()==name)
def fit(widget,name):
 ok,b=widget.compute_bounds(w);assert ok and b.get_x()>=-1 and b.get_x()+b.get_width()<=w.get_width()+1,(name,b.get_x(),b.get_width(),w.get_width())
app=MonitorApplication();assert app.register(None)
w=MonitorWindow(app);w.set_default_size(402,874);w.present();settle();assert w.fixture
for width in [402,360,720,1180]:
 w.set_default_size(width,874);settle();assert w.get_width()==width,(width,w.get_width())
 for resource in ['cpu','mem','disk','net','en']:
  w._resource(resource);capture(f'{width}-{resource}');fit(w.page,'page');fit(w.center,'bar')
 for key in ['saver','perf','bal']:
  mode=next(x for x in descendants(w) if isinstance(x,ModeSwitch) and key in x.buttons)
  mode.buttons[key].activate();settle();assert w.profile==key
 w._resource('cpu');settle();w.search.entry.set_text('rustc');capture(f'{width}-search');assert w.query=='rustc'
 w.search.entry.set_text('not-an-app');capture(f'{width}-empty');w.search.entry.set_text('');settle()
 named('mn-app-viola').emit('clicked');settle();assert w.selected=='a:viola'
 named('mn-details').emit('clicked');capture(f'{width}-details');fit(w.center,'details panel')
 if width<560:
  values=[x for x in descendants(w) if x.has_css_class('lumaui-t-monitor-detail-mono')]
  assert len(values)==2 and all(x.get_justify()==Gtk.Justification.RIGHT for x in values)
  assert all(x.get_layout().get_pixel_size()[1]<=42 for x in values)
  w.center.fold();settle()
 w._ask_force();capture(f'{width}-force')
 cancel=next(x for x in descendants(w) if isinstance(x,Gtk.Button) and x.get_label()=='Cancel' and x.get_mapped())
 cancel.emit('clicked');settle();assert w.selected=='a:viola'
 w._deselect();settle()
w.close();print('MONITOR_FOUR_WIDTH_VIEWS_AND_CANCEL_PASS',flush=True)
