import sys,time
from pathlib import Path
package_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(package_root))
from lumaui_runtime import window_id, xdo
callback_errors = []
original_excepthook = sys.excepthook
def callback_failed(kind, value, traceback):
 callback_errors.append(value)
 original_excepthook(kind, value, traceback)
sys.excepthook = callback_failed
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk,GLib,Adw,Gio
from luma_appkit import install_appkit,install_lumaui,add_style_sheet
from luma_tide.fixture import FixtureLibrary
from luma_tide.ui import TideWindow

def settle():
 end=time.monotonic()+.65
 while time.monotonic()<end:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  time.sleep(.005)
def named(w,name):
 if w.get_name()==name:return w
 c=w.get_first_child()
 while c:
  found=named(c,name)
  if found:return found
  c=c.get_next_sibling()
app=Adw.Application(application_id='org.projectluma.TidePolishTest',flags=Gio.ApplicationFlags.NON_UNIQUE);assert app.register(None)
install_appkit();install_lumaui();add_style_sheet(str(package_root / 'data/tide.css'))
w=TideWindow(app,FixtureLibrary(package_root / 'fixtures/tide-v70.json'));w.set_default_size(390,874);w.present();settle()
for width in (1180, 1024, 720, 560):
 w.set_size_request(width,874);w.set_default_size(width,874);xdo('windowsize','--sync',window_id(w),str(width+10),'884');settle();assert w.get_width()==width,(width,w.get_width())
 if w.pane.shown:w.pane.close();settle()
 assert w.deck.scrub.get_visible()==(width>720)
 assert w.deck.previous.get_visible()==(width>720)
 assert w.deck.love.get_visible()==(width>720)
 assert w.deck.end.get_visible()==(width>900)
 assert w.deck.transport._volume_range.get_visible()==(width>1100)
 for show in (False,True):
  if show:w.open_sources();settle()
  if show:
   for control in [*w.modes.buttons.values(),w.corner.controls['actions.0'],w.corner.controls['actions.1']]:
    ok,bounds=control.compute_bounds(w.layer)
    assert ok and bounds.get_x()>=0 and bounds.get_x()+bounds.get_width()<=w.layer.get_width(),(width,control.get_name())
   w.open_search();settle()
   for control in [*w.modes.buttons.values(),w.search,w.corner.controls['actions.1']]:
    ok,bounds=control.compute_bounds(w.layer)
    assert ok and bounds.get_x()>=0 and bounds.get_x()+bounds.get_width()<=w.layer.get_width(),('search',width,control.get_name(),bounds.get_x(),bounds.get_width())
   w.close_search();settle()
  expected=min(880,max(250,w.layer.get_width()-96))+36
  assert w.deck.get_allocated_width()==expected,(width,show,w.deck.get_allocated_width(),expected)
  for key in (w.deck.play,w.deck.next,w.deck.previous,w.deck.love,w.deck.queue):
   if key.get_mapped():
    ok,bounds=key.compute_bounds(w.deck)
    assert ok and bounds.get_x()>=0 and bounds.get_x()+bounds.get_width()<=w.deck.get_width()+1,(width,key.get_name(),bounds.get_x(),bounds.get_width(),w.deck.get_width())
 if width==1180:
  assert w.deck.repeat.get_mapped() and w.deck.shuffle.get_mapped()
  w._add_source();settle()
  assert w.server_address.get_mapped() and w.server_password.get_mapped() and w.connect_button.get_mapped()
  assert w.get_focus() is w.server_address.entry or w.get_focus().is_ancestor(w.server_address.entry)
  w.pane.close();settle()
  original=w.view
  w.open_search();settle();assert w.modes.get_visible() and w.search.get_mapped() and w.view==original
  w.search_item.entry.set_text('love');settle()
  assert w.query=='love' and w.view=='songs' and len(w._rows)==2,(w.query,w.view,len(w._rows))
  w.close_search();settle()
  w.open_now_playing();settle();assert not w.deck.sleeve.get_visible()
  assert w.deck.inner.get_margin_start()==28
  w.close_now_playing();settle()
 if w.pane.shown:w.pane.close();settle()
 w.open_now_playing();settle()
 assert named(w, 'td-now-side').get_visible()==(width>720)
 w.close_now_playing();settle()
 print('PASS desktop deck and source layout',width,flush=True)
assert not callback_errors, callback_errors
w.close();print('PASS Tide desktop v71 runtime',flush=True)
