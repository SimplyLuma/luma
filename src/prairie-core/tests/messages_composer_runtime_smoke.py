"""Real shared Messages GTK window; synthetic account, history and transport."""
import json,os,tempfile,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk,GLib,Gdk,Gio
import ctypes,ctypes.util
x11=ctypes.CDLL(ctypes.util.find_library("X11"));xtst=ctypes.CDLL(ctypes.util.find_library("Xtst"))
x11.XOpenDisplay.restype=ctypes.c_void_p
x11.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong];x11.XKeysymToKeycode.restype=ctypes.c_uint
x11.XSetInputFocus.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
x11.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]
x11.XCloseDisplay.argtypes=[ctypes.c_void_p]
xtst.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
assert "luma-native-check-" in os.environ["XDG_DATA_HOME"]
xdpy=x11.XOpenDisplay(None);assert xdpy
def key(sym,down):
 code=x11.XKeysymToKeycode(xdpy,sym);assert code
 assert xtst.XTestFakeKeyEvent(xdpy,code,int(down),0);x11.XSync(xdpy,False);pump(.05)
def enter(modifier=None):
 if modifier:key(modifier,True)
 key(Gdk.KEY_Return,True);key(Gdk.KEY_Return,False)
 if modifier:key(modifier,False)
from prairie_apps.messages import MessagesApplication,MessagesWindow
from prairie_apps.messages_backend import MessageStore
from luma_continuity.bootstrap import create_identity
from luma_continuity.message_provider import MessageProvider,SelectedPhone
from luma_continuity.policy import Journal
context=GLib.MainContext.default()
def pump(seconds=.1):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  while context.pending():context.iteration(False)
  time.sleep(.005)
def wait(predicate):
 end=time.monotonic()+8
 while not predicate():
  if time.monotonic()>end:raise AssertionError('Messages GTK state timed out')
  pump(.03)
out=Path(os.environ['LUMA_MESSAGES_CONNECT_UI_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
app=MessagesApplication();assert app.register(None)
results=[]
# Private test-session broker: exercise actual GTK action and D-Bus dispatch.
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
assert bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',GLib.Variant('(su)',('org.projectluma.Connect1',0)),None,Gio.DBusCallFlags.NONE,1500,None).unpack()[0]==1
unlock_requests=[]
info=Gio.DBusNodeInfo.new_for_xml('<node><interface name="org.projectluma.Connect1"><method name="UnlockAccount"/></interface></node>')
def unlock_call(_c,_s,_p,_i,method,_parameters,invocation):
 assert method=='UnlockAccount'
 unlock_requests.append(method)
 invocation.return_value(GLib.Variant('()',()))
registration=bus.register_object('/org/projectluma/Connect',info.interfaces[0],unlock_call,None,None)

for width in (920,360):
 with tempfile.TemporaryDirectory(prefix='messages-connect-ui-') as temporary:
  root=Path(temporary);directory=root/'identity';create_identity(directory)
  peer,epoch,account='a'*64,'b'*32,'synthetic-account'
  journal=Journal(directory/'continuity.db');journal.approve(peer,epoch,[],account=account,outgoing_grants=['messages.read','messages.send']);journal.close()
  records=[dict(uid='synthetic-incoming',address='+12025550123',body='Synthetic incoming conversation',timestamp=123,direction='incoming',state='received',attachments=[])]
  sends=[]
  def exchange(request):
   assert request['account']==account
   if request['capability']=='messages.send':
    sends.append(request['id'])
    row=dict(uid='synthetic-out-'+request['id'],address=request['payload']['address'],body=request['payload']['body'],timestamp=124,direction='outgoing',state='sent',attachments=[])
    records.append(row);return {'state':'complete','result':{'uid':row['uid'],'state':'sent'}}
   result={'threads':[dict(address='+12025550123',display_name='Synthetic phone contact')],'truncated':False} if 'query' in request['payload'] else {'messages':records}
   return {'state':'complete','result':result}
  provider=MessageProvider(directory,SelectedPhone(peer,epoch,account,'Synthetic phone','127.0.0.1',18444),account_observation=lambda:{'account':account,'signed_in':True,'stale':False},store_factory=MessageStore,dispatch=GLib.idle_add,exchange_factory=lambda *_a,**_k:exchange)
  app.message_provider=provider
  os.environ['LUMA_PRESENTATION_MODE']='fullscreen-mobile' if width==360 else 'windowed'
  window=MessagesWindow(app);window.set_default_size(width,760);window.present()
  wait(lambda:len(window.store.thread('+12025550123'))==1 and provider.status['state']=='ready')
  window.open_address('+12025550123');pump()
  # Real key dispatch in private Xvfb, using only synthetic transport/history.
  gi.require_version('GdkX11','4.0')
  from gi.repository import GdkX11
  x11.XSetInputFocus(xdpy,window.get_surface().get_xid(),2,0);x11.XSync(xdpy,False)
  window.composer_view.grab_focus();pump()
  assert not window.transport_status.get_visible()
  window.composer_buffer.set_text('Synthetic reply from shared composer');enter()
  wait(lambda:len(window.store.thread('+12025550123'))==2 and window.store.thread('+12025550123')[-1].state=='sent')
  assert len(sends)==1
  window.composer_buffer.set_text('Line one');enter(Gdk.KEY_Shift_L)
  assert window._composer_text()=='Line one\n';assert len(sends)==1
  window.composer_view.emit('preedit-changed','synthetic preedit')
  key(Gdk.KEY_Return,True)
  assert len(sends)==1
  window.composer_view.emit('preedit-changed','')
  window.composer_buffer.set_text('Draft retained during held Return')
  key(Gdk.KEY_Return,True);pump(.8)
  assert len(sends)==1
  key(Gdk.KEY_Return,False)
  window.composer_buffer.set_text('');enter();assert len(sends)==1
  window.composer_buffer.set_text('Synthetic Ctrl Enter reply');enter(Gdk.KEY_Control_L)
  wait(lambda:len(sends)==2)
  retries=[];provider.retry=lambda:retries.append('retry')  # the synthetic status must not be re-observed here
  provider.status['state']='offline';window._set_transport_status('Waiting for your phone… Messages stay queued.',True)
  assert window.transport_status.get_visible()
  # A sleeping phone offers Retry, which re-observes without any unlock prompt.
  assert window.unlock_phone_button.get_visible() and window.unlock_phone_button.get_label()=='Retry'
  before=len(unlock_requests);window.lookup_action('unlock-phone').activate(None);pump()
  assert retries==['retry'] and len(unlock_requests)==before
  provider.status['state']='locked';window._set_transport_status('Unlock your login keyring.',False)
  assert window.transport_status.get_visible()
  assert window.unlock_phone_button.get_visible() and window.unlock_phone_button.get_label()=='Unlock'
  before=len(unlock_requests);old_sends=len(sends);retries.clear()
  draft_before=window._composer_text()
  window.lookup_action('unlock-phone').activate(None)
  window.lookup_action('unlock-phone').activate(None)
  wait(lambda:len(unlock_requests)==before+1 and not window._unlock_requested)
  assert len(sends)==old_sends and window._composer_text()==draft_before
  # Acknowledgement is not proof of unlock: the provider re-observes (an already
  # open keyring recovers), and a cancelled prompt can be requested again.
  assert retries==['retry']
  assert provider.status['state']=='locked' and window.unlock_phone_button.get_visible()
  window.lookup_action('unlock-phone').activate(None)
  wait(lambda:len(unlock_requests)==before+2 and not window._unlock_requested)
  provider.status['state']='ready' ;window._set_transport_status('',True);pump()
  assert not window.unlock_phone_button.get_visible()
  assert not window.lookup_action('unlock-phone').get_enabled()
  assert not window.transport_status.get_visible()
  paint=Gtk.WidgetPaintable.new(window);snap=Gtk.Snapshot();paint.snapshot(snap,window.get_width(),window.get_height())
  node=snap.to_node();assert node;assert window.get_renderer().render_texture(node,None).save_to_png(str(out/('messages-'+str(width)+'.png')))
  results.append({'width':width,'synthetic_incoming':1,'synthetic_reply':2,'enter_sends':True,'shift_enter_newline':True,'ctrl_enter_compatible':True,'preedit_confirmation_no_send':True,'repeat_no_send':True,'empty_enter_no_send':True,'ready_row_hidden':True,'actionable_row_visible':True})
  window.quit();pump(.3);assert provider._closed
(out/'result.json').write_text(json.dumps({'passed':True,'real_shared_GTK':True,'synthetic_transport_only':True,'cases':results},indent=2))
x11.XCloseDisplay(xdpy)
print('PASS shared Messages GTK real Enter/Shift Enter/Ctrl Enter; preedit/repeat/empty guards; collapsed status; synthetic transport only')
