"""Actual shared GTK Phone over paired TLS; synthetic calls and feedback only."""
import json,os,socket,tempfile,threading,time
from pathlib import Path
assert "luma-native-check-" in os.environ["XDG_DATA_HOME"]
os.environ["HOME"]=str(Path(os.environ["XDG_DATA_HOME"]).parent/"call-home")
Path.home().mkdir(mode=0o700)
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
from luma_continuity.call_events import CallListener
from luma_continuity.daemon import Daemon,BUS,PATH as BUS_PATH
from luma_continuity.account import AccountModel

root=Path(tempfile.mkdtemp(prefix='call-session-'))
client=Path.home()/'.local/share/luma-connect/device';client.parent.mkdir(parents=True,exist_ok=True)
server=root/'phone'
cp=create_identity(client);sp=create_identity(server);epoch='e'*32;account='synthetic-account'
approve_peer(client,server/'device.pem',sp,epoch,['calls.read','calls.control'],account=account)
approve_peer(server,client/'device.pem',cp,epoch,['calls.read','calls.control'],account=account)
calls=[];actions=[]
class SyntheticPhone:
    def calls(self):return tuple(calls)
    def dial(self,address):
        actions.append('dial');calls[:]=[NativeCall('synthetic-outgoing',address,'outgoing',CallPhase.DIALLING,started_at=100)]
        listener.changed()
    def accept(self,uid):
        assert calls[0].call_id==uid and calls[0].phase is CallPhase.INCOMING
        actions.append('answer');old=calls[0]
        calls[:]=[NativeCall(old.call_id,old.address,'incoming',CallPhase.ACTIVE,started_at=old.started_at,answered_at=110)]
        listener.changed()
    def decline(self,uid):
        assert calls[0].call_id==uid and calls[0].phase is CallPhase.INCOMING
        actions.append('decline');calls.clear();listener.changed()
    def hangup(self,uid):
        actions.append('hangup');calls.clear();listener.changed()
with socket.socket() as reservation:
    reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
listener=CallListener(server,cp,address='127.0.0.1',port=port,authorized=lambda:True,
    adapter_factory=lambda *_:(NativeCalls(SyntheticPhone(),authorized=lambda:True,control_authorized=lambda:True,now=time.time),lambda:None))
listener.start()
daemon_loop=GLib.MainLoop.new(GLib.MainContext.new(),False);daemon_ready=threading.Event();holders=[]
def daemon_server():
    ctx=daemon_loop.get_context();ctx.push_thread_default()
    bus=Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
    bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',GLib.Variant('(su)',(BUS,0)),None,Gio.DBusCallFlags.NONE,1500,None)
    model=AccountModel()
    def refresh():
        model.state.update(account_status='signed_in',service_status='ready',stale=False,
            identity={'account_id':account},valid_until=time.time()+15,lease_deadline_monotonic=time.monotonic()+15)
    refresh();model.refresh=refresh
    daemon=Daemon(bus,model,observe_environment=False)
    # Only suppress launching an external installed Phone process in this
    # private fixture; the actual shared app is activated below.
    daemon.calls.on_incoming=lambda:None
    holders.append(daemon);daemon_ready.set();daemon_loop.run()
    daemon.close();bus.close_sync(None);ctx.pop_thread_default()
dt=threading.Thread(target=daemon_server);dt.start();assert daemon_ready.wait(5)
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
def invoke(method,signature='()',values=()):
    return bus.call_sync(BUS,BUS_PATH,BUS,method,GLib.Variant(signature,values),None,Gio.DBusCallFlags.NONE,7000,None)
def state():return json.loads(invoke('GetCallState').unpack()[0])
try:
    # Actual daemon selection API, then ordinary Phone startup. No provider
    # injection and no in-process wake callback reaches the Phone provider.
    wait(lambda:not holders[0].busy)
    invoke('SelectCallPhone','(sssu)',(sp,'Synthetic phone','127.0.0.1',port))
    wait(lambda:state()['connected'])
    requested_width=[360]
    initialize=PhoneWindow.__init__
    def sized_window(window,application):
        initialize(window,application)
        window.set_default_size(requested_width[0],760)
    PhoneWindow.__init__=sized_window  # geometry only, before normal activate maps it
    app=PhoneApplication();assert app.register(None);results=[]
    for width in (360,500,1024,1440):
        os.environ['LUMA_PRESENTATION_MODE']='fullscreen-mobile' if width==360 else 'windowed'
        requested_width[0]=width
        app.activate();window=app.props.active_window
        window.present()
        wait(lambda:window.capability.available)
        assert window.continuity is app.call_provider
        assert abs(window.get_width()-width)<25,(width,window.get_width())
        window.set_dial_address('+12025550123');window._place_call()
        wait(lambda:window.session.phase is CallPhase.DIALLING)
        window._end_call(window.end_button)
        wait(lambda:not calls and not window._paired_control_pending)
        calls[:]=[NativeCall('incoming-'+str(width),'+12025550123','incoming',CallPhase.INCOMING,started_at=200)]
        listener.changed();wait(lambda:window.session.phase is CallPhase.INCOMING)
        wait(lambda:feedback and feedback[-1]=='TriggerFeedback')
        pump(.2);paint=Gtk.WidgetPaintable.new(window);snap=Gtk.Snapshot();paint.snapshot(snap,window.get_width(),window.get_height())
        node=snap.to_node();assert node
        assert window.get_renderer().render_texture(node,None).save_to_png(str(out/('session-incoming-'+str(width)+'.png')))
        window.accept_button.emit('clicked');wait(lambda:window.session.phase is CallPhase.ACTIVE and not window._paired_control_pending)
        assert feedback[-1]=='EndFeedback'
        window._end_call(window.end_button);wait(lambda:not calls and not window._paired_control_pending)
        calls[:]=[NativeCall('decline-'+str(width),'+12025550123','incoming',CallPhase.INCOMING,started_at=300)]
        listener.changed();wait(lambda:window.session.phase is CallPhase.INCOMING)
        window.decline_button.emit('clicked');wait(lambda:not calls and not window._paired_control_pending)
        results.append({'width':width,'normal_startup':True,'encrypted_events':True})
        window.close();pump()
    assert actions==['dial','hangup','answer','hangup','decline']*4,actions
    listener.owner_changed();wait(lambda:not state()['connected'])
    journal=Journal(server/'continuity.db')
    try:assert journal.db.execute('SELECT count(*) FROM requests').fetchone()[0]==0
    finally:journal.close()
    (out/'session-result.json').write_text(json.dumps({'passed':True,'results':results,'synthetic_native_calls':True,'carrier_or_audio_tested':False},indent=2))
finally:
    listener.stop();daemon_loop.quit();dt.join(10);feedback_loop.quit();ft.join(5)
print('PASS normal Phone startup / daemon selection / paired TLS events / call controls; synthetic native service only')
