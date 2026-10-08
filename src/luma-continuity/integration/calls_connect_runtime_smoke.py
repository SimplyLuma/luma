"""Actual shared GTK Phone over paired TLS; synthetic calls and feedback only."""
import json,os,socket,tempfile,threading,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gio,GLib,Gtk
from prairie_apps.phone import PhoneApplication,PhoneWindow
from prairie_apps.phone_backend import NativeCall,CallPhase
from luma_continuity.bootstrap import create_identity,approve_peer
from luma_continuity.local import PairedExchange,bounded_stream
from luma_continuity.policy import Journal
from luma_continuity.native import NativeCalls
from luma_continuity.call_receiver import CallReceiver
from luma_continuity.call_provider import CallProvider
from luma_continuity import transport

assert 'luma-native-check-' in os.environ['XDG_DATA_HOME']
context=GLib.MainContext.default()
def pump(seconds=.1):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        while context.pending():context.iteration(False)
        time.sleep(.005)
def wait(predicate):
    end=time.monotonic()+15
    while not predicate():
        if time.monotonic()>end:raise AssertionError('paired Phone state timed out')
        pump(.02)

# A separate native D-Bus connection lets the existing synchronous feedbackd
# client execute normally while the private test server runs its GLib context.
feedback=[];feedback_ready=threading.Event();feedback_loop=GLib.MainLoop.new(GLib.MainContext.new(),False)
def feedback_server():
    ctx=feedback_loop.get_context();ctx.push_thread_default()
    bus=Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
    bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',GLib.Variant('(su)',('org.sigxcpu.Feedback',0)),None,Gio.DBusCallFlags.NONE,1500,None)
    info=Gio.DBusNodeInfo.new_for_xml('<node><interface name="org.sigxcpu.Feedback"><method name="TriggerFeedback"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/></method><method name="EndFeedback"><arg type="u" direction="in"/></method></interface></node>')
    def call(_c,_s,_p,_i,method,_params,invocation):
        feedback.append(method)
        invocation.return_value(GLib.Variant('(u)',(len(feedback),)) if method=='TriggerFeedback' else GLib.Variant('()',()))
    registration=bus.register_object('/org/sigxcpu/Feedback',info.interfaces[0],call,None,None)
    feedback_ready.set();feedback_loop.run();bus.unregister_object(registration);bus.close_sync(None);ctx.pop_thread_default()
ft=threading.Thread(target=feedback_server);ft.start();assert feedback_ready.wait(5)
out=Path(os.environ['LUMA_CALLS_UI_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
app=PhoneApplication();assert app.register(None);results=[]
try:
 for width in (360,1024):
  with tempfile.TemporaryDirectory(prefix='paired-call-test-') as temporary:
    root=Path(temporary);client=root/'client';server=root/'server'
    cp=create_identity(client);sp=create_identity(server);epoch='e'*32
    approve_peer(client,server/'device.pem',sp,epoch,['calls.read','calls.control'])
    approve_peer(server,client/'device.pem',cp,epoch,['calls.read','calls.control'])
    calls=[];actions=[];notifications=[];failures=[];stop=threading.Event()
    class SyntheticPhone:
        def calls(self):return tuple(calls)
        def dial(self,address):
            actions.append('dial');calls[:]=[NativeCall('synthetic-outgoing',address,'outgoing',CallPhase.DIALLING,started_at=100)]
            for callback in notifications:callback()
        def accept(self,uid):
            actions.append('answer');old=calls[0];calls[:]=[NativeCall(old.call_id,old.address,'incoming',CallPhase.ACTIVE,started_at=old.started_at,answered_at=110)]
            for callback in notifications:callback()
        def decline(self,uid):
            actions.append('decline');calls.clear()
        def hangup(self,uid):
            actions.append('hangup');calls.clear()
            for callback in notifications:callback()
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(8);listener.settimeout(.2)
    port=listener.getsockname()[1]
    def serve():
        journal=Journal(server/'continuity.db')
        adapter=NativeCalls(SyntheticPhone(),authorized=lambda:True,control_authorized=lambda:True,now=time.time)
        receiver=CallReceiver(journal,adapter,authorized=lambda:True)
        tls=transport.context(server/'device.pem',server/'device.key',client/'device.pem',server=True)
        try:
            while not stop.is_set():
                try:raw,_=listener.accept()
                except socket.timeout:continue
                except OSError:break
                try:
                    with raw,bounded_stream(raw,tls,server=True,timeout=5) as stream:
                        peer=transport.authenticate(stream,cp)
                        result=receiver.handle(peer,transport.receive(stream));transport.send(stream,result)
                except Exception as error:failures.append(type(error).__name__)
            assert journal.db.execute('select count(*) from requests').fetchone()[0]==0
        finally:journal.close()
    worker=threading.Thread(target=serve);worker.start()
    def subscribe(callback):notifications.append(callback);return lambda:notifications.remove(callback)
    provider=CallProvider(PairedExchange(client,sp,epoch,'127.0.0.1',port),account=None,epoch=epoch,
        authorized=lambda:True,control_authorized=lambda:True,subscribe=subscribe,dispatch=GLib.idle_add)
    app.call_provider=provider
    os.environ['LUMA_PRESENTATION_MODE']='fullscreen-mobile' if width==360 else 'windowed'
    window=PhoneWindow(app);window.set_default_size(width,760);window.present()
    wait(lambda:window.capability.available)
    window.set_dial_address('+12025550123');window._place_call()
    wait(lambda:window.session.phase is CallPhase.DIALLING)
    assert actions==['dial'];assert not window.mute_button.get_sensitive()
    window._end_call(window.end_button);wait(lambda:not calls and not window._paired_control_pending)
    calls[:]=[NativeCall('synthetic-incoming','+12025550123','incoming',CallPhase.INCOMING,started_at=200)]
    for callback in notifications:callback()
    wait(lambda:window.session.phase is CallPhase.INCOMING)
    paint=Gtk.WidgetPaintable.new(window);snap=Gtk.Snapshot();paint.snapshot(snap,window.get_width(),window.get_height())
    assert window.get_renderer().render_texture(snap.to_node(),None).save_to_png(str(out/f'incoming-{width}.png'))
    assert feedback[-1]=='TriggerFeedback'
    window.accept_button.emit('clicked');wait(lambda:window.session.phase is CallPhase.ACTIVE and not window._paired_control_pending)
    assert actions[-1]=='answer';assert feedback[-1]=='EndFeedback'
    window._end_call(window.end_button);wait(lambda:not calls and not window._paired_control_pending)
    calls[:]=[NativeCall('synthetic-decline','+12025550123','incoming',CallPhase.INCOMING,started_at=300)]
    for callback in notifications:callback()
    wait(lambda:window.session.phase is CallPhase.INCOMING)
    window.decline_button.emit('clicked');wait(lambda:not calls and not window._paired_control_pending and feedback[-1]=='EndFeedback')
    assert actions==['dial','hangup','answer','hangup','decline']
    window.close();pump(.2);stop.set();listener.close();worker.join(6)
    assert not worker.is_alive();assert failures==[]
    results.append({'width':width,'paired_TLS_controls':actions,'incoming_feedback_start_stop':True,'durable_call_requests':0,'carrier_calls':0,'remote_audio':False})
finally:
 feedback_loop.quit();ft.join(5)
(out/'result.json').write_text(json.dumps({'passed':True,'synthetic_calls_only':True,'cases':results},indent=2))
print('PASS shared Phone paired TLS dial/incoming/answer/decline/hangup; synthetic feedback; no carrier/audio')
