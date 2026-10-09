"""Private Linux runtime fixture. Synthetic state, isolated bus/home/display only."""
import json,os,signal,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1');gi.require_version('Gdk','4.0')
from gi.repository import Adw,Gio,GLib,Gtk,Gdk
from luma_continuity.application import ConnectApplication,ConnectWindow,OPTIONAL
from luma_continuity.account import AccountModel
from luma_continuity.daemon import Daemon,BUS,PATH
assert os.environ.get('LUMA_CONNECT_PRIVATE_SMOKE')=='1'
out=Path(os.environ['LUMA_CONNECT_SMOKE_OUTPUT'])
context=GLib.MainContext.default()
def pump(seconds=.25):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while context.pending():context.iteration(False)
        time.sleep(.005)
def wait(predicate,seconds=8):
    until=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>until:raise AssertionError('Native state timed out')
        pump(.05)
def descendants(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from descendants(child);child=child.get_next_sibling()
def labels(window):return [w.get_label() for w in descendants(window) if isinstance(w,Gtk.Label)]
def screenshot(window,name):
    pump(.5)
    paint=Gtk.WidgetPaintable.new(window);snap=Gtk.Snapshot();paint.snapshot(snap,window.get_width(),window.get_height())
    node=snap.to_node();assert node
    texture=window.get_renderer().render_texture(node,None)
    assert texture.save_to_png(str(out/(name+'.png')))
app=ConnectApplication();assert app.register(None)
window=ConnectWindow(app);window.present()
wait(lambda:window.state is not None)
assert window.state['account_status']=='signed_out'
assert window.title_bar.get_visible()  # every screen keeps the window's own title bar
assert OPTIONAL in labels(window)
assert not window.state.get('login_available')
screenshot(window,'signed-out-desktop')
# Prove on-demand activation and daemon loss against the real isolated bus.
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
pid=bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetConnectionUnixProcessID',GLib.Variant('(s)',(BUS,)),None,Gio.DBusCallFlags.NONE,5000,None).unpack()[0]
assert pid!=os.getpid();os.kill(pid,signal.SIGTERM)
wait(lambda:window.state.get('service_status')=='unavailable')
assert not window.state.get('login_available')
window.destroy();pump()
# Fixture-only broker owns synthetic server observations; production has no fixture switch.
model=AccountModel();daemon=Daemon(bus,model)
owner=Gio.bus_own_name_on_connection(bus,BUS,Gio.BusNameOwnerFlags.NONE,None,None)
pump(.4)
fixture={'version':1,'generation':2,'account_status':'signed_in','service_status':'ready','stale':False,'busy':False,
    'profile':{'name':'Synthetic Test Account','email':'fixture@example.test'},'identity':{'session_id':'here'},
    'sync':{'sync_service_status':'unavailable','last_sync_at':None,'enabled_count':0,'items':[
        {'id':key,'label':key.replace('_',' ').title(),'enabled':False,'available':False,'visible':key!='clipboard'}
        for key in ['files','photos','messages','calls','contacts','calendar','notes','voice_recordings','passwords','desktop_documents','settings_layout','installed_apps','clipboard']]},
    'usage':{'used_bytes':None,'plan':None,'stale':True},
    'sessions':{'items':[{'session_id':'here','current':True,'can_revoke_here':False},{'session_id':'there','current':False,'can_revoke_here':True,'device_name':'Synthetic Device'}]},'security':{},'login_available':False}
daemon.latest=fixture;daemon._signal()
results=[]
for handheld,width in [(False,880),(False,560),(False,1024),(True,360),(True,500)]:
    os.environ['LUMA_PRESENTATION_MODE']='fullscreen-mobile' if handheld else 'windowed'
    window=ConnectWindow(app);window.set_default_size(width,720 if handheld else 620);window.present()
    wait(lambda:window.state is not None and window.state['account_status']=='signed_in');pump(.5)
    assert window.compact==(handheld or width<640)
    assert 'Clipboard' not in labels(window)
    # Services the account cannot offer yet are not shown as dead switches.
    if window.compact:
        switches=[w for w in descendants(window) if isinstance(w,Adw.SwitchRow)]
        assert len(switches)==0
        # Everything fits the phone: nothing asks for more width than the window has.
        body=window.get_content()
        assert body.measure(Gtk.Orientation.HORIZONTAL,-1)[0]<=width,('page wider than the window',
            [(type(w).__name__,w.measure(Gtk.Orientation.HORIZONTAL,-1)[0]) for w in descendants(body) if w.measure(Gtk.Orientation.HORIZONTAL,-1)[0]>width][:5])
        # A phone shows one page at a time, so Luma Connect's state is said once.
        assert labels(window).count('Luma Connect isn’t connected')<=1
    else:
        window.select_panel('sync');pump()
        assert len([w for w in descendants(window) if isinstance(w,(Adw.SwitchRow,Gtk.Switch))])==0
        for key in ['devices','phones','account']:
            window.select_panel(key);pump()
        window._confirm_signout('others','other devices');pump()
        dialog=window.get_visible_dialog();assert isinstance(dialog,Adw.AlertDialog)
        assert dialog.get_response_appearance('signout')==Adw.ResponseAppearance.DESTRUCTIVE
        dialog.close();pump()
    screenshot(window,('mobile' if handheld else 'desktop')+'-'+str(width))
    results.append({'handheld':handheld,'requested_width':width,'actual_width':window.get_width(),'compact':window.compact})
    window.destroy();pump()
# Luma Connect switches: each shows what turning it off does before it happens,
# and cancelling leaves it as it was. The sync engine is replaced by a fake.
class FakeCloud:
    available=True
    def __init__(self):self.calls=[]
    def status(self,callback):pass
    def set_service(self,service,enabled,callback):self.calls.append((service,enabled));callback(None)
    def sign_out(self,device,callback):self.calls.append(('sign-out',device));callback(None,'')
    def connect(self,code,name,callback):callback(None)
os.environ['LUMA_PRESENTATION_MODE']='windowed'
window=ConnectWindow(app);window.set_default_size(980,700);window.present()
wait(lambda:window.state is not None and window.state['account_status']=='signed_in');pump(.3)
fake=FakeCloud();window.cloud_sync=fake
window.cloud={'signed_in':True,'reachable':True,'hub':'https://hub.example','account':{'name':'fixture'},
    'device':{'id':'d1','name':'Fixture Laptop'},'devices':[{'id':'d1','name':'Fixture Laptop','revoked':False,'services':[]},
    {'id':'d2','name':'Fixture Phone','revoked':False,'services':[]},{'id':'d3','name':'Old','revoked':True,'services':[]}],
    'services':[{'id':key,'name':key.title(),'enabled':True,'last_success_at':None,'item_count':None,'on':f'{key} on text','off':f'{key} off text'}
        for key in ['calendar','notes','contacts','photos','world-clocks','weather-places','tide-sources']]}
window._render();pump(.3)
window.select_panel('sync');pump()
def service_switch(title):
    row=next(w for w in descendants(window) if isinstance(w,Adw.ActionRow) and w.get_title()==title)
    return next(w for w in descendants(row) if isinstance(w,Gtk.Switch))
assert len([w for w in descendants(window) if isinstance(w,Gtk.Switch)])==7
notes=service_switch('Notes')
notes.set_active(False);pump()
dialog=window.get_visible_dialog();assert isinstance(dialog,Adw.AlertDialog)
assert dialog.get_body()=='notes off text' and dialog.get_response_appearance('go')==Adw.ResponseAppearance.DESTRUCTIVE
dialog.close();pump()
assert fake.calls==[] and service_switch('Notes').get_active()
screenshot(window,'cloud-sync-desktop')
window.select_panel('devices');pump()
assert 'Fixture Phone' in labels(window) and 'Removed Devices' in labels(window)
screenshot(window,'cloud-devices-desktop')
window.destroy();pump()
# Shared native light/dark behavior, with the same synthetic state.
Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
window=ConnectWindow(app);window.set_default_size(360,720);window.present()
wait(lambda:window.state is not None);screenshot(window,'mobile-dark-360');window.destroy();pump()
# Snapshot preserved when broker disappears.
window=ConnectWindow(app);window.present();wait(lambda:window.state is not None)
Gio.bus_unown_name(owner);daemon.close()
wait(lambda:window.state.get('stale'))
assert window.state['profile']['name']=='Synthetic Test Account'
assert window.state['usage']['used_bytes'] is None
screenshot(window,'stale-mobile');window.destroy();pump()
(out/'result.json').write_text(json.dumps({'passed':True,'cases':results,'dbus_activation':True,'daemon_loss_preserved_stale':True,'synthetic_only':True},indent=2))
print('PASS native Connect runtime')
