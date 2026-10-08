#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the fixed host action and real unavailable-provider repair window."""
import os,tempfile,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gio,GLib,Gtk,GObject
from luma_displays import application,model,names,settings
from test_model import XML,home_state
from luma_appkit import AppWindow

def until(predicate, seconds=6):
 end=time.monotonic()+seconds;ctx=GLib.MainContext.default()
 while not predicate():
  assert time.monotonic()<end,'Real async operation did not complete'
  while ctx.pending():ctx.iteration(False)
  time.sleep(.005)

def settle(seconds=.15):
 end=time.monotonic()+seconds;ctx=GLib.MainContext.default()
 while time.monotonic()<end:
  while ctx.pending():ctx.iteration(False)
  time.sleep(.005)

with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);host=root/'host';private=root/'private';host.mkdir();private.mkdir()
 (host/'monitors.xml').write_text(XML);(private/'monitors.xml').write_text('<monitors/>')
 os.environ.update(FLATPAK_ID=application.APP_ID,HOST_XDG_CONFIG_HOME=str(host),XDG_CONFIG_HOME=str(private),XDG_DATA_HOME=str(private/'data'))
 assert len(model.read_arrangements())==1,'Read Mutter host arrangement, not private empty config'
 key=home_state().key;names.rename(key,'Private label')
 assert (private/'data/state/luma/displays.json').is_file()
 assert not (host/'luma').exists(),'Private name must not write host configuration'
 assert (host/'monitors.xml').read_text()==XML
 os.environ.pop('FLATPAK_ID');os.environ['XDG_STATE_HOME']=str(private/'state')

# Actual async supported org.gtk.Actions protocol, fixed launch-panel/display.
connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
node=Gio.DBusNodeInfo.new_for_xml('''<node><interface name="org.gtk.Actions"><method name="Activate"><arg type="s" direction="in"/><arg type="av" direction="in"/><arg type="a{sv}" direction="in"/></method></interface></node>''')
received=[];failed=[]
def called(_c,_sender,_path,_iface,method,args,invocation):
 assert method=='Activate';name,values,platform=args.unpack()
 assert name=='launch-panel'and values==[('display',[])]and platform=={},repr(args)
 received.append(args.print_(True));invocation.return_value(GLib.Variant('()',()))
registration=connection.register_object('/org/gnome/Settings',node.interfaces[0],called,None,None)
owned=[];owner=Gio.bus_own_name_on_connection(connection,'org.gnome.Settings',Gio.BusNameOwnerFlags.NONE,lambda *_:owned.append(True),None)
until(lambda:owned);settings.open_display_settings(lambda:failed.append(True));until(lambda:received or failed)
assert received and not failed
Gio.bus_unown_name(owner);connection.unregister_object(registration);settle()
settings.open_display_settings(lambda:failed.append(True));until(lambda:failed)

class Config(GObject.Object):
 __gsignals__={'changed':(GObject.SignalFlags.RUN_LAST,None,())}
 def state(self):return home_state()
 def apply(self,*_args):pass
available=[False]
def factory():
 if not available[0]:raise GLib.Error('The host display service is unavailable')
 return Config()
app=application.DisplaysApplication(config_factory=factory);app.set_flags(app.get_flags()|Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None),'Missing host provider must not prevent application registration'
app.activate();settle();repair=app.window
assert isinstance(repair,AppWindow)and not isinstance(repair,application.DisplaysWindow)
assert repair.get_mapped(),'Real repair window must render instead of throwing at startup'
def walk(widget):
 yield widget;child=widget.get_first_child()
 while child:
  yield from walk(child);child=child.get_next_sibling()
for width in (360,500,1024,1440):
 repair.set_default_size(width,520);settle()
 labels=[x.get_text()for x in walk(repair)if isinstance(x,Gtk.Label)]
 assert 'Display controls are unavailable'in labels
 buttons=[x for x in walk(repair)if isinstance(x,Gtk.Button)and x.get_label()=='Try again']
 assert len(buttons)==1 and buttons[0].get_mapped()
 ok,b=buttons[0].compute_bounds(repair);assert ok and b.get_x()>=0 and b.get_y()>=0 and b.get_x()+b.get_width()<=repair.get_width()+1
available[0]=True;buttons[0].emit('clicked');settle()
assert isinstance(app.window,application.DisplaysWindow)and app.window.get_mapped(),'Retry must create real display controls'
app.window._finish();settle();assert app.window is None
print('PASS: exact host action, failure callback, native repair/retry four widths and isolated host/private configuration')
