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
for width in (360,402,500):
 w.set_size_request(width,874);w.set_default_size(width,874);xdo('windowsize','--sync',window_id(w),str(width+10),'884');settle()
 w.open_album(w.library.albums[0].id);settle()
 hero=named(w,'td-hero');cover=named(w,'td-hero-cover')
 assert hero.get_margin_top()==76 and hero.get_margin_bottom()==8
 if w.phone_device:
  ok,cover_bounds=cover.compute_bounds(w)
  assert ok and abs(cover_bounds.get_y()-120)<=1,cover_bounds.get_y()
  assert w.has_css_class('lumaui-bleed') and w.page.get_margin_top()==w.status_inset
 play=named(w,'td-play-album');shuffle=named(w,'td-shuffle-album')
 assert play.get_height()==shuffle.get_height()==46
 assert play.get_allocated_width()==shuffle.get_allocated_width()
 assert 85 <= play.get_allocated_width() <= 120,(width,play.get_allocated_width())
 for button in (play,shuffle):
  ok,b=button.compute_bounds(w)
  assert ok and b.get_x()>=0 and b.get_x()+b.get_width()<=width
 assert shuffle.has_css_class('fill')
 allocated=play.get_allocated_width()
 play.emit('clicked');settle()
 assert w.source.player.song_id in {s.id for s in w.library.albums[0].songs}
 print('PASS phone album actions',width,allocated)
assert not callback_errors, callback_errors
w.close()
