"""Real GTK/private D-Bus controls; synthetic account/peer, no modem/listener."""
import json,os,tempfile,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gio,GLib,Gtk
from luma_continuity.application import ConnectApplication,ConnectWindow
from luma_continuity.account import AccountModel
from luma_continuity.daemon import Daemon,BUS
from luma_continuity.message_devices import MessageDevices
from luma_continuity.bootstrap import create_identity
from luma_continuity.policy import Journal
out=Path(os.environ['LUMA_CONNECT_SMOKE_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
context=GLib.MainContext.default()
def pump(seconds=.1):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  while context.pending():context.iteration(False)
  time.sleep(.005)
def wait(predicate):
 end=time.monotonic()+5
 while not predicate():
  if time.monotonic()>end:raise AssertionError('GTK result timed out')
  pump(.03)
def walk(widget):
 yield widget
 child=widget.get_first_child()
 while child:
  yield from walk(child);child=child.get_next_sibling()
def click(window,label):
 buttons=[w for w in walk(window) if isinstance(w,Gtk.Button) and w.get_label()==label]
 assert len(buttons)==1,(label,len(buttons))
 buttons[0].emit('clicked');pump()
def capture(window,name):
 pump(.2);paint=Gtk.WidgetPaintable.new(window);snap=Gtk.Snapshot();paint.snapshot(snap,window.get_width(),window.get_height())
 node=snap.to_node();assert node
 assert window.get_renderer().render_texture(node,None).save_to_png(str(out/(name+'.png')))
app=ConnectApplication();assert app.register(None)
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
model=AccountModel();model.state.update(account_status='signed_in',service_status='ready',stale=False,
 identity={'account_id':'synthetic-account','session_id':'fixture'},profile={'name':'Synthetic account'},sessions={'items':[]})
daemon=Daemon(bus,model);owner=Gio.bus_own_name_on_connection(bus,BUS,Gio.BusNameOwnerFlags.NONE,None,None)
pump(.3)
with tempfile.TemporaryDirectory(prefix='connect-consent-ui-') as temporary:
 root=Path(temporary);directory=root/'identity';create_identity(directory);peer='a'*64
 events=[]
 class Receiver:
  running=True
  def start(self):events.append('start')
  def stop(self):self.running=False;events.append('stop')
 for width in (880,360):
  journal=Journal(directory/'continuity.db');journal.approve(peer,('b' if width==880 else 'c')*32,['messages.read'],account='synthetic-account',outgoing_grants=['messages.read']);journal.close()
  daemon.devices=MessageDevices(directory,root/'config/selected.json',account=lambda:'synthetic-account',receiver_factory=lambda *_:Receiver())
  daemon._schedule(lambda:None);wait(lambda:not daemon.busy)
  os.environ['LUMA_PRESENTATION_MODE']='fullscreen-mobile' if width==360 else 'windowed'
  window=ConnectWindow(app);window.set_default_size(width,760);window.present();wait(lambda:window.state is not None)
  if width==880:window.sidebar.list.select_row(window.rows['phones']);pump()
  click(window,'Create phone invitation');wait(lambda:bool(window.state.get('pairing')))
  assert window.state['pairing']['kind']=='offer'
  capture(window,'pairing-'+str(width))
  # The pairing controls and device actions fit the window at every width.
  assert window.get_content().measure(Gtk.Orientation.HORIZONTAL,-1)[0]<=width,window.get_content().measure(Gtk.Orientation.HORIZONTAL,-1)
  click(window,'Use for Messages');dialog=window.get_visible_dialog();assert dialog
  entries=[w for w in walk(dialog) if isinstance(w,Gtk.Entry)]
  next(w for w in entries if w.get_placeholder_text()=='Local IP address').set_text('127.0.0.1')
  capture(window,'select-'+str(width));click(dialog,'Use phone')
  wait(lambda:daemon.devices.selection_path.exists())
  document=json.loads(daemon.devices.selection_path.read_text());assert document['phone']['peer']==peer
  wait(lambda:not daemon.busy);pump(.2)
  click(window,'Enable sending through phone');dialog=window.get_visible_dialog();click(dialog,'Allow sending')
  wait(lambda:daemon.devices.devices()[0]['can_send']);wait(lambda:not daemon.busy);pump(.2)
  click(window,'Allow sending from this device');dialog=window.get_visible_dialog();capture(window,'send-consent-'+str(width));click(dialog,'Allow sending')
  wait(lambda:daemon.devices.devices()[0]['can_receive_sends']);wait(lambda:not daemon.busy);pump(.2)
  click(window,'Disable sending');wait(lambda:not daemon.devices.devices()[0]['can_send']);wait(lambda:not daemon.busy);pump(.2)
  click(window,'Share conversations');dialog=window.get_visible_dialog()
  next(w for w in walk(dialog) if isinstance(w,Gtk.Entry) and w.get_placeholder_text()=='Local IP address').set_text('127.0.0.1')
  click(dialog,'Start sharing');wait(lambda:daemon.devices.shared_peer==peer);wait(lambda:not daemon.busy);pump(.2)
  click(window,'Stop sharing');wait(lambda:daemon.devices.shared_peer is None)
  click(window,'Revoke');dialog=window.get_visible_dialog();capture(window,'revoke-'+str(width));click(dialog,'Revoke')
  wait(lambda:not daemon.devices.devices());window.destroy();pump()
 daemon.close();Gio.bus_unown_name(owner)
assert events==['start','stop','start','stop'],events
(out/'result.json').write_text(json.dumps({'passed':True,'widths':[880,360],'real_gtk_dbus':True,'synthetic_account_and_receiver':True,'actions':['create invitation','select','enable sending','allow incoming sends','disable sending','share','stop','revoke']},indent=2))
print('PASS native GTK message consent controls; synthetic account/receiver only')
