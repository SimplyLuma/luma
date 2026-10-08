"""Real shared Messages GTK window; synthetic account, history and transport."""
import json,os,tempfile,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gtk,GLib
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
  window.composer_buffer.set_text('Synthetic reply from shared composer');window._send_message()
  wait(lambda:len(window.store.thread('+12025550123'))==2 and window.store.thread('+12025550123')[-1].state=='sent')
  assert len(sends)==1
  # The window must be mapped and allocated, or the snapshot is empty.
  wait(lambda:window.get_mapped() and window.get_width()>0 and window.get_height()>0);pump(.3)
  paint=Gtk.WidgetPaintable.new(window);snap=Gtk.Snapshot();paint.snapshot(snap,window.get_width(),window.get_height())
  node=snap.to_node();assert node;assert window.get_renderer().render_texture(node,None).save_to_png(str(out/('messages-'+str(width)+'.png')))
  results.append({'width':width,'synthetic_incoming':1,'synthetic_reply':1,'single_send_operation':True})
  window.quit();pump(.3);assert provider._closed
(out/'result.json').write_text(json.dumps({'passed':True,'real_shared_GTK':True,'synthetic_transport_only':True,'cases':results},indent=2))
print('PASS real shared Messages GTK incoming/reply; synthetic transport only')
