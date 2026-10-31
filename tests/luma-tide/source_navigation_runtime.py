# SPDX-License-Identifier: Apache-2.0
"""Real Sources > More > Edit input across phone and desktop panes."""
import argparse
from pathlib import Path
import sys,time
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk,GLib,Adw,Gio
from luma_appkit import install_appkit,install_lumaui,add_style_sheet
from luma_tide.fixture import FixtureLibrary
from luma_tide.ui import TideWindow

parser=argparse.ArgumentParser()
parser.add_argument('--phone-only',action='store_true')
parser.add_argument('--fixture',type=Path)
parser.add_argument('--stylesheet',type=Path)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
fixture=args.fixture or root/'fixtures/tide-v70.json'
stylesheet=args.stylesheet or root/'data/tide.css'
errors=[]
original_excepthook=sys.excepthook
def failed(kind,error,tb):
 errors.append(error);original_excepthook(kind,error,tb)
sys.excepthook=failed

def settle():
 end=time.monotonic()+.65
 while time.monotonic()<end:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  time.sleep(.005)

def walk(widget):
 yield widget
 child=widget.get_first_child()
 while child:
  yield from walk(child);child=child.get_next_sibling()

app=Adw.Application(application_id='org.projectluma.TideSourceNavigationQA',flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit();install_lumaui();add_style_sheet(str(stylesheet))
for width,height in ([(360,400)] if args.phone_only else [(360,400),(1180,740)]):
 w=TideWindow(app,FixtureLibrary(fixture));w.set_default_size(width,height);w.present();settle()
 assert w.root.get_margin_start()==0, 'The phone content must reach its leading edge'
 origin=w.trail.current
 w.now_tab='next'
 assert w.go_back() and w.now_tab is None and w.trail.current==origin, 'Back dismisses Now Playing before changing the library trail'
 w.show_section('home');w.open_sources();settle()
 source=next(s for s in w.library.sources if s.remote)
 w._source_menu(source.id);settle()
 button=next(b for b in walk(w) if isinstance(b,Gtk.Button) and b.get_mapped()
             and any(isinstance(label,Gtk.Label) and label.get_label()=='Edit' for label in walk(b)))
 button.emit('clicked');settle()
 assert not errors,errors
 assert w.pane_kind=='sources' and w.editing and w.source_id==source.id
 assert w.pane.shown and w.connect_button.get_mapped(),'Edit must present its destination after the menu dismisses'
 w.server_password.entry.grab_focus();settle()
 assert w.server_password.entry.get_mapped()
 focus=w.get_focus()
 assert focus is w.server_password.entry or focus is not None and focus.is_ancestor(w.server_password.entry)
 while w.pane_trail.can_go_back:
  assert w.go_back()
 assert w.go_back() and not w.pane.shown, 'Back returns through source details before closing the drawer'
 settle();w.close();settle()
 print('PASS flush content edge, transient Back and Sources > More > Edit with focused password',width,height,flush=True)
