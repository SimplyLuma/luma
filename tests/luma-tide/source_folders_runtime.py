# SPDX-License-Identifier: Apache-2.0
"""Sources > Add source offers mounted drives and a folder chooser, on a phone and a computer."""
from pathlib import Path
import sys,time
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk,GLib,Adw,Gio
from luma_appkit import install_appkit,install_lumaui,add_style_sheet
from luma_tide.fixture import FixtureLibrary
from luma_tide.folders import FolderChoice
from luma_tide.ui import TideWindow

root=Path(__file__).resolve().parents[1]
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

def named(window,name):
 return next((w for w in walk(window) if w.get_name()==name),None)

app=Adw.Application(application_id='org.projectluma.TideSourceFoldersQA',flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit();install_lumaui();add_style_sheet(str(root/'data/tide.css'))
card=Path('/run/media/listener/SD CARD')
for width,height in ((360,640),(1180,740)):
 library=FixtureLibrary(root/'fixtures/tide-v70.json')
 library.offered_folders=(FolderChoice(card,'SD CARD','Removable drive','hard-drive',True),)
 w=TideWindow(app,library);w.set_default_size(width,height);w.present();settle()
 before=len(w.library.sources)
 w.open_sources();settle()
 w._add_source();settle()
 assert w.source_id=='add' and w.pane.shown
 row=named(w,'td-source-folder-SD-CARD');choose=named(w,'td-source-folder-choose')
 assert row is not None and row.get_mapped(),'A mounted drive is offered in Add source'
 assert choose is not None and choose.get_mapped(),'Any other folder stays one tap away'
 assert w.connect_button.get_mapped(),'The server form follows the folders'
 focus=w.get_focus()
 assert not (focus is w.server_address.entry or focus is not None and focus.is_ancestor(w.server_address.entry)), \
  'Opening Add source must not raise the keyboard over the folder choices'
 row.emit('clicked');settle()
 assert not errors,errors
 assert len(w.library.sources)==before+1 and w.library.sources[-1].name=='SD CARD'
 assert w.library.sources[-1].icon=='hard-drive'
 assert w.source_id is None and w.pane_trail.current.view=='sources','Adding returns to the Sources list'
 w._add_source();settle()
 assert named(w,'td-source-folder-SD-CARD') is None,'A drive already in the library is not offered again'
 w.mounts_changed();settle()
 assert w.source_id=='add' and named(w,'td-source-folder-choose').get_mapped(),'A mount change refreshes the open pane'
 settle();w.close();settle()
 print('PASS Add source offers drives, adds one and returns to Sources',width,height,flush=True)
