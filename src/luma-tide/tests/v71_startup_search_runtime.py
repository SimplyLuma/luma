import sys,time,os
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
os.environ['LUMA_TIDE_QUERY']='love'
w=TideWindow(app,FixtureLibrary(package_root / 'fixtures/tide-v70.json'))
from luma_appkit import icons
for group in w.commands.visible_groups(menu=True):
 for command in group.commands:
  assert command.icon, command.id
  assert Gtk.IconTheme.get_for_display(w.get_display()).has_icon(icons.resolve(command.icon)), command.icon
w.set_default_size(402,874);w.present();settle()
assert w.phone and w.view=='search' and w.query=='love',(w.phone,w.view,w.query)
assert len(w._rows)==2,len(w._rows)
assert w.phone_bar.center.searching
w.phone_bar.center.close_search();settle()
assert not w.query and not w.view.startswith('search')
assert not callback_errors, callback_errors
w.close();print('PASS startup query crosses desktop-to-phone allocation',flush=True)
