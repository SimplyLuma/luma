# SPDX-License-Identifier: Apache-2.0
"""Signed Phone's bounded metadata and companion adapter, owned by Connect."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,secrets,sqlite3,threading,time
from pathlib import Path

METHODS=frozenset({'GetPhoneContext','GetPhoneHistory','GetPhoneTogether','PhoneCompanionRequest','ClosePhoneCompanion'})

class PhoneAppBroker:
    def __init__(self, daemon, *, authenticate=None):
        if authenticate is None:
            from luma_installer.app_data_broker import authenticate
        self.authenticate=authenticate;self.daemon=daemon
        self.worker=ThreadPoolExecutor(max_workers=2,thread_name_prefix='connect-phone-app')
        self.slots=threading.BoundedSemaphore(2);self.lock=threading.RLock()
        self.companion=None;self.binding=None;self.dial=None;self.references={};self.closed=False;self.seen={};self.senders={}

    def _selection(self):
        # Companion setup persists through the same private selection owner.
        # Refresh it before any context/control; absence is an explicit local choice.
        with self.daemon.calls.lock:
            if self.daemon.calls.path.exists():self.daemon.calls._load()
            else:self.daemon.calls.selection=None
            return self.daemon.calls.selection

    def context(self):
        selected=self._selection()
        if selected is None:return {'selected':False}
        return {'selected':True,'peer':selected.peer,'epoch':selected.epoch,'account':selected.account,
                'label':selected.label,'carrier':selected.carrier}

    def history(self):
        from prairie_apps.connect_messages import cloud_history_path
        path=cloud_history_path()
        if not path.exists():return {'calls':[]}
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
        deadline=time.monotonic()+1
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            rows=db.execute('SELECT id,device_id,direction,started_at,duration_s,answered,number,contact_uid FROM calls WHERE deleted=0 ORDER BY started_at DESC,id LIMIT 512').fetchall()
        finally:db.close()
        return {'calls':[dict(zip(('id','device_id','direction','started_at','duration_s','answered','number','contact_uid'),row))for row in rows]}

    def together(self, number):
        from prairie_apps.phone_fixture import digits
        if not isinstance(number,str) or len(number)>128:raise ValueError('Bounded phone number required.')
        number=digits(number)
        if not number or len(number)>32:raise ValueError('Phone number required.')
        path=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')/'prairie/messages/messages.db'
        if not path.exists():return {'message':None}
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
        deadline=time.monotonic()+1
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        db.create_function('phone_digits',1,digits,deterministic=True)
        try:
            row=db.execute('SELECT substr(body,1,45),timestamp,direction FROM messages WHERE phone_digits(address)=? ORDER BY timestamp DESC LIMIT 1',(number,)).fetchone()
        finally:db.close()
        return {'message':None if row is None else {'body':row[0],'timestamp':row[1],'direction':row[2]}}

    def _provider(self):
        selected=self._selection()
        if selected is None or selected.carrier!='companion':raise PermissionError('Companion selection required.')
        binding=(selected.peer,selected.epoch)
        if binding!=self.binding:
            self._close_companion()
            from .call_provider import _companion_provider
            self.companion=_companion_provider(selected,lambda cb:self.daemon.GLib.idle_add(cb))
            self.binding=binding
            self.companion.start(lambda *_:self.daemon.connection.emit_signal(None,
                '/org/projectluma/Connect','org.projectluma.Connect1','CallsChanged',None))
        if not self.companion.authorized():raise PermissionError('Companion authorization unavailable.')
        return self.companion,selected

    def _close_companion(self):
        if self.companion:self.companion.close()
        self.companion=None;self.binding=None;self.dial=None;self.references={}

    def companion_request(self, request):
        if not isinstance(request,dict) or set(request)!={'version','account','epoch','id','capability','expires','payload'}:
            raise ValueError('Typed companion request required.')
        from .policy import IDENTIFIER
        if (type(request['version']) is not int or request['version']!=1 or request['account'] is not None or
                not isinstance(request['id'],str) or not IDENTIFIER.fullmatch(request['id']) or
                type(request['expires']) is not int or not time.time()<=request['expires']<=time.time()+15):
            raise ValueError('Current companion request required.')
        self.seen={key:value for key,value in self.seen.items() if value>=time.time()}
        if request["id"] in self.seen or len(self.seen)>=128:raise PermissionError("Companion request already used.")
        self.seen[request["id"]]=request["expires"]
        provider,selected=self._provider()
        if request['epoch']!=selected.epoch:raise PermissionError('Selected companion changed.')
        payload=request['payload']
        if not isinstance(payload,dict):raise ValueError('Typed companion operation required.')
        operation=payload.get('operation')
        if request['capability']=='calls.read' and payload=={'operation':'snapshot'}:
            calls=provider.calls()
            if len(calls)>8:raise ValueError('Call count exceeds its bound.')
            self.references={hashlib.sha256((selected.epoch+'|'+c.call_id).encode()).hexdigest()[:32]:c.call_id for c in calls}
            rows=[]
            for c in calls:
                identifier=hashlib.sha256((selected.epoch+'|'+c.call_id).encode()).hexdigest()[:32]
                rows.append(dict(id=identifier,generation=identifier,address=c.address,direction=c.direction,
                    phase=c.phase.value,started_at=c.started_at,answered_at=c.answered_at))
            token=secrets.token_hex(16) if provider.control_authorized() and not calls else None
            self.dial=(token,time.monotonic()+10) if token else None
            result=dict(calls=rows,dial_token=token,voice_available=bool(provider.control_authorized()))
        elif request['capability']=='calls.control' and operation=='dial' and set(payload)=={'operation','token','address'}:
            token=self.dial;self.dial=None
            if not token or token[0]!=payload['token'] or time.monotonic()>token[1]:raise PermissionError('Dial state changed.')
            provider.dial(payload['address']);result={'accepted':True}
        elif request['capability']=='calls.control' and operation in ('answer','decline','hangup') and set(payload)=={'operation','call'}:
            actual=self.references.get(payload['call'])
            if actual is None:raise PermissionError('Call state changed.')
            getattr(provider,{'answer':'accept','decline':'decline','hangup':'hangup'}[operation])(actual);result={'accepted':True}
        elif request['capability']=='calls.control' and operation in ('audio-start','audio-stop','mute'):
            if operation=='audio-start' and set(payload)=={'operation','call'}:
                actual=self.references.get(payload['call'])
                if actual is None:raise PermissionError('Call state changed.')
                provider.start_audio(actual)
            elif operation=='audio-stop' and set(payload)=={'operation'}:provider.stop_audio()
            elif operation=='mute' and set(payload)=={'operation','muted'} and type(payload['muted']) is bool:provider.mute_audio(payload['muted'])
            else:raise ValueError('Typed audio operation required.')
            result={'accepted':True}
        elif request['capability']=='calls.read' and payload=={'operation':'audio-state'}:result=provider.audio_state()
        else:raise ValueError('Unsupported companion operation.')
        if not provider.authorized() or self._selection()!=selected:raise PermissionError('Companion changed.')
        return {'state':'complete','result':result}

    def _forget_sender(self, connection, sender):
        with self.lock:
            subscription=self.senders.pop(sender,None)
            if subscription is not None:connection.signal_unsubscribe(subscription)
            if not self.senders:self._close_companion()

    def _watch_sender(self, connection, sender):
        if sender in self.senders:return
        if len(self.senders)>=16:raise PermissionError('Phone client count exceeds its bound.')
        from gi.repository import Gio
        def changed(_connection,_sender,_path,_interface,_signal,parameters):
            name,_old,new=parameters.unpack()
            if name==sender and not new:
                self.worker.submit(self._forget_sender,connection,sender)
        self.senders[sender]=connection.signal_subscribe('org.freedesktop.DBus',
            'org.freedesktop.DBus','NameOwnerChanged','/org/freedesktop/DBus',sender,
            Gio.DBusSignalFlags.NONE,changed)

    def dispatch(self,connection,sender,method,values,invocation,GLib):
        try:
            if self.closed or self.authenticate(connection,sender)!='org.projectluma.Phone':raise PermissionError()
            if method not in METHODS or len(values)!=(1 if method in ('GetPhoneTogether','PhoneCompanionRequest') else 0):raise ValueError()
            if values and (not isinstance(values[0],str) or len(values[0].encode())>8192):raise ValueError()
            if method=='PhoneCompanionRequest':
                with self.lock:self._watch_sender(connection,sender)
            if not self.slots.acquire(blocking=False):raise RuntimeError()
        except Exception:
            invocation.return_dbus_error('org.projectluma.Connect1.Phone.Refused','Phone access is unavailable.');return
        def work():
            try:
                if self.closed or self.authenticate(connection,sender)!='org.projectluma.Phone':raise PermissionError()
                with self.lock:
                    if method=='GetPhoneContext':result=self.context()
                    elif method=='GetPhoneHistory':result=self.history()
                    elif method=='GetPhoneTogether':result=self.together(values[0])
                    elif method=='PhoneCompanionRequest':result=self.companion_request(json.loads(values[0]))
                    else:self._forget_sender(connection,sender);result={'closed':True}
                text=json.dumps(result,allow_nan=False)
                if len(text.encode())>256*1024:raise ValueError()
                GLib.idle_add(lambda:(invocation.return_value(GLib.Variant('(s)',(text,))),False)[1])
            except Exception:
                GLib.idle_add(lambda:(invocation.return_dbus_error('org.projectluma.Connect1.Phone.Unavailable','Phone information is unavailable.'),False)[1])
            finally:self.slots.release()
        self.worker.submit(work)

    def close(self):
        self.closed=True
        self.worker.shutdown(wait=True,cancel_futures=True)
        with self.lock:
            for subscription in self.senders.values():self.daemon.connection.signal_unsubscribe(subscription)
            self.senders.clear();self._close_companion()
