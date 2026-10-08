"""Daemon-owned Calls rendezvous, pinned channels, and paired lease lifetime.

Existing registration and native pairing remain authoritative. Calls sharing
intent is separate from Messages; an audio offer requires per-call preparation
provided by the audio owner. All registry mutations run on the main context.
"""
from dataclasses import asdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import random
from .account import AccountError
from .call_relay_api import CallRelayAPI
from .call_relay_directory import CallRelayDirectory
from .call_relay_channel import RelayJSONChannel
from .call_control_channel import CallControlChannel
from .call_lease_owner import CallLeaseOwner
from .sharing_intent import SharingIntent
from .call_retry import CallRetry


class CallRuntime:
    def __init__(self,directory,bindings,api,*,bearer,account,permission,selected,
                 adapter_factory,changed,incoming,dispatch,later,cancel,
                 audio_allowed=lambda _:False,audio_factory=lambda *args:None):
        if not bindings or len({(b.account,b.device_id) for b in bindings})!=1:
            raise ValueError('one registered Calls identity required')
        if len({b.pair_id for b in bindings})!=len(bindings):raise ValueError('duplicate Calls pair')
        self.directory=Path(directory);self.bindings={b.pair_id:b for b in bindings}
        self.account,self.permission,self.selected=account,permission,selected
        self.bound_account=bindings[0].account
        self.adapter_factory,self.changed,self.incoming=adapter_factory,changed,incoming
        self.dispatch,self.later,self.cancel=dispatch,later,cancel
        from .call_audio_owner import CallAudioOwner
        self.audio=CallAudioOwner(authorized=lambda binding:self.current() and self.permission(
            binding.peer,'calls.audio',incoming=binding.role=='receiver',account=binding.account,epoch=binding.epoch),
            dispatch=dispatch,later=later,cancel=cancel)
        self.audio_allowed=self.audio.allowed
        self.audio_factory=lambda session,binding,allowed,factory:self.audio.factory(
            self.directory,session,binding,allowed,factory,lambda:self.owner.end(session.session_id),self.changed)
        self.intent=SharingIntent(directory,bindings,service='calls');self.sharing=self.intent.load()
        self.enabled=threading.Event();self.stopped=threading.Event();self.wake=threading.Event()
        self.generation=0;self.thread=None;self.ready=set();self.starting=set()
        self.owner_thread=threading.get_ident()
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='connect-call-start')
        self.api=CallRelayAPI(api,bindings[0].device_id,bearer=bearer,authorized=self.current)
        self.state=CallRelayDirectory(bindings[0].device_id,self.bindings,api.config.api_origin)
        self.owner=self._owner()
        self.retry_policy=CallRetry(directory,bindings)
        self.retry_timers={}
        self.recovery_lock=threading.Lock();self.recovery_requests={}

    def current(self):
        return self.enabled.is_set() and not self.stopped.is_set() and self.account()==self.bound_account

    def _owner(self):
        return CallLeaseOwner(self.api,self.state,authorized=self._allowed,factory=self._channel,
            dispatch=self.dispatch,later=self.later,cancel=self.cancel)

    def _allowed(self,session):
        binding=self.bindings.get(session.pair_id)
        if not self.current() or binding is None:return False
        if not self.permission(binding.peer,'calls.read',incoming=binding.role=='receiver',
                               account=binding.account,epoch=binding.epoch):return False
        if binding.role=='receiver':
            if binding.peer not in self.sharing:return False
        else:
            selected=self.selected()
            if (selected is None or selected.carrier!='relay' or
                    (selected.peer,selected.account,selected.epoch)!=(binding.peer,binding.account,binding.epoch)):return False
        return session.purpose=='call-control' or self.audio_allowed(session)

    def _channel(self,session,allowed):
        binding=self.bindings[session.pair_id];generation=self.generation
        def lost():
            if generation!=self.generation:return False
            self.ready.discard(session.session_id);self.owner.end(session.session_id);self.changed();return False
        def ready():
            def publish():
                if generation==self.generation and allowed():
                    self.ready.add(session.session_id)
                    if session.purpose=='call-control' and binding.role=='requester':
                        self.retry_policy.authenticated(binding.pair_id)
                    self.changed()
                return False
            self.dispatch(publish)
        def factory(**callbacks):
            return RelayJSONChannel(self.directory,binding.peer,server=binding.role=='receiver',
                websocket_factory=lambda:self.api.open_async(session),ready=ready,**callbacks)
        if session.purpose=='audio-signaling':return self.audio_factory(session,binding,allowed,factory)
        adapter=cleanup=None
        if binding.role=='receiver':
            def hint(*_):
                active=self.owner.active.get(session.session_id)
                if active and active.channel and allowed():
                    try:active.channel.native_changed()
                    except Exception:lost()
            adapter,cleanup=self.adapter_factory(binding,allowed,hint)
            adapter.audio_prepare=lambda call,generation,attempt:self.audio.prepare(
                binding,generation,attempt,native_call=call.call_id)
        try:
            channel=CallControlChannel(None,self.directory,binding.peer,account=binding.account,
                epoch=binding.epoch,session=session.session_id.replace('-',''),incoming=binding.role=='receiver',
                authorized=allowed,dispatch=self.dispatch,changed=lambda ringing:self.incoming() if ringing else self.changed(),
                audio_changed=lambda generations:self.audio.reconcile(binding.pair_id,generations),
                failed=lost,adapter=adapter,channel_factory=factory)
        except Exception:
            if cleanup:cleanup()
            raise
        close=channel.close
        def close_owned():
            close()
            if cleanup:cleanup()
            self.ready.discard(session.session_id);self.changed()
        channel.close=close_owned
        return channel

    def retry_state(self,peer):
        binding=next((b for b in self.bindings.values() if b.peer==peer),None)
        if binding is None:return dict(reason='unavailable',retry_at=None)
        if self.connected(peer):return dict(reason='connected',retry_at=None)
        if binding.role!='requester':return dict(reason='waiting_for_computer',retry_at=None)
        result=self.retry_policy.state(binding.pair_id)
        if result['reason']=='available':result['reason']='connecting'
        return result

    def retry(self,peer):
        binding=next((b for b in self.bindings.values() if b.peer==peer and b.role=='requester'),None)
        generation=self.generation
        def valid():
            selected=self.selected()
            return (binding is not None and generation==self.generation and self.current()
                and selected is not None and selected.carrier=='relay'
                and (selected.peer,selected.account,selected.epoch)==(binding.peer,binding.account,binding.epoch)
                and self.permission(peer,'calls.read',account=binding.account,epoch=binding.epoch))
        if not valid():raise PermissionError('current selected phone required')
        status=self.api.admission_status(binding.pair_id)
        if not valid():raise PermissionError('call recovery cancelled')
        grant=status['recovery']
        if status['normal_capacity_available'] is True:
            self.retry_policy.capacity_restored(binding.pair_id,authorized=valid)
            grant=None  # Prefer ordinary bounded admission; do not spend an exception.
        if grant and 'call-control' in grant['purposes']:
            if not self.retry_policy.reserve_recovery(binding.pair_id,grant,'call-control'):
                raise PermissionError('call recovery is not available yet')
            with self.recovery_lock:
                if not valid():raise PermissionError('call recovery cancelled')
                self.recovery_requests[binding.pair_id]=(generation,grant['recovery_id'],grant['expires'])
        else:self.retry_policy.retry(binding.pair_id)
        self.dispatch(lambda:(self.reconcile(),False)[1] if valid() else False)

    def _cancel_retry(self,pair):
        timer=self.retry_timers.pop(pair,None)
        if timer is not None:self.cancel(timer[1])

    def _schedule_retry(self,pair):
        self._cancel_retry(pair)
        state=self.retry_policy.state(pair)
        if state['retry_at'] is None:return
        generation=self.generation;token=object()
        def retry():
            current=self.retry_timers.get(pair)
            if current is None or current[0] is not token:return False
            self.retry_timers.pop(pair,None)
            if generation==self.generation and self.current():self.reconcile()
            return False
        self.retry_timers[pair]=(token,self.later(max(.001,state['retry_at']-self.retry_policy.now()),retry))

    def connected(self,peer):
        return any(sid in self.ready and self._allowed(active.session) and
            self.bindings[active.session.pair_id].peer==peer and active.session.purpose=='call-control'
            for sid,active in list(self.owner.active.items()))

    def exchange(self,peer,request):
        for sid,active in list(self.owner.active.items()):
            if (sid in self.ready and active.session.purpose=='call-control' and
                    self.bindings[active.session.pair_id].peer==peer and self._allowed(active.session)):
                return active.channel.request(request)
        raise PermissionError('call phone unavailable')

    def start_audio(self,generation):
        """Worker entry for one explicit UI action; never dials or answers."""
        import secrets,time
        selected=self.selected()
        binding=next((b for b in self.bindings.values() if selected and b.peer==selected.peer and b.role=='requester'),None)
        if not binding or not self.connected(binding.peer):raise PermissionError('call phone unavailable')
        def request(capability,payload):
            return self.exchange(binding.peer,dict(version=1,account=binding.account,epoch=binding.epoch,
                id=secrets.token_hex(16),expires=int(time.time())+10,capability=capability,payload=payload))
        def control_session():
            return next((sid for sid,active in list(self.owner.active.items()) if sid in self.ready
                and active.session.pair_id==binding.pair_id and active.session.purpose=='call-control'
                and self._allowed(active.session)),None)
        control=control_session()
        def active_call():
            if (control is None or control_session()!=control or not self.current()
                    or not self.permission(binding.peer,'calls.audio',account=binding.account,epoch=binding.epoch)):
                raise PermissionError('call audio consent changed')
            snapshot=request('calls.read',{'operation':'snapshot'})
            rows=snapshot.get('result',{}).get('calls',[])
            row=next((row for row in rows if row.get('generation')==generation and row.get('phase')=='active'),None)
            if row is None or control_session()!=control:raise PermissionError('active call changed')
            return row
        row=active_call()
        def recover_audio():
            nonlocal row
            row=active_call()
            status=self.api.admission_status(binding.pair_id)
            row=active_call()
            if status['normal_capacity_available'] is True:
                self.retry_policy.capacity_restored(binding.pair_id,authorized=lambda:self.current()
                    and control_session()==control and self.permission(binding.peer,'calls.audio',account=binding.account,epoch=binding.epoch))
                if self.retry_policy.cooldowns(binding.pair_id)['server_until']>self.retry_policy.now():
                    raise PermissionError('call service cooldown')
                row=active_call()
                return self.api.mint(binding.pair_id,'audio-signaling')
            grant=status['recovery']
            if not grant or not self.retry_policy.reserve_recovery(binding.pair_id,grant,'audio-signaling'):
                raise PermissionError('call audio recovery unavailable')
            row=active_call()
            if grant['expires']<=self.retry_policy.now():raise PermissionError('call recovery expired')
            return self.api.mint(binding.pair_id,'audio-signaling',grant['recovery_id'])
        cooldowns=self.retry_policy.cooldowns(binding.pair_id)
        if cooldowns['server_until']>self.retry_policy.now():raise PermissionError('call service cooldown')
        if cooldowns['capacity_until']>self.retry_policy.now():attempt=recover_audio()
        else:
            try:attempt=self.api.mint(binding.pair_id,'audio-signaling')
            except AccountError as error:
                if error.status==429:self.retry_policy.rate_limited(binding.pair_id,error.retry_after,error.code)
                if error.status!=429 or error.code!='call_attempt_limit':raise
                attempt=recover_audio()
        self.audio.prepare(binding,generation,attempt['attempt_id'])
        try:
            result=request('calls.audio',dict(operation='prepare',call=row['id'],generation=generation,
                attempt_id=attempt['attempt_id']))
            if result!={'state':'complete','result':{'prepared':True}}:raise PermissionError('audio preparation unconfirmed')
            if not self.current():raise PermissionError('call account changed')
            session=self.api.ensure_call(binding.pair_id,attempt['attempt_id'],'audio-signaling')
            if not self.audio.allowed(session):raise PermissionError('call audio consent changed')
        except Exception:
            self.audio.invalidate(binding.pair_id);raise

    def devices(self):
        return [dict(peer=b.peer,role=b.role,sharing=b.peer in self.sharing,
            selected=bool(self.selected() and self.selected().peer==b.peer),connected=self.connected(b.peer),connection=self.retry_state(b.peer),permissions={cap:self.permission(b.peer,cap,
                incoming=b.role=='receiver',account=b.account,epoch=b.epoch)
                for cap in ('calls.read','calls.control','calls.audio')}) for b in self.bindings.values()]

    def audio_state(self):
        with self.audio.lock:
            value=next((v for v in self.audio.prepared.values() if not v.closed),None)
            audio=value.audio if value else None
            return dict(status='connected' if audio and audio.connected and audio.ready else 'connecting' if value else 'phone',
                muted=bool(audio and audio.muted))

    def mute_audio(self,muted):
        with self.audio.lock:
            value=next((v for v in self.audio.prepared.values() if not v.closed and v.native_call is None),None)
            if not value or not value.audio:raise PermissionError('desktop call audio unavailable')
            value.audio.set_muted(muted)

    def stop_audio(self):
        self.audio.invalidate();self.owner.authority_changed();self.changed()

    def accepts_sharing(self,peer):
        return any(b.peer==peer and b.role=='receiver' for b in self.bindings.values())

    def enable_sharing(self,peer):
        if threading.get_ident()!=self.owner_thread:raise RuntimeError('Calls intent belongs to main context')
        binding=next((b for b in self.bindings.values() if b.peer==peer and b.role=='receiver'),None)
        if not binding or not self.current() or not self.permission(peer,'calls.read',incoming=True,
                account=binding.account,epoch=binding.epoch):raise PermissionError('call sharing consent required')
        selected=self.sharing|{peer};self.intent.save(selected);self.sharing=selected
        self.reconcile()

    def stop_sharing(self):
        if threading.get_ident()!=self.owner_thread:raise RuntimeError('Calls intent belongs to main context')
        self.intent.save(set());self.sharing.clear();self.owner.authority_changed();self.changed()

    def reconcile(self):
        if not self.current():return
        self.owner.authority_changed()
        # Reconcile existing metadata without inventing a server cursor.
        if self.state.cursor:
            self.owner.apply('snapshot',self.state.cursor,dict(device_id=self.api.device_id,reset=False,
                sessions=[dict(asdict(s),generation=s.session_id,end_to_end_tls_required=True) for s in self.state.sessions.values()]))
        selected=self.selected()
        if selected is None or selected.carrier!='relay':return
        binding=next((b for b in self.bindings.values() if b.peer==selected.peer and b.role=='requester'),None)
        if binding is None or binding.pair_id in self.starting:return
        if (binding.pair_id,'call-control') in self.state.sessions:
            self._cancel_retry(binding.pair_id);return
        if not self.permission(binding.peer,'calls.read',account=binding.account,epoch=binding.epoch):return
        with self.recovery_lock:recovery=self.recovery_requests.pop(binding.pair_id,None)
        recovery_id=None
        if recovery:
            reserved_generation,recovery_id,expires=recovery
            if reserved_generation!=self.generation or expires<=self.retry_policy.now():return
        if recovery_id is None and not self.retry_policy.reserve(binding.pair_id):
            self._schedule_retry(binding.pair_id);return
        self._cancel_retry(binding.pair_id)
        self.starting.add(binding.pair_id);generation=self.generation
        def work():
            session=None;error=None
            try:
                if generation!=self.generation or not self.current():return
                selected_now=self.selected()
                if not selected_now or (selected_now.peer,selected_now.account,selected_now.epoch)!=(binding.peer,binding.account,binding.epoch):return
                if not self.permission(binding.peer,'calls.read',account=binding.account,epoch=binding.epoch):return
                if recovery_id and expires<=self.retry_policy.now():return
                attempt=self.api.mint(binding.pair_id,'call-control',recovery_id) if recovery_id else self.api.mint(binding.pair_id,'call-control')
                if generation!=self.generation or not self.current():return
                session=self.api.ensure_call(binding.pair_id,attempt['attempt_id'],'call-control')
                if generation!=self.generation and self.current():
                    try:self.api.close_call(session)
                    except Exception:pass
            except Exception as failure:
                error=failure
                if isinstance(error,AccountError) and error.status==429:
                    # Persist before returning to a main context that may close;
                    # the retry ledger serializes with a reconstructed runtime.
                    self.retry_policy.rate_limited(binding.pair_id,error.retry_after,error.code)
            finally:
                def publish():
                    if self.stopped.is_set():return False
                    if generation!=self.generation:return False
                    self.starting.discard(binding.pair_id)
                    if session is None:self._schedule_retry(binding.pair_id)
                    # SSE owns cursor and acceptance; ensure response never
                    # fabricates an event or bypasses the fresh snapshot.
                    self.changed();return False
                self.dispatch(publish)
        self.executor.submit(work)

    def resume(self):
        if self.stopped.is_set() or self.account()!=self.bound_account:return
        self.enabled.set();self.wake.set()
        if self.thread is None:
            self.thread=threading.Thread(target=self._events,name='connect-call-events',daemon=True);self.thread.start()
        self.reconcile()

    def refreshed(self):
        if not self.current():self.pause();return
        self.owner.account_refreshed();self.api.cancel_events();self.wake.set()

    def pause(self):
        self.generation+=1;self.enabled.clear();self.api.cancel_events();self.wake.set();self.audio.invalidate()
        for pair in list(self.retry_timers):self._cancel_retry(pair)
        with self.recovery_lock:self.recovery_requests.clear()
        self.owner.close();self.ready.clear();self.starting.clear();self.state.sessions.clear();self.state.cursor=None
        self.owner=self._owner();self.changed()

    def close(self):
        self.stopped.set();self.pause();self.owner.close();self.enabled.set();self.wake.set()
        self.executor.shutdown(wait=False,cancel_futures=True)

    def _events(self):
        failures=0
        while not self.stopped.is_set():
            self.enabled.wait()
            if self.stopped.is_set():return
            generation=self.generation;delay=None
            try:
                for event,cursor,data in self.api.events(self.state.cursor):
                    if generation!=self.generation or not self.current():break
                    def apply(event=event,cursor=cursor,data=data,generation=generation):
                        if generation==self.generation and self.current():
                            try:self.owner.apply(event,cursor,data);self.reconcile()
                            except Exception:self.pause()
                        return False
                    self.dispatch(apply);failures=0
            except AccountError as error:delay=error.retry_after
            except Exception:pass
            def disconnected(generation=generation):
                if generation==self.generation:self.owner.events_disconnected()
                return False
            self.dispatch(disconnected)
            if self.stopped.is_set():return
            failures=min(failures+1,6)
            self.wake.wait(delay if delay is not None else min(60,2**failures)*random.uniform(.8,1))
            self.wake.clear()
