"""Shared Phone adapter for ephemeral approved paired call control.

An owning authenticated session supplies exchange and change notifications.
No durable queue, offline command retention, automatic redial or audio capture.
"""
from concurrent.futures import ThreadPoolExecutor
import secrets
import logging
import threading
import time


class CallProvider:
    def __init__(self, exchange, *, account, epoch, authorized, control_authorized,
                 subscribe, dispatch, now=time.time):
        self.exchange,self.account,self.epoch=exchange,account,epoch
        self.authorized,self.control_authorized=authorized,control_authorized
        self.subscribe,self.dispatch,self.now=subscribe,dispatch,now
        self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='connect-call-state')
        self.lock=threading.RLock();self.command=threading.Lock()
        self.closed=False;self.generation=0;self.refreshing=False;self.dirty=False
        self.snapshot={'calls':[],'dial_token':None};self.listener=lambda *_:None
        self.disconnect=lambda:None
        self.retry_timer=None;self.retry_delay=1

    def _request(self, capability, payload):
        if self.closed or not self.authorized():raise PermissionError('phone connection unavailable')
        if capability=='calls.control' and not self.control_authorized():raise PermissionError('call control not approved')
        response=self.exchange(dict(version=1,account=self.account,epoch=self.epoch,id=secrets.token_hex(16),
            capability=capability,expires=int(self.now())+10,payload=payload))
        if self.closed or not self.authorized():raise PermissionError('phone connection unavailable')
        if capability=='calls.control' and not self.control_authorized():raise PermissionError('call control revoked')
        if not isinstance(response,dict) or response.get('state')!='complete' or not isinstance(response.get('result'),dict):
            raise PermissionError('call request not confirmed')
        return response['result']

    def _snapshot(self):
        from .phone_contract import IDENTIFIER
        result=self._request('calls.read',{'operation':'snapshot'})
        if set(result) not in ({'calls','dial_token'},{'calls','dial_token','voice_available'}) or not isinstance(result['calls'],list) or len(result['calls'])>8:
            raise ValueError('invalid call state')
        if 'voice_available' in result and type(result['voice_available']) is not bool:
            raise ValueError('invalid phone voice availability')
        result=dict(result,voice_available=result.get('voice_available',False))
        token=result['dial_token']
        if token is not None and (not isinstance(token,str) or not IDENTIFIER.fullmatch(token)):
            raise ValueError('invalid dial token')
        seen=set()
        for row in result['calls']:
            if (not isinstance(row,dict) or set(row)!={'id','generation','address','direction','phase','started_at','answered_at'}
                    or any(not isinstance(row[k],str) or not IDENTIFIER.fullmatch(row[k]) for k in ('id','generation'))
                    or row['generation'] in seen or not isinstance(row['address'],str) or len(row['address'])>128
                    or row['direction'] not in {'incoming','outgoing'}
                    or row['phase'] not in {'idle','preparing','dialling','ringing-outgoing','incoming','connecting','active','held','waiting','multi-call','ending','ended','failed'}
                    or any(type(row[k]) is not int or row[k]<0 for k in ('started_at','answered_at'))):
                raise ValueError('invalid call state')
            seen.add(row['generation'])
        return result

    def start(self, listener):
        self.listener=listener
        self.disconnect=self.subscribe(self.invalidate)
        self.invalidate()

    def invalidate(self, *_args):
        with self.lock:
            if self.closed:return
            if self.retry_timer:
                self.retry_timer.cancel();self.retry_timer=None
            self.generation+=1;self.dirty=True
            if self.refreshing:return
            self.refreshing=True
        self.worker.submit(self._refresh)

    def _refresh(self):
        with self.lock:generation=self.generation;self.dirty=False
        result=None
        try:
            with self.command:result=self._snapshot()
        except Exception as error:
            logging.getLogger(__name__).warning("Call snapshot failed (%s)",type(error).__name__)
        def publish():
            with self.lock:
                self.refreshing=False
                if self.closed:return False
                if generation!=self.generation:
                    self.invalidate();return False
                self.snapshot=result or {'calls':[],'dial_token':None}
                if result is None:
                    # Recover a missed notification or transient RPC failure.
                    # Only read state; never replay a control or audio request.
                    self.retry_timer=threading.Timer(self.retry_delay,self.invalidate)
                    self.retry_timer.daemon=True;self.retry_timer.start()
                    self.retry_delay=min(self.retry_delay*2,30)
                else:self.retry_delay=1
            self.listener(self.snapshot,result is not None)
            return False
        self.dispatch(publish)

    def calls(self):
        from prairie_apps.phone_backend import NativeCall,CallPhase
        return tuple(NativeCall(row['generation'],row['address'],row['direction'],CallPhase(row['phase']),
            started_at=row['started_at'],answered_at=row['answered_at']) for row in self.snapshot['calls'])

    def _control(self, operation, call_id=None, address=None):
        if not self.command.acquire(blocking=False):raise PermissionError('call state is changing')
        try:
            # Obtain a fresh one-shot token for this explicit user action.
            # A changed call generation/phase is never silently retargeted.
            snapshot=self._snapshot()
            if operation=='dial':
                if not snapshot['voice_available']:raise PermissionError('Your phone cannot make calls right now.')
                if not snapshot['dial_token']:raise PermissionError('phone already has a call')
                payload={'operation':'dial','token':snapshot['dial_token'],'address':address}
            else:
                row=next((r for r in snapshot['calls'] if r['generation']==call_id),None)
                if row is None:raise PermissionError('call has changed')
                payload={'operation':operation,'call':row['id']}
            result=self._request('calls.control',payload)
            if result=={'accepted':False,'reason':'voice_unavailable'}:
                raise PermissionError('Your phone cannot make calls right now.')
            if result!={'accepted':True}:raise PermissionError('call request not confirmed')
        finally:self.command.release()
        self.invalidate()

    def dial(self,address):self._control('dial',address=address);return ''
    def accept(self,call_id):self._control('answer',call_id)
    def decline(self,call_id):self._control('decline',call_id)
    def hangup(self,call_id):self._control('hangup',call_id)

    def close(self):
        with self.lock:
            self.closed=True;self.generation+=1
            if self.retry_timer:
                self.retry_timer.cancel();self.retry_timer=None
        self.disconnect();self.snapshot={'calls':[],'dial_token':None}
        self.worker.shutdown(wait=False,cancel_futures=True)


def _public_selection(context):
    """Validate the host's public DTO without importing endpoint/key material."""
    from types import SimpleNamespace
    from .phone_contract import DIGEST,IDENTIFIER
    if context=={'selected':False} and type(context['selected']) is bool:return None
    if (not isinstance(context,dict) or set(context)!={'selected','peer','epoch','account','label','carrier'}
            or context['selected'] is not True
            or not isinstance(context['peer'],str) or not DIGEST.fullmatch(context['peer'])
            or not isinstance(context['epoch'],str) or not IDENTIFIER.fullmatch(context['epoch'])
            or not isinstance(context['label'],str) or not 0<len(context['label'])<=128
            or context['carrier'] not in {'lan','relay','companion'}
            or (context['account'] is not None if context['carrier']=='companion' else
                not isinstance(context['account'],str) or not 0<len(context['account'])<=512)):
        raise ValueError('Invalid selected phone context.')
    return SimpleNamespace(**{k:v for k,v in context.items() if k!='selected'})


def _sandbox_companion_provider(selected,connection,dispatch):
    from gi.repository import Gio,GLib
    from .phone_contract import BUS,PATH
    from prairie_apps.phone_host import call
    import json
    def authorized():
        try:
            current=_public_selection(call('GetPhoneContext'))
            return current is not None and vars(current)==vars(selected)
        except Exception:return False
    def subscribe(changed):
        ids=[connection.signal_subscribe(BUS,BUS,'CallsChanged',PATH,None,Gio.DBusSignalFlags.NONE,lambda *_:changed()),
             connection.signal_subscribe('org.freedesktop.DBus','org.freedesktop.DBus','NameOwnerChanged',
                 '/org/freedesktop/DBus',BUS,Gio.DBusSignalFlags.NONE,lambda *_:changed())]
        return lambda:[connection.signal_unsubscribe(identifier) for identifier in ids]
    provider=CallProvider(lambda request:call('PhoneCompanionRequest',json.dumps(request)),account=None,
        epoch=selected.epoch,authorized=authorized,control_authorized=authorized,subscribe=subscribe,dispatch=dispatch)
    provider.audio_state=lambda:provider._request('calls.read',{'operation':'audio-state'})
    def audio(operation,**values):
        with provider.command:provider._request('calls.control',dict(operation=operation,**values))
        provider.invalidate()
    provider.start_audio=lambda generation:audio('audio-start',call=generation)
    provider.stop_audio=lambda:audio('audio-stop')
    provider.mute_audio=lambda muted:audio('mute',muted=muted)
    original_close=provider.close
    def close():
        original_close()
        connection.call(BUS,PATH,BUS,'ClosePhoneCompanion',None,None,Gio.DBusCallFlags.NONE,1500,None,None)
    provider.close=close
    return provider


def _selected_provider(*,dispatch):
    """Normal Phone startup uses only the daemon's explicit call selection.

    An existing selection that cannot be loaded stays unavailable; it never
    silently falls back to a local modem. No device key or bearer enters UI.
    """
    import json
    from pathlib import Path
    from gi.repository import Gio,GLib
    from .phone_contract import BUS,PATH
    import os
    sandbox=os.environ.get('FLATPAK_ID')=='org.projectluma.Phone'
    selection_path=Path.home()/'.config/luma-connect/calls-phone.json'
    if not sandbox and not selection_path.exists():return None
    connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    state={'connected':False,'control':False}
    if sandbox:
        from prairie_apps.phone_host import call
        selected=_public_selection(call('GetPhoneContext'))
        if selected is None:return None
    else:
        import os
        from .transport import _unique
        from .message_provider import SelectedPhone
        info=selection_path.lstat()
        if (selection_path.is_symlink() or info.st_uid!=os.getuid() or info.st_mode&0o077
                or selection_path.parent.stat().st_mode&0o077 or info.st_size>8192):
            raise PermissionError('private call selection required')
        document=json.loads(selection_path.read_text(),object_pairs_hook=_unique)
        if set(document)!={'version','phone'} or type(document['version']) is not int or document['version']!=1:
            raise ValueError('invalid call selection')
        selected=SelectedPhone(**document['phone'])
    try:
        result=connection.call_sync(BUS,PATH,BUS,'GetCallState',None,None,Gio.DBusCallFlags.NONE,1500,None)
        state=json.loads(result.unpack()[0])
    except Exception:pass
    if selected.carrier=='companion':
        return _sandbox_companion_provider(selected,connection,dispatch) if sandbox else _companion_provider(selected,dispatch)
    account,epoch=selected.account,selected.epoch
    def exchange(request):
        result=connection.call_sync(BUS,PATH,BUS,'ExchangeCall',GLib.Variant('(s)',(json.dumps(request),)),
                                    None,Gio.DBusCallFlags.NONE,7000,None)
        return json.loads(result.unpack()[0])
    def subscribe(changed):
        def event(*_):
            # CallsChanged is a notification, not revocation evidence. Keep
            # the verified connection through an in-flight command response.
            # Every control still obtains fresh GetCallState + native tokens;
            # the daemon independently enforces current account/permission.
            changed()
        def owner_event(*_):
            state.update(connected=False,control=False)
            changed()
        ids=[connection.signal_subscribe(BUS,BUS,'CallsChanged',PATH,None,Gio.DBusSignalFlags.NONE,event),
             connection.signal_subscribe('org.freedesktop.DBus','org.freedesktop.DBus','NameOwnerChanged',
                '/org/freedesktop/DBus',BUS,Gio.DBusSignalFlags.NONE,owner_event)]
        return lambda:[connection.signal_unsubscribe(identifier) for identifier in ids]
    provider=CallProvider(exchange,account=account,epoch=epoch,authorized=lambda:state.get('connected') is True,
        control_authorized=lambda:state.get('control') is True,subscribe=subscribe,dispatch=dispatch)
    provider.audio_state=lambda:state.get('audio',{'status':'phone','muted':False})
    def audio_call(method,parameters=None):
        with provider.command:
            connection.call_sync(BUS,PATH,BUS,method,parameters,None,Gio.DBusCallFlags.NONE,15000,None)
        provider.invalidate()
    provider.start_audio=lambda generation:audio_call('StartCallAudio',GLib.Variant('(s)',(generation,)))
    provider.stop_audio=lambda:audio_call('StopCallAudio')
    provider.mute_audio=lambda muted:audio_call('SetCallAudioMuted',GLib.Variant('(b)',(muted,)))
    original_close=provider.close
    def close():
        connection.call(BUS,PATH,BUS,'StopCallAudio',None,None,Gio.DBusCallFlags.NONE,1500,None,None)
        original_close()
    provider.close=close
    original=provider._snapshot
    def snapshot():
        result=connection.call_sync(BUS,PATH,BUS,'GetCallState',None,None,Gio.DBusCallFlags.NONE,1500,None)
        current=json.loads(result.unpack()[0])
        if current.get('account')!=account or current.get('epoch')!=epoch:
            raise PermissionError('selected phone changed; reopen Phone')
        state.update(current)
        return original()
    provider._snapshot=snapshot
    return provider


def _companion_provider(selected,dispatch):
    """Calls from a companion phone over Bluetooth hands-free (companion_calls, ADR-021).

    Authorization is the companion pairing itself: the phone must still be paired,
    and it must have bonded with this computer through `bluetooth.bond`.
    """
    from pathlib import Path
    from .companion import Registry
    from .companion_calls import BluetoothBonding, BluetoothCallProvider, PipeWireTelephony
    directory=Path.home()/'.local/share/luma-connect/device'
    bonding=BluetoothBonding(None,directory)
    telephony=PipeWireTelephony.gio()
    telephony.start()
    def authorized():
        return selected.peer in Registry(directory).active() and bonding.remembered(selected.peer) is not None
    provider=BluetoothCallProvider(telephony,address=lambda:bonding.remembered(selected.peer),authorized=authorized,
                                   dispatch=dispatch)
    original_close=provider.close
    def close():
        original_close();telephony.stop()
    provider.close=close
    return provider


def selected_provider(*,dispatch):
    try:return _selected_provider(dispatch=dispatch)
    except Exception:
        # Existing invalid selection must show unavailable, never select a
        # desktop modem or leak a configuration/transport error to the user.
        def denied(_request):raise PermissionError('selected phone unavailable')
        return CallProvider(denied,account=None,epoch=None,authorized=lambda:False,
            control_authorized=lambda:False,subscribe=lambda _callback:lambda:None,dispatch=dispatch)
