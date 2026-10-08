"""Source-owned user-session Connect daemon; no autostart login scripts.

Account state is separate from device capability grants. Public methods expose
no token, raw D-Bus, filesystem, modem or network target chosen by a caller.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import logging
from pathlib import Path
import signal
import sqlite3
import time

from .account import AccountConfig,AccountModel,AccountAPI,SecretTokens,AccountError,authorization_current

BUS='org.projectluma.Connect1'
PATH='/org/projectluma/Connect'


def wait_event_owners(daemons,timeout=35):
    """Bound shutdown before closing the shared HTTP client under SSE readers."""
    deadline=time.monotonic()+timeout
    threads=[]
    for daemon in daemons:
        for runtime in (daemon.relay_runtime,daemon.calls.relay):
            if runtime is not None and runtime.thread is not None:threads.append(runtime.thread)
    for thread in threads:thread.join(max(0,deadline-time.monotonic()))
    return not any(thread.is_alive() for thread in threads)


COMPANION_METHODS=frozenset({'StartCompanionPairing','CancelCompanionPairing','CompanionDevices','RemoveCompanion',
    'SetCompanionGrants','CompanionInvoke','CompanionSendFile','CancelCompanionTransfer','CancelCompanionReply',
    'CompanionRequestHotspot','CompanionApprove','CompanionSelectForMessages','CompanionStartCamera',
    'CompanionShowScreen','CompanionStopMedia','CompanionPowerModePair','CompanionPowerModeOpen','CompanionSetUpCalls'})


XML=f'''<node><interface name="{BUS}">
  <method name="GetQuickState"><arg name="state" type="s" direction="out"/></method>
  <method name="ConnectCloudRequest"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="GetCollaborationContext"><arg type="s" direction="out"/></method>
  <method name="GetCollaborationFavorites"><arg type="s" direction="out"/></method>
  <method name="CollaborationRequest"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="SetEnabled"><arg name="enabled" type="b" direction="in"/></method>
  <method name="GetState"><arg name="state" type="s" direction="out"/></method>
  <method name="Refresh"/>
  <method name="RecoverDeviceRegistration"/>
  <method name="UnlockAccount"/>
  <method name="ExchangeMessage"><arg name="peer" type="s" direction="in"/><arg name="request" type="s" direction="in"/><arg name="receipt" type="s" direction="out"/></method>
  <method name="PairMessageDevice"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="EnrollMessageRelay"><arg name="peer" type="s" direction="in"/><arg name="peer_device_id" type="s" direction="in"/><arg name="role" type="s" direction="in"/></method>
  <method name="SelectMessagePhone"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="u" direction="in"/></method>
  <method name="SetMessageSending"><arg type="s" direction="in"/><arg type="b" direction="in"/><arg type="b" direction="in"/></method>
  <method name="RevokeMessagePeer"><arg type="s" direction="in"/></method>
  <method name="StartMessageSharing"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="u" direction="in"/></method>
  <method name="StopMessageSharing"/>
  <method name="RetryCallConnection"><arg type="s" direction="in"/></method>
  <method name="GetCallState"><arg type="s" direction="out"/></method>
  <method name="GetPhoneContext"><arg type="s" direction="out"/></method>
  <method name="GetPhoneHistory"><arg type="s" direction="out"/></method>
  <method name="GetPhoneTogether"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="PhoneCompanionRequest"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="ClosePhoneCompanion"><arg type="s" direction="out"/></method>
  <method name="ExchangeCall"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="SelectCallPhone"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="u" direction="in"/></method>
  <method name="SelectCallRelayPhone"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="SetCallPermission"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="in"/><arg type="b" direction="in"/></method>
  <method name="StartCallSharing"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="u" direction="in"/></method>
  <method name="StopCallSharing"/>
  <method name="StartCallAudio"><arg type="s" direction="in"/></method>
  <method name="StopCallAudio"/>
  <method name="SetCallAudioMuted"><arg type="b" direction="in"/></method>
  <signal name="CallsChanged"/>
  <method name="OpenAccountSettings"/>
  <method name="BeginLogin"><arg name="email" type="s" direction="in"/></method>
  <method name="UpgradeSession"/>
  <method name="SetSyncEnabled"><arg name="category" type="s" direction="in"/><arg name="enabled" type="b" direction="in"/></method>
  <method name="SignOut"><arg name="target" type="s" direction="in"/></method>
  <method name="StartCompanionPairing"><arg name="phone_to_desktop" type="as" direction="in"/><arg name="desktop_to_phone" type="as" direction="in"/><arg name="uri" type="s" direction="out"/></method>
  <method name="CancelCompanionPairing"/>
  <method name="CompanionDevices"><arg name="devices" type="s" direction="out"/></method>
  <method name="RemoveCompanion"><arg name="fingerprint" type="s" direction="in"/></method>
  <method name="SetCompanionGrants"><arg name="fingerprint" type="s" direction="in"/><arg name="incoming" type="as" direction="in"/></method>
  <method name="CompanionInvoke"><arg name="fingerprint" type="s" direction="in"/><arg name="capability" type="s" direction="in"/><arg name="payload" type="s" direction="in"/><arg name="receipt" type="s" direction="out"/></method>
  <method name="CompanionSendFile"><arg name="fingerprint" type="s" direction="in"/><arg name="path" type="s" direction="in"/><arg name="transfer" type="s" direction="out"/></method>
  <method name="CancelCompanionTransfer"><arg name="transfer" type="s" direction="in"/></method>
  <method name="CancelCompanionReply"/>
  <method name="CompanionRequestHotspot"><arg name="fingerprint" type="s" direction="in"/><arg name="result" type="s" direction="out"/></method>
  <method name="CompanionApprove"><arg name="fingerprint" type="s" direction="in"/><arg name="reason" type="s" direction="in"/><arg name="app" type="s" direction="in"/><arg name="approved" type="b" direction="out"/></method>
  <method name="CompanionSelectForMessages"><arg name="fingerprint" type="s" direction="in"/></method>
  <method name="CompanionStartCamera"><arg name="fingerprint" type="s" direction="in"/><arg name="state" type="s" direction="out"/></method>
  <method name="CompanionShowScreen"><arg name="fingerprint" type="s" direction="in"/><arg name="state" type="s" direction="out"/></method>
  <method name="CompanionStopMedia"><arg name="fingerprint" type="s" direction="in"/></method>
  <method name="CompanionSetUpCalls"><arg name="fingerprint" type="s" direction="in"/><arg name="result" type="s" direction="out"/></method>
  <method name="CompanionPowerModePair"><arg name="fingerprint" type="s" direction="in"/><arg name="qr" type="s" direction="out"/></method>
  <method name="CompanionPowerModeOpen"><arg name="fingerprint" type="s" direction="in"/><arg name="package" type="s" direction="in"/><arg name="result" type="s" direction="out"/></method>
  <signal name="CompanionChanged"/>
  <signal name="CompanionReplyRequested"><arg name="request" type="s"/></signal>
  <signal name="StateChanged"><arg name="generation" type="t"/></signal>
</interface></node>'''


def load_config(path=Path('/etc/luma-connect/client.json')):
    if not path.exists(): return None
    stat=path.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o022 or path.is_symlink():
        raise AccountError('untrusted_client_configuration')
    raw=path.read_text()
    if len(raw)>16384: raise AccountError('invalid_client_configuration')
    return AccountConfig(**json.loads(raw))


class Daemon:
    # Companion phones (ADR-021) are optional; a daemon built without them keeps working.
    companion=None;companion_effects=None;companion_network_timer=None
    # The ADR-033 agent presence (agent.ConnectAgent), when started as the agent.
    agent=None

    def __init__(self,connection,model,config=None,*,observe_environment=True,relay_runtime=None):
        from gi.repository import Gio,GLib
        self.Gio,self.GLib=Gio,GLib
        self.connection,self.model,self.config=connection,model,config
        self.relay_runtime=relay_runtime
        self.relay_busy=False
        self.relay_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='luma-relay')
        self.latest=model.snapshot();self.latest['login_available']=bool(config and model.tokens)
        self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='luma-connect')
        self.busy=False;self.closed=False;self.login=None;self.started=0
        self.credentials_pending=False
        self.lease_timer=None
        self.credential_locked=False;self.awake=True;self.online=True
        self.environment_generation=0;self.route_check=None;self.system_bus=None;self.sleep_subscription=None
        from .message_devices import MessageDevices
        directory=Path.home()/'.local/share/luma-connect/device'
        from .quick_state import LinkIntent
        self.link_intent=LinkIntent(directory.parent/'enabled.json')
        try:self.enabled=self.link_intent.load()
        except (OSError,ValueError):self.enabled=False
        self.latest['enabled']=self.enabled
        def account():
            state=self.model.snapshot()
            if (self.closed or not self.enabled or not self.awake or not self.online or self.credential_locked
                    or not authorization_current(state)):return None
            return (state.get('identity') or {}).get('account_id')
        def native_adapter(peer,allowed):
            from prairie_apps.messages_backend import MessageStore
            from .native import NativeMessages
            from prairie_apps.messages_reply import ReplySender
            from prairie_apps.messages_mms import MmsMessagingTransport
            store=MessageStore()
            details=next(row for row in self.devices.devices() if row['peer']==peer)
            epoch=details['epoch']
            def send_allowed():
                if not allowed():return False
                current=next((row for row in self.devices.devices() if row['peer']==peer),{})
                return current.get('epoch')==epoch and current.get('can_receive_sends') is True
            native=NativeMessages(store,ReplySender(store.path),pair_token=epoch,authorized=allowed,
                                  send_authorized=send_allowed,mms_transport=MmsMessagingTransport())
            return native,store.close
        def receiver(peer,address,port,allowed):
            from .read_receiver import ReadReceiver
            return ReadReceiver(directory,peer,address=address,port=port,authorized=allowed,adapter_factory=lambda:native_adapter(peer,allowed),allow_send=True,event_driven=True)
        self.devices=MessageDevices(directory,Path.home()/'.config/luma-connect/messages-phone.json',account=account,receiver_factory=receiver)
        from .device_registration import DeviceRegistration
        self.device_registration=DeviceRegistration(directory,self.model,enabled=lambda:account() is not None)
        from .relay_enrollment import RelayEnrollment
        self.relay_enrollment=RelayEnrollment(directory,self.device_registration,self.model,
            Path.home()/'.config/luma-connect/relay-bindings.json',enabled=lambda:account() is not None)
        self.enrollment=None
        def make_relay():
            if not config or not self.model.api or not self.model.tokens:
                raise AccountError('relay_enrollment_unavailable')
            from .relay_binding import load_bindings
            from .relay_runtime import RelayRuntime
            bindings=load_bindings(Path.home()/'.config/luma-connect/relay-bindings.json')
            if not bindings:return None
            def relay_account():
                from .relay_enrollment import registered_relay_account
                return registered_relay_account(directory,self.device_registration,
                    self.model,bindings,account())
            def bearer():
                if not relay_account():raise PermissionError('account unavailable')
                token=self.model.authority_token()
                if not relay_account():raise PermissionError('account unavailable')
                return token
            return RelayRuntime(directory,bindings,self.model.api,bearer=bearer,account=relay_account,
                adapter_factory=lambda binding,allowed:native_adapter(binding.peer,allowed))
        self.make_relay=make_relay
        if self.relay_runtime is None and config and self.model.api and self.model.tokens:
            self.relay_runtime=make_relay()
        from .call_devices import CallDevices
        def voice_available(transport):
            try:
                result=transport.proxy.call_sync('GetStatus',GLib.Variant('()',()),
                    Gio.DBusCallFlags.NONE,1500,None)
                status=result.unpack()[0] if result is not None else {}
                return isinstance(status,dict) and status.get('registered') is True
            except Exception:return False
        def call_receiver(peer,address,port,allowed,control_allowed):
            from .call_events import CallListener
            def adapter(changed,owner_changed):
                from prairie_apps.phone_backend import ImsVoiceTransport,IncomingCallMonitor
                from .native import NativeCalls
                transport=ImsVoiceTransport()
                native=NativeCalls(transport,authorized=allowed,control_authorized=control_allowed,now=time.time,
                    voice_available=lambda:voice_available(transport))
                monitor=IncomingCallMonitor(on_added=changed,on_state=changed,on_ended=changed)
                # Register native callbacks on the daemon's main context; the
                # listener worker serializes the actual snapshots and controls.
                monitor.start()
                bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
                subscription=bus.signal_subscribe('org.freedesktop.DBus','org.freedesktop.DBus',
                    'NameOwnerChanged','/org/freedesktop/DBus',ImsVoiceTransport.BUS_NAME,
                    Gio.DBusSignalFlags.NONE,owner_changed)
                registration=bus.signal_subscribe(ImsVoiceTransport.BUS_NAME,ImsVoiceTransport.INTERFACE,
                    'RegistrationChanged',ImsVoiceTransport.OBJECT_PATH,None,Gio.DBusSignalFlags.NONE,lambda *_:changed())
                return native,lambda:(monitor.stop(),bus.signal_unsubscribe(subscription),bus.signal_unsubscribe(registration))
            return CallListener(directory,peer,address=address,port=port,authorized=allowed,adapter_factory=adapter)
        self.calls=CallDevices(directory,Path.home()/'.config/luma-connect/calls-phone.json',account=account,
            changed=lambda:GLib.idle_add(self._calls_changed),receiver_factory=call_receiver,
            on_incoming=lambda:GLib.idle_add(self._present_paired_phone))
        self.calls.dispatch=GLib.idle_add
        def make_calls():
            if not config or not self.model.api or not self.model.tokens:return None
            from .relay_binding import load_bindings
            from .relay_enrollment import registered_relay_account
            from .call_runtime import CallRuntime
            bindings=load_bindings(Path.home()/'.config/luma-connect/relay-bindings.json')
            if not bindings:return None
            def current():
                return registered_relay_account(directory,self.device_registration,self.model,bindings,account())
            def bearer():
                if not current():raise PermissionError('call account unavailable')
                token=self.model.authority_token()
                if not current():raise PermissionError('call account unavailable')
                return token
            def adapter(binding,allowed,changed):
                from prairie_apps.phone_backend import ImsVoiceTransport,IncomingCallMonitor
                from .native import NativeCalls
                transport=ImsVoiceTransport()
                native=NativeCalls(transport,authorized=allowed,voice_available=lambda:voice_available(transport),
                    control_authorized=lambda:allowed() and self.calls.permission(binding.peer,'calls.control',
                        incoming=True,account=binding.account,epoch=binding.epoch),now=time.time)
                monitor=IncomingCallMonitor(on_added=changed,on_state=changed,on_ended=changed)
                monitor.start()
                bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
                def owner_changed(*_):
                    native.invalidate()
                    if self.calls.relay:self.calls.relay.pause()
                subscription=bus.signal_subscribe('org.freedesktop.DBus','org.freedesktop.DBus',
                    'NameOwnerChanged','/org/freedesktop/DBus',ImsVoiceTransport.BUS_NAME,
                    Gio.DBusSignalFlags.NONE,owner_changed)
                registration=bus.signal_subscribe(ImsVoiceTransport.BUS_NAME,ImsVoiceTransport.INTERFACE,
                    'RegistrationChanged',ImsVoiceTransport.OBJECT_PATH,None,Gio.DBusSignalFlags.NONE,lambda *_:changed())
                stopped=[False]
                def cleanup():
                    if stopped[0]:return
                    stopped[0]=True;monitor.stop();bus.signal_unsubscribe(subscription);bus.signal_unsubscribe(registration);native.invalidate()
                return native,cleanup
            return CallRuntime(directory,bindings,self.model.api,bearer=bearer,account=current,
                permission=self.calls.permission,selected=lambda:self.calls.selection,adapter_factory=adapter,
                changed=lambda:GLib.idle_add(self._calls_changed),incoming=self._present_paired_phone,
                dispatch=GLib.idle_add,later=lambda seconds,callback:GLib.timeout_add(max(1,int(seconds*1000)),callback),
                cancel=GLib.source_remove)
        self.make_calls=make_calls
        self.calls.relay=make_calls()
        self.call_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='connect-call-control')
        self.call_busy=False;self.call_reads=0
        self.latest['message_devices']=[]
        self.pairing=None
        # Companion phones (ADR-021) need no account: the Connect link switch and
        # a pairing are the only gates. Work runs on its own workers, results on
        # the main loop, and every desktop effect goes to its owner.
        self.companion_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='connect-companion')
        self.companion_files=ThreadPoolExecutor(max_workers=1,thread_name_prefix='connect-companion-files')
        self.companion_network_timer=None
        self.companion,self.companion_effects=self._make_companion(directory)
        self.registration=connection.register_object(PATH,Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],self._call,None,None)
        from .lifecycle import Recovery
        self.recovery=Recovery(lambda:self._schedule(self.model.refresh),
            later=lambda seconds,callback:GLib.timeout_add(max(1,int(seconds*1000)),callback),cancel=GLib.source_remove)
        self.secret_subscription=connection.signal_subscribe('org.freedesktop.secrets',
            'org.freedesktop.DBus.Properties','PropertiesChanged',None,
            'org.freedesktop.Secret.Collection',Gio.DBusSignalFlags.NONE,self._credentials_changed)
        self.network_monitor=None;self.network_handler=None
        if observe_environment:
            self.network_monitor=Gio.NetworkMonitor.get_default()
            self.network_handler=self.network_monitor.connect('network-changed',self._network_changed)
            try:
                self.system_bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
                self.sleep_subscription=self.system_bus.signal_subscribe('org.freedesktop.login1',
                    'org.freedesktop.login1.Manager','PrepareForSleep','/org/freedesktop/login1',None,
                    Gio.DBusSignalFlags.NONE,self._sleep_changed)
            except GLib.Error:pass  # no wake support is inferred on this platform
        self.recovery.request()
        self.companion.set_enabled(self.enabled)

    def _make_companion(self,directory):
        from . import companion, companion_desktop as desktop
        GLib,Gio=self.GLib,self.Gio
        holder=[]
        def dispatch(work):
            GLib.idle_add(lambda:(None if self.closed else work(),False)[1])
        def guarded(work):
            try:work()
            except Exception as error:
                # Type only: phone data and paths must not reach the journal.
                logging.getLogger(__name__).warning('Companion work failed (%s)',type(error).__name__)
        def submit(work):
            if not self.closed:self.companion_worker.submit(guarded,work)
        def submit_file(work):
            if not self.closed:self.companion_files.submit(guarded,work)
        def optional(label,factory):
            try:return factory()
            except Exception as error:
                logging.getLogger(__name__).info('Companion %s owner unavailable (%s)',label,type(error).__name__)
                return None
        service=lambda:holder[0]
        notifier=optional('notification',lambda:desktop.FreedesktopNotifier.gio(
            act=lambda peer,payload:service().effect_call(peer,'notifications.act',payload),
            reply=lambda peer,request:service().request_reply(peer,request),
            launch=desktop.gio_launch,show_file=desktop.gio_show_file,
            can_act=lambda peer:'notifications.act' in service().outgoing(peer)))
        clipboard=optional('clipboard',desktop.ShellClipboard.gio)
        mpris=desktop.MprisBridge(desktop.GioMprisExport,device_name=lambda peer:service().device_name(peer),
            control=lambda peer,payload:service().effect_call(peer,'media.control',payload))
        ringer=optional('sound',desktop.DesktopRinger.gio)
        injector=optional('input',desktop.InputInjector.gio)
        effects={'media':lambda peer,payload:dispatch(lambda:mpris.update(peer,payload))}
        # Notify and the Shell clipboard are synchronous, thread-safe D-Bus calls
        # from the listener thread; MPRIS objects and GSound belong to this context.
        if notifier:effects['notify']=notifier
        if clipboard:effects.update(clipboard_set=clipboard.set_text,clipboard_get=clipboard.get_text)
        if ringer:effects['ring']=lambda ring:dispatch(lambda:ringer(ring))
        if injector:effects['input']=injector
        from .companion_approval import ApprovalBroker
        approval=ApprovalBroker(directory,submit=lambda work:submit(work))
        effects['approval']=approval
        dnd=optional('do not disturb',lambda:desktop.GnomeDnd.gio(
            send=lambda on:submit(lambda:desktop.GnomeDnd.broadcast(companion.call,directory,service().snapshot()['companion_devices'],on)),
            run_on_main=desktop.main_loop_runner()))
        if dnd:effects['dnd']=dnd
        wifi=optional('Wi-Fi',desktop.NetworkManagerWifi.gio)
        hotspot=desktop.HotspotJoiner(wifi,directory) if wifi else None
        from . import companion_media, companion_power as power
        def media_sink(session):
            # One factory per session so the camera node and viewer carry this phone's name.
            return companion_media.gtk_sink_factory(service().device_name(session.peer),application=None)(session)
        from . import companion_calls as calls
        bluez=optional('Bluetooth',calls.BlueZ.gio)
        bonding=calls.BluetoothBonding(bluez,directory) if bluez else None
        # Phone owns the live call; the daemon only refuses to change audio roles while one is active.
        roles=optional('WirePlumber',lambda:calls.WirePlumberRoles.gio(call_active=lambda:False))
        def select_for_messages(fingerprint):
            from .message_devices import MessageDevices
            MessageDevices(directory,Path.home()/'.config/luma-connect/messages-phone.json',account=lambda:None).select_companion(fingerprint)
        downloads=GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or str(Path.home()/'Downloads')
        def reply_requested(request):
            self.connection.emit_signal(None,PATH,BUS,'CompanionReplyRequested',GLib.Variant('(s)',(json.dumps(request),)))
            app=Gio.DesktopAppInfo.new('org.projectluma.Connect.desktop')
            if app:app.launch([],None)
        holder.append(desktop.CompanionService(directory,downloads=downloads,name=desktop.desktop_name,
            dispatch=dispatch,submit=submit,submit_transfer=submit_file,changed=self._companion_changed,
            effects=effects,addresses=desktop.local_addresses,publisher=desktop.AvahiPublisher.gio,
            reply_requested=reply_requested,enabled=self.enabled,hotspot=hotspot,approval=approval,
            messages_selection=select_for_messages,media_sink_factory=media_sink,
            adb=power.Adb() if power.Adb().available else None,scrcpy=power.ScrcpyLauncher() if power.ScrcpyLauncher().path else None,
            bonding=bonding,roles=roles,calls_selection_path=Path.home()/'.config/luma-connect/calls-phone.json'))
        from . import companion_streams as streams
        companion_service=holder[0]
        addresses=lambda:companion_service.listening_on
        inputs=streams.InputStreams(companion_service.adapters,addresses=addresses) if injector else None
        files=streams.FileStreams(companion_service.adapters,addresses=addresses)
        companion_service.adapters.for_peer=streams.stream_adapters(companion_service.adapters,inputs=inputs,files=files)
        return holder[0],{'notifier':notifier,'mpris':mpris,'ringer':ringer,'injector':injector,'clipboard':clipboard,
                          'dnd':dnd,'approval':approval,'input_streams':inputs,'file_streams':files}

    def _companion_changed(self):
        if self.closed:return False
        self.connection.emit_signal(None,PATH,BUS,'CompanionChanged',None)
        return False

    def _companion_removed(self,peer):
        for owner in ('input_streams','file_streams'):
            if self.companion_effects.get(owner):self.companion_effects[owner].close_peer(peer)
        self.companion_effects['mpris'].remove(peer)
        if self.companion_effects['notifier']:self.companion_effects['notifier'].forget(peer)

    def _companion_network(self):
        # Coalesce NetworkMonitor hints; the worker restarts the listener only
        # when the explicit address set actually changed.
        if self.closed or self.companion is None:return
        if self.companion_network_timer is not None:self.GLib.source_remove(self.companion_network_timer)
        def run():
            self.companion_network_timer=None
            if not self.closed:self.companion.network_changed()
            return False
        self.companion_network_timer=self.GLib.timeout_add_seconds(2,run)

    def _companion_call(self,method,values,invocation):
        GLib,service=self.GLib,self.companion
        def fail(name='Unavailable',text='The phone is not reachable right now'):
            invocation.return_dbus_error(BUS+'.Error.'+name,text)
        if self.closed:return fail()
        try:
            if method=='CompanionDevices':
                invocation.return_value(GLib.Variant('(s)',(json.dumps(service.snapshot()['companion_devices'],allow_nan=False),)));return
            if method=='StartCompanionPairing':
                service.start_pairing(*values,lambda uri:invocation.return_value(GLib.Variant('(s)',(uri,))) if uri else fail())
                return
            if method=='CompanionInvoke':
                service.invoke(*values,lambda receipt:invocation.return_value(GLib.Variant('(s)',(json.dumps(receipt),)))
                               if receipt is not None else fail())
                return
            if method=='CompanionRequestHotspot':
                service.request_hotspot(values[0],lambda result:invocation.return_value(GLib.Variant('(s)',(result,))));return
            if method=='CompanionApprove':
                # Confirmation for Luma-owned prompts only; never a polkit or login authorization (ADR-021 gate).
                service.approve(*values,lambda approved:invocation.return_value(GLib.Variant('(b)',(approved,))));return
            if method in ('CompanionStartCamera','CompanionShowScreen'):
                service.start_media(values[0],'camera' if method=='CompanionStartCamera' else 'screen',
                                    lambda state:invocation.return_value(GLib.Variant('(s)',(state,))));return
            if method=='CompanionSetUpCalls':
                service.set_up_calls(values[0],lambda code:invocation.return_value(GLib.Variant('(s)',(code,))));return
            if method=='CompanionPowerModePair':
                invocation.return_value(GLib.Variant('(s)',(service.power_pair(values[0],lambda _code:None),)));return
            if method=='CompanionPowerModeOpen':
                package=values[1] or None
                if package is not None and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+',package):raise ValueError('invalid package')
                invocation.return_value(GLib.Variant('(s)',(service.power_open(values[0],package),)));return
            if method=='CompanionSendFile':
                invocation.return_value(GLib.Variant('(s)',(service.send_file(*values),)));return
            if method=='CancelCompanionPairing':service.cancel_pairing()
            elif method=='RemoveCompanion':service.remove(values[0],removed=self._companion_removed)
            elif method=='SetCompanionGrants':service.set_grants(*values)
            elif method=='CancelCompanionTransfer':service.cancel_transfer(values[0])
            elif method=='CancelCompanionReply':service.cancel_reply()
            elif method=='CompanionSelectForMessages':service.select_for_messages(values[0])
            elif method=='CompanionStopMedia':service.stop_media(values[0])
        except PermissionError:return fail('Denied','That is not allowed for this phone')
        except (ValueError,TypeError,KeyError):return fail('InvalidRequest','Invalid phone request')
        invocation.return_value(self.GLib.Variant('()',()))

    def _pause_connection(self):
        self.environment_generation+=1
        if self.route_check:
            self.route_check.cancel();self.route_check=None
        self._cancel_lease()
        self.devices.suspend();self.calls.suspend()
        if self.relay_runtime:self.relay_runtime.pause()
        self.latest.update(stale=True,service_status='offline',suspended=not self.awake)
        self._signal()

    def _cancel_lease(self):
        if self.lease_timer is not None:
            self.GLib.source_remove(self.lease_timer);self.lease_timer=None

    def _network_changed(self,monitor,_available):
        if self.closed:return
        self._companion_network()
        # NetworkMonitor reports unrelated interface/default-route changes too.
        # Coalesce the hints and preserve an authorized working connection while
        # checking the configured endpoint, including direct LAN without WAN.
        if self.config is None or not self.awake or self.route_check is not None:return
        check=self.Gio.Cancellable();self.route_check=check
        generation=self.environment_generation
        endpoint=self.Gio.NetworkAddress.parse_uri(self.config.api_origin,443)
        def checked(source,result):
            if self.route_check is not check:return
            self.route_check=None
            if self.closed or not self.awake or generation!=self.environment_generation:return
            try:reachable=bool(source.can_reach_finish(result))
            except self.GLib.Error:reachable=False
            if reachable==self.online:return
            self.online=reachable
            if not reachable:self._pause_connection()
            self.recovery.environment(online=reachable)
        # Test route reachability, not just default-Internet availability: a
        # direct LAN or loopback peer can remain reachable without a WAN route.
        monitor.can_reach_async(endpoint,check,checked)

    def _sleep_changed(self,*args):
        if self.closed:return
        self.awake=not args[-1].unpack()[0]
        if self.awake:self._companion_network()
        self._pause_connection();self.recovery.environment(awake=self.awake)
        if self.awake and self.network_monitor:self._network_changed(self.network_monitor,True)

    def _credentials_changed(self,*args):
        # Secret Service owns unlocking. Refresh on its event, never replay a password.
        interface,changed,invalidated=args[-1].unpack()
        if 'Locked' not in changed and 'Locked' not in invalidated:return
        path=getattr(self.model.tokens,'collection_path',None)
        if path is not None and args[2]!=path:return
        if changed.get('Locked') is True:
            self.credential_locked=True
            self._cancel_lease()
            self.model.pause_account();self.devices.suspend();self.calls.suspend()
            if self.relay_runtime:self.relay_runtime.pause()
            self.latest.update(account_status='locked',credential_status='locked',stale=True,
                               service_status='unavailable')
            self._signal()
        elif changed.get('Locked') is False:self.credential_locked=False
        if not self.awake or not self.online or not self._schedule(self.model.refresh):self.credentials_pending=True

    def _publish(self,state,error=None,environment_generation=None):
        if self.closed: return False
        self.busy=False
        if environment_generation is not None and environment_generation!=self.environment_generation:
            self.recovery.completed(self.latest)
            if self.awake and self.online:self.recovery.request()
            return False
        if self.credentials_pending:
            self.credentials_pending=False
            if self.awake and self.online:
                self._schedule(self.model.refresh)
                return False
        self.latest=state
        self.latest['enabled']=self.enabled
        if not self.awake or not self.online:
            self.latest.update(stale=True,service_status='offline',suspended=not self.awake)
        if self.credential_locked:
            self.latest.update(account_status='locked',credential_status='locked',stale=True,service_status='unavailable')
        self.latest['login_available']=bool(self.config and self.model.tokens) and state.get('account_status')!='locked'
        self.latest['error']=error;self.latest['busy']=False
        if self.relay_runtime:
            if self.enabled and authorization_current(self.latest):self.relay_runtime.resume()
            else:self.relay_runtime.pause()
        if self.calls.relay is None and self.enrollment and self.enrollment.get('status')=='enrolled':
            self.calls.relay=self.make_calls()
        if self.calls.relay:
            if self.enabled and authorization_current(self.latest):
                self.calls.relay.resume();self.calls.relay.refreshed()
            else:self.calls.relay.pause()
        self._signal()
        self.recovery.completed(self.latest)
        self._cancel_lease()
        deadline=self.latest.get('valid_until')
        if self.latest.get('account_status')=='signed_in' and not self.latest.get('stale') and deadline:
            def expired():
                self.lease_timer=None
                if self.closed or self.latest.get('account_status')!='signed_in':return False
                self.devices.suspend();self.calls.suspend();self.latest.update(stale=True,service_status='unavailable');self._signal()
                if self.relay_runtime:self.relay_runtime.pause()
                return False
            remaining=min(deadline-time.time(),self.latest.get('lease_deadline_monotonic',0)-time.monotonic())
            self.lease_timer=self.GLib.timeout_add(max(1,int(remaining*1000)),expired)
        return False

    def _present_paired_phone(self):
        if self.closed or not self.calls.state()['connected']:return False
        app=self.Gio.DesktopAppInfo.new('org.projectluma.Phone.desktop')
        if app:app.launch([],None)
        return False

    def _calls_changed(self):
        if self.closed:return False
        self.connection.emit_signal(None,PATH,BUS,'CallsChanged',None)
        return False

    def _signal(self):
        self.connection.emit_signal(None,PATH,BUS,'StateChanged',self.GLib.Variant('(t)',(self.latest['generation'],)))
        if self.agent is not None:self.agent.update()

    def _public_state(self):
        state=dict(self.latest)
        # Calls change independently of account refresh. Never publish the
        # worker's old connected row after channel loss or lease expiry.
        current=(not self.closed and self.enabled and self.awake and self.online
                 and not self.credential_locked and authorization_current(state))
        try:rows=self.calls.relay.devices() if self.calls.relay else []
        except (sqlite3.Error,OSError):
            # Preserve device labels/intent, but never stale authority, when
            # local policy cannot be read. This endpoint remains inspectable.
            rows=state.get('call_devices',[]);current=False
        state['call_devices']=[dict(row) for row in rows]
        if self.companion is not None:state.update(self.companion.snapshot())
        if not current:
            for row in state['call_devices']:
                row.update(connected=False,connection=dict(reason='unavailable',retry_at=None),
                           permissions={cap:False for cap in row['permissions']})
        return state

    def _schedule(self,work):
        if self.closed or self.busy: return False
        self.busy=True;self.started=time.monotonic()
        self.latest['busy']=True;self.latest['error']=None;self._signal()
        generation=self.environment_generation
        def run():
            error=None
            try: work()
            except AccountError as failure: error=failure.code
            except Exception: error='service_unavailable'
            state=self.model.snapshot()
            if self.enabled and authorization_current(state):
                import socket
                try:state['device_registration']=self.device_registration.ensure(socket.gethostname()[:80])
                except AccountError as failure:state['device_registration']={'status':'unavailable','error':failure.code}
                except Exception:state['device_registration']={'status':'unavailable'}
            if self.closed or state.get('account_status')=='signed_out':self.devices.stop();self.calls.stop()
            elif (state.get('account_status')!='signed_in' or state.get('stale')
                    or not self.enabled or not self.awake or not self.online or self.credential_locked
                    or generation!=self.environment_generation):self.devices.suspend();self.calls.suspend()
            else:
                try:self.devices.resume();self.calls.resume()
                except PermissionError:pass
                except OSError:error=error or 'sharing_unavailable'
            state['pairing']=self.pairing
            state['relay_enrollment']=self.enrollment
            state['call_devices']=self.calls.relay.devices() if self.calls.relay else []
            try: state['message_devices']=self.devices.devices()
            except Exception:
                state['message_devices']=[]
                error=error or 'message_devices_unavailable'
            if self.relay_runtime:
                for device in state['message_devices']:
                    if self.relay_runtime.sharing(device['peer']):device['sharing']=True
            self.GLib.idle_add(self._publish,state,error,generation)
        # A failure while collecting device state must still release busy.
        # Future exceptions otherwise remain invisible to the main context.
        def completed(future):
            try:future.result()
            except Exception as error:
                # Do not log exception text, locals, credentials or server data.
                frames=[];trace=error.__traceback__
                while trace is not None:
                    frames.append(trace.tb_frame.f_code.co_name)
                    trace=trace.tb_next
                logging.getLogger(__name__).warning('Account state collection failed (%s; frames=%s)',
                    type(error).__name__,','.join(frames[-8:]))
                self.GLib.idle_add(self._worker_failed,generation)
        self.worker.submit(run).add_done_callback(completed)
        return True

    def _worker_failed(self,generation):
        state=dict(self.latest)
        state.update(stale=True,service_status='unavailable')
        return self._publish(state,'state_collection_failed',generation)

    def _call(self,_connection,_sender,_path,_interface,method,parameters,invocation):
        if method == 'ConnectCloudRequest':
            from .connect_ui_broker import ConnectUIBroker
            if not hasattr(self, 'connect_ui_broker'):
                self.connect_ui_broker = ConnectUIBroker()
            self.connect_ui_broker.dispatch(_connection, _sender, parameters.unpack(), invocation, self.GLib)
            return
        if method in ('GetCollaborationContext', 'GetCollaborationFavorites', 'CollaborationRequest'):
            from .collaboration_broker import CollaborationBroker
            if not hasattr(self, 'collaboration_broker'):
                self.collaboration_broker = CollaborationBroker()
            self.collaboration_broker.dispatch(_connection, _sender, method, parameters.unpack(), invocation, self.GLib)
            return
        from .phone_app_broker import METHODS as PHONE_METHODS, PhoneAppBroker
        if method in PHONE_METHODS:
            if not hasattr(self, 'phone_app_broker'):
                self.phone_app_broker = PhoneAppBroker(self)
            self.phone_app_broker.dispatch(_connection, _sender, method, parameters.unpack(), invocation, self.GLib)
            return
        # The existing native user API is retained; an independent sandbox UI
        # must prove the official signed Connect deployment, not merely a bus grant.
        from .connect_ui_broker import require_connect_if_sandbox
        try:
            require_connect_if_sandbox(_connection, _sender, method)
        except Exception:
            invocation.return_dbus_error('org.projectluma.Connect1.UI.Refused', 'Connect access is unavailable.')
            return
        if method=='GetQuickState':
            from .quick_state import quick_state
            invocation.return_value(self.GLib.Variant('(s)',(json.dumps(quick_state(self.latest,self.enabled)),)));return
        if method=='GetState':
            invocation.return_value(self.GLib.Variant('(s)',(json.dumps(self._public_state(),allow_nan=False),)));return
        if method=='GetCallState':
            invocation.return_value(self.GLib.Variant('(s)',(json.dumps(self.calls.state()),)));return
        values=parameters.unpack()
        if method in COMPANION_METHODS:
            self._companion_call(method,values,invocation);return
        if method=='SetEnabled':
            if self.closed or self.busy:
                invocation.return_dbus_error(BUS+'.Error.Busy','Connect is busy');return
            enabled,=values
            # Fence old callbacks before persistence or transport shutdown.
            self.enabled=False;self.latest['enabled']=False;self.companion.enabled=False
            self._pause_connection()
            def work():
                from .policy import Journal
                self.link_intent.save(False)
                path=self.devices.directory/'continuity.db'
                if path.exists():
                    journal=Journal(path)
                    try:journal.set_enabled(enabled)
                    finally:journal.close()
                if enabled:self.link_intent.save(True)
                self.enabled=enabled
                self.companion.set_enabled(enabled)
                self.model.refresh()
            self._schedule(work)
            invocation.return_value(self.GLib.Variant('()',()));return
        if method=='ExchangeMessage':
            if self.closed or not self.enabled or self.relay_runtime is None or self.relay_busy:
                invocation.return_dbus_error(BUS+'.Error.Unavailable','Message connection unavailable');return
            self.relay_busy=True
            generation=self.environment_generation
            def exchange():
                result=None
                try: result=self.relay_runtime.exchange(*values)
                except Exception: pass  # Never return transport exception text or credentials.
                def publish():
                    self.relay_busy=False
                    if (self.closed or generation!=self.environment_generation or result is None
                            or self.credential_locked or not authorization_current(self.model.snapshot())
                            or not self.relay_runtime.can_publish(*values)):
                        invocation.return_dbus_error(BUS+'.Error.Unavailable','Message connection unavailable')
                    else:
                        invocation.return_value(self.GLib.Variant('(s)',(json.dumps(result,allow_nan=False),)))
                    return False
                self.GLib.idle_add(publish)
            self.relay_worker.submit(exchange)
            return
        if method=='ExchangeCall':
            if self.closed or not self.enabled:
                invocation.return_dbus_error(BUS+'.Error.Busy','A call request is pending');return
            from .transport import _unique
            try:
                if len(values[0].encode())>8192:raise ValueError('call request bound')
                request=json.loads(values[0],object_pairs_hook=_unique)
                if not isinstance(request,dict):raise ValueError('object required')
            except (ValueError,TypeError):
                invocation.return_dbus_error(BUS+'.Error.InvalidRequest','Invalid call request');return
            # Phone and the call activity producer both observe the same call.
            # A read waits its turn on the serial worker instead of failing as
            # "busy", which the UI must present as a lost phone connection.
            # Commands remain exclusive; reads never replay a command.
            read=request.get('capability')=='calls.read'
            if (self.call_reads>=4) if read else self.call_busy:
                invocation.return_dbus_error(BUS+'.Error.Busy','A call request is pending');return
            if read:self.call_reads+=1
            else:self.call_busy=True
            call_generation=self.environment_generation
            def run_call():
                response=None
                try:response=self.calls.exchange_call(request)
                except Exception:pass
                def publish():
                    if read:self.call_reads-=1
                    else:self.call_busy=False
                    if (response is None or self.closed or not self.enabled or self.credential_locked
                            or call_generation!=self.environment_generation or not authorization_current(self.model.snapshot())):invocation.return_dbus_error(BUS+'.Error.Unavailable','Call request not confirmed')
                    else:invocation.return_value(self.GLib.Variant('(s)',(json.dumps(response),)))
                    return False
                self.GLib.idle_add(publish)
            self.call_worker.submit(run_call);return
        if method=='StartCallAudio':
            if self.closed or not self.enabled or self.call_busy or self.calls.relay is None:
                invocation.return_dbus_error(BUS+'.Error.Busy','Call audio unavailable');return
            self.call_busy=True;runtime=self.calls.relay;call_generation=self.environment_generation
            def start_audio():
                success=False
                try:runtime.start_audio(*values);success=True
                except Exception:pass
                def publish():
                    self.call_busy=False
                    if (not success or self.closed or not self.enabled or self.credential_locked
                            or call_generation!=self.environment_generation or not authorization_current(self.model.snapshot())):
                        runtime.stop_audio();invocation.return_dbus_error(BUS+'.Error.Unavailable','Call audio unavailable')
                    else:invocation.return_value(self.GLib.Variant('()',()))
                    return False
                self.GLib.idle_add(publish)
            self.call_worker.submit(start_audio);return
        if method=='SetCallAudioMuted':
            try:
                if not self.calls.relay:raise PermissionError('call audio unavailable')
                self.calls.relay.mute_audio(*values)
                invocation.return_value(self.GLib.Variant('()',()))
            except Exception:invocation.return_dbus_error(BUS+'.Error.Unavailable','Call audio unavailable')
            return
        if method=='StopCallAudio':
            if self.calls.relay:self.calls.relay.stop_audio()
            invocation.return_value(self.GLib.Variant('()',()));return
        if method=='StartCallSharing' and self.calls.relay and self.calls.relay.accepts_sharing(values[0]):
            # Small private intent writes and stop share one main-context
            # ordering; no worker can publish a stale start after stop.
            try:
                self.calls.relay.enable_sharing(values[0])
                invocation.return_value(self.GLib.Variant('()',()))
            except Exception:invocation.return_dbus_error(BUS+'.Error.Unavailable','Call sharing unavailable')
            return
        if method=='StopCallSharing':
            self.calls.stop_sharing()
            if self.calls.relay:self.calls.relay.stop_sharing()
            invocation.return_value(self.GLib.Variant('()',()));return
        if method=='StopMessageSharing':
            self.devices.stop()
            if self.relay_runtime:self.relay_runtime.stop_sharing()
            for device in self.latest.get('message_devices',[]): device['sharing']=False
            self._signal()
            invocation.return_value(self.GLib.Variant('()',()));return
        if method=='EnrollMessageRelay':
            def work():
                self.enrollment=self.relay_enrollment.enroll(*values)
                if self.enrollment['status']=='enrolled' and self.relay_runtime is None:
                    self.relay_runtime=self.make_relay()
        elif method=='PairMessageDevice':
            def work():
                self.pairing=self.devices.pairing_document(*values)
        elif method=='SelectMessagePhone': work=lambda:self.devices.select(*values)
        elif method=='SetMessageSending': work=lambda:self.devices.set_sending(*values)
        elif method=='RevokeMessagePeer': work=lambda:self.devices.revoke(*values)
        elif method=='StartMessageSharing':
            if self.relay_runtime and self.relay_runtime.accepts_sharing(values[0]):
                work=lambda:self.relay_runtime.enable_sharing(values[0])
            else:work=lambda:self.devices.share(*values)
        elif method=='SelectCallRelayPhone':
            def work():
                if self.calls.relay is None:raise PermissionError('registered call pair required')
                peer,label=values
                if not any(b.peer==peer and b.role=='requester' for b in self.calls.relay.bindings.values()):
                    raise PermissionError('registered call requester required')
                binding=next(b for b in self.calls.relay.bindings.values() if b.peer==peer)
                self.calls.relay.retry_policy.retry(binding.pair_id)
                self.calls.select(peer,label,'127.0.0.1',1,carrier='relay')
        elif method=='RetryCallConnection':
            def work():
                if self.calls.relay is None:raise PermissionError('registered call pair required')
                self.calls.relay.retry(*values)
        elif method=='SelectCallPhone':work=lambda:self.calls.select(*values)
        elif method=='SetCallPermission':work=lambda:self.calls.set_permission(*values)
        elif method=='StartCallSharing':work=lambda:self.calls.share(*values)
        elif method=='RecoverDeviceRegistration':
            def work():
                from .device_recovery import recover
                recover(self.device_registration)
        elif method=='Refresh': work=self.model.refresh
        elif method=='UnlockAccount':
            def work():
                if self.model.tokens is None:raise AccountError('keyring_unavailable')
                self.model.tokens.unlock();self.model.refresh()
        elif method=='OpenAccountSettings':
            def work():
                url=self.model.management_url()
                self.GLib.idle_add(lambda:(self.Gio.AppInfo.launch_default_for_uri(url,None),False)[1])
        elif method=='SetSyncEnabled': work=lambda:self.model.set_sync(*values)
        elif method=='SignOut':
            def work():
                if self.relay_runtime:self.relay_runtime.pause()
                self.devices.stop();self.calls.stop();self.model.sign_out(*values)
        elif method in ('BeginLogin','UpgradeSession'):
            upgrade=method=='UpgradeSession'
            if (not self.config or not self.model.tokens or self.latest['account_status']=='locked'
                or (upgrade and not authorization_current(self.latest))
                or (not upgrade and self.latest['account_status']=='signed_in')):
                invocation.return_dbus_error(BUS+'.Error.Unavailable','Sign-in is unavailable');return
            expected={k:(self.model.snapshot().get('identity') or {}).get(k)
                      for k in ('issuer','subject','account_id')} if upgrade else None
            if upgrade and any(not isinstance(v,str) or not v for v in expected.values()):
                invocation.return_dbus_error(BUS+'.Error.Unavailable','Account identity unavailable');return
            email='' if upgrade else values[0]
            def work():
                from .login import BrowserLogin
                self.login=BrowserLogin(self.config,self.model.api,self.model.tokens)
                try:
                    url=self.login.prepare(email)
                    self.GLib.idle_add(lambda:(self.Gio.AppInfo.launch_default_for_uri(url,None),False)[1])
                    self.login.finish(expected_identity=expected)
                finally:
                    self.login=None
                    # Cancellation retains the previous encrypted record and
                    # renews its normal authority, without a sign-out/reset.
                    self.model.refresh()
        else:
            invocation.return_dbus_error(BUS+'.Error.InvalidMethod','Invalid method');return
        if not self._schedule(work):
            invocation.return_dbus_error(BUS+'.Error.Busy','Connect is busy');return
        invocation.return_value(self.GLib.Variant('()',()))

    def close(self):
        if self.closed: return
        self.closed=True
        if hasattr(self, "phone_app_broker"):self.phone_app_broker.close()
        if hasattr(self, "connect_ui_broker"):self.connect_ui_broker.close()
        if self.relay_runtime:self.relay_runtime.close()
        self.relay_worker.shutdown(wait=False,cancel_futures=True)
        self.devices.stop();self.calls.stop()
        if self.calls.relay:self.calls.relay.close()
        self.call_worker.shutdown(wait=True,cancel_futures=True)
        if self.login: self.login.deadline=0
        self.recovery.close()
        if self.lease_timer is not None:self.GLib.source_remove(self.lease_timer)
        if self.route_check:self.route_check.cancel()
        if self.network_monitor and self.network_handler:self.network_monitor.disconnect(self.network_handler)
        if self.system_bus and self.sleep_subscription:self.system_bus.signal_unsubscribe(self.sleep_subscription)
        if self.companion_network_timer is not None:self.GLib.source_remove(self.companion_network_timer)
        self.companion_worker.shutdown(wait=True,cancel_futures=True)
        for owner in ('input_streams','file_streams'):
            if self.companion_effects.get(owner):
                try:self.companion_effects[owner].close_all()
                except Exception:pass
        self.companion.close()
        for owner in ('dnd','approval'):
            if self.companion_effects.get(owner):
                try:self.companion_effects[owner].close()
                except Exception:pass
        self.companion_files.shutdown(wait=True,cancel_futures=True)
        effects=self.companion_effects
        effects['mpris'].close()
        if effects['injector']:effects['injector'].close();effects['injector'].screensaver.close()
        if effects['ringer']:effects['ringer'].stop()
        if effects['notifier']:effects['notifier'].service.close()
        if effects['clipboard']:effects['clipboard'].caller.close()
        self.connection.unregister_object(self.registration)
        self.connection.signal_unsubscribe(self.secret_subscription)
        self.worker.shutdown(wait=True,cancel_futures=True)


def main(argv=None):
    import sys
    import gi
    gi.require_version('Gio','2.0')
    from gi.repository import Gio,GLib
    from . import agent as background
    argv=sys.argv if argv is None else argv
    as_agent=background.agent_requested(argv)
    if as_agent:
        # ADR-033: luma-background starts the agent; the autostart entry is
        # only the fallback for a session without it.
        try:
            if background.autostart_steps_aside(argv,Gio.bus_get_sync(Gio.BusType.SESSION,None)):return 0
        except GLib.Error:pass
    config=load_config()
    api=tokens=None
    if config:
        api=AccountAPI(config)
        try: tokens=SecretTokens(config)
        except Exception: pass  # signed-out/unavailable, never a plaintext fallback
    model=AccountModel(api,tokens)
    loop=GLib.MainLoop();holder=[]
    def acquired(connection,_name):
        daemon=Daemon(connection,model,config);holder.append(daemon)
        if as_agent:
            daemon.agent=background.ConnectAgent(daemon,connection);daemon.agent.start()
    def lost(*_): loop.quit()
    # The agent takes Connect1 over from a daemon D-Bus activated for the app;
    # that one allows it and exits when the name goes.
    flags=(Gio.BusNameOwnerFlags.REPLACE|Gio.BusNameOwnerFlags.DO_NOT_QUEUE if as_agent
           else Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT|Gio.BusNameOwnerFlags.DO_NOT_QUEUE)
    ownership=Gio.bus_own_name(Gio.BusType.SESSION,BUS,flags,acquired,None,lost)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,signal.SIGTERM,lambda:(loop.quit(),False)[1])
    try: loop.run()
    finally:
        for daemon in holder:
            if daemon.agent is not None:daemon.agent.close()
            daemon.close()
        Gio.bus_unown_name(ownership)
        if api:
            if wait_event_owners(holder):api.close()
            else:
                # A stalled owner must retain its descriptors until process
                # exit; closing the pool here would recreate the TLS race.
                logging.getLogger(__name__).warning('HTTP client retained for unfinished event owner at shutdown')
    return 0


if __name__=='__main__': raise SystemExit(main())
