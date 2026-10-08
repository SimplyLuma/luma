import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
from luma_continuity.call_runtime import CallRuntime
from luma_continuity.relay_binding import RelayBinding
from luma_continuity.message_provider import SelectedPhone
import test_call_relay_api as fixture


class CallRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CallRelayAPITests();self.f.setUp()
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.peer='a'*64;self.epoch='e'*32
        self.binding=RelayBinding('account',self.peer,self.epoch,self.f.device,self.f.pair,'receiver')
        self.account='account';self.allowed=True;self.selected=None;self.callbacks=[];self.channels=[]
        self.runtime=CallRuntime(self.root,[self.binding],self.f.api.api,bearer=lambda:'synthetic',
            account=lambda:self.account,permission=lambda *a,**kw:self.allowed,selected=lambda:self.selected,
            adapter_factory=None,changed=lambda:None,incoming=lambda:None,dispatch=self.callbacks.append,
            later=lambda *args:object(),cancel=lambda _:None)
        self.addCleanup(self.runtime.close)
        self.runtime.enabled.set()
        def channel(session,allowed):
            self.assertTrue(allowed())
            result=SimpleNamespace(closed=False,start=lambda:None)
            result.close=lambda:setattr(result,'closed',True)
            self.channels.append(result);return result
        self.runtime.owner.factory=channel
        # Fixed clock fixtures deliberately avoid the real endpoint.
        self.runtime.state.now=lambda:1000;self.runtime.owner.now=lambda:1000

    def offer(self,purpose='call-control'):
        raw=dict(self.f.metadata,purpose=purpose,
            url='wss://api.example/v1/call-relay/'+purpose+'/'+self.f.sid)
        self.runtime.owner.apply('session_offered','first',raw)

    def test_messages_sharing_does_not_enable_calls(self):
        (self.root/'relay-sharing.json').write_text('messages intent is deliberately unrelated')
        self.offer();self.assertEqual(self.channels,[])
        self.runtime.enable_sharing(self.peer)
        self.assertEqual(len(self.channels),1)
        self.assertEqual((self.root/'relay-sharing.json').read_text(),'messages intent is deliberately unrelated')

    def test_audio_offer_never_opens_without_per_call_preparation(self):
        self.runtime.enable_sharing(self.peer);self.offer('audio-signaling')
        self.assertEqual(self.channels,[])

    def test_metadata_is_not_connected_until_authenticated_handshake(self):
        self.runtime.enable_sharing(self.peer);self.offer()
        self.assertFalse(self.runtime.connected(self.peer))
        self.runtime.ready.add(self.f.sid);self.assertTrue(self.runtime.connected(self.peer))
        self.account=None
        self.assertFalse(self.runtime.connected(self.peer))
        self.runtime.owner.authority_changed();self.assertTrue(self.channels[0].closed)

    def test_stop_sharing_and_pause_close_channels(self):
        self.runtime.enable_sharing(self.peer);self.offer()
        self.runtime.stop_sharing();self.assertTrue(self.channels[0].closed)
        self.runtime.pause();self.assertEqual(self.runtime.state.sessions,{})
        self.assertFalse(self.runtime.enabled.is_set())

    def test_worker_cannot_publish_start_after_main_context_stop(self):
        import threading
        self.runtime.enable_sharing(self.peer)
        self.runtime.stop_sharing()
        errors=[]
        def late_start():
            try:self.runtime.enable_sharing(self.peer)
            except RuntimeError:errors.append(True)
        thread=threading.Thread(target=late_start);thread.start();thread.join(2)
        self.assertEqual(errors,[True]);self.assertEqual(self.runtime.intent.load(),set())
        self.assertEqual(self.runtime.sharing,set())
        self.account=None
        with self.assertRaises(PermissionError):self.runtime.enable_sharing(self.peer)
        self.assertEqual(self.runtime.intent.load(),set())


    def requester(self):
        from dataclasses import replace
        self.binding=replace(self.binding,role='requester')
        self.runtime.bindings[self.f.pair]=self.binding
        self.selected=SelectedPhone(self.peer,self.epoch,'account','Phone','127.0.0.1',1,'relay')
        self.clock=1000
        from luma_continuity.call_retry import CallRetry
        self.runtime.retry_policy=CallRetry(self.root,[self.binding])
        self.runtime.retry_policy.now=lambda:self.clock
        self.runtime.retry_policy.jitter=lambda a,b:1
        self.runtime.api=self.f.api
        self.runtime.api.admission=lambda pair:None
        self.runtime.api.admission_status=lambda pair:dict(recovery=self.runtime.api.admission(pair),normal_capacity_available=None)
        self.runtime.executor.shutdown(wait=True)
        self.runtime.executor=SimpleNamespace(submit=lambda work:work(),shutdown=lambda **kw:None)
        self.timers=[];self.cancelled=[]
        def later(delay,callback):
            timer=(delay,callback);self.timers.append(timer);return timer
        self.runtime.later=later;self.runtime.cancel=self.cancelled.append
        self.runtime._events=lambda:None

    def drain(self):
        while self.callbacks:self.callbacks.pop(0)()

    def test_requester_429_storm_pause_resume_and_reconstruction(self):
        from luma_continuity.account import AccountError
        from luma_continuity.call_retry import CallRetry
        self.requester();attempts=[]
        def limited(*_):attempts.append(1);raise AccountError('rate_limited',429,86400)
        self.runtime.api.mint=limited
        self.runtime.reconcile();self.drain()
        for _ in range(100):self.runtime.reconcile()
        self.assertEqual(len(attempts),1)
        self.assertEqual(self.runtime.retry_state(self.peer)['reason'],'rate_limited')
        pending=list(self.runtime.retry_timers.values());self.runtime.pause()
        self.assertTrue(all(timer[1] in self.cancelled for timer in pending))
        self.runtime.resume();self.drain()
        self.runtime.retry(self.peer);self.runtime.reconcile()
        self.assertEqual(len(attempts),1)
        reloaded=CallRetry(self.root,[self.binding],now=lambda:self.clock)
        self.assertEqual(reloaded.state(self.f.pair)['retry_at'],87400)

    def test_stale_retry_timer_cannot_mint_after_pause(self):
        from luma_continuity.account import AccountError
        self.requester();attempts=[]
        def failed(*_):attempts.append(1);raise AccountError('unavailable',503)
        self.runtime.api.mint=failed
        self.runtime.reconcile();self.drain()
        callbacks=[timer[1][1] for timer in self.runtime.retry_timers.values()]
        self.runtime.pause();self.clock+=100
        for callback in callbacks:callback()
        self.assertEqual(len(attempts),1)

    def test_repeated_snapshots_do_not_reset_failed_attempt_budget(self):
        self.requester()
        def failed(*_):raise OSError('synthetic')
        self.runtime.api.mint=failed
        for _ in range(3):
            self.runtime.reconcile();self.drain();self.clock+=400
        for _ in range(10):
            self.runtime.owner.apply('snapshot','cursor',dict(device_id=self.f.device,reset=False,sessions=[]))
            self.runtime.reconcile();self.drain()
        self.assertEqual(self.runtime.retry_state(self.peer)['reason'],'retry_required')
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['attempts'],3)

    def test_authenticated_channel_callback_resets_budget_but_metadata_does_not(self):
        self.requester();self.runtime.retry_policy.reserve(self.f.pair)
        self.runtime.owner.apply('snapshot','cursor',dict(device_id=self.f.device,reset=False,sessions=[]))
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['attempts'],1)
        ready=[]
        def carrier(*args,**kw):
            ready.append(kw['ready']);return SimpleNamespace(close=lambda:None)
        def control(*args,**kw):
            kw['channel_factory'](check=lambda:None,receive=lambda value:None,failed=lambda:None)
            return SimpleNamespace(close=lambda:None)
        session=self.f.api._session(self.f.metadata)
        with patch('luma_continuity.call_runtime.RelayJSONChannel',carrier),patch('luma_continuity.call_runtime.CallControlChannel',control):
            channel=self.runtime._channel(session,lambda:True)
            self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['attempts'],1)
            ready[0]();self.drain()
            self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['attempts'],0)
            channel.close()


    def test_cancelled_callback_cannot_remove_new_timer(self):
        self.requester();self.runtime.retry_policy.reserve(self.f.pair)
        self.runtime._schedule_retry(self.f.pair)
        old=self.runtime.retry_timers[self.f.pair][1][1]
        self.runtime._schedule_retry(self.f.pair)
        current=self.runtime.retry_timers[self.f.pair]
        old()
        self.assertIs(self.runtime.retry_timers[self.f.pair],current)


    def recovery_grant(self):
        return dict(recovery_id='55555555-5555-4555-8555-555555555555',expires=1600,purposes=['call-control'])

    def test_pause_burns_reserved_recovery_without_dispatching_handoff(self):
        self.requester();self.runtime.api.admission=lambda pair:self.recovery_grant()
        self.runtime.retry(self.peer)
        self.assertEqual(len(self.runtime.retry_policy.recoveries),1)
        self.runtime.pause();self.drain()
        self.assertEqual(self.f.calls,[])
        self.assertEqual(self.runtime.recovery_requests,{})
        self.assertEqual(len(self.runtime.retry_policy.recoveries),1)

    def test_cancel_during_reservation_burns_credit_without_mint(self):
        self.requester();self.runtime.api.admission=lambda pair:self.recovery_grant()
        reserve=self.runtime.retry_policy.reserve_recovery
        def cancelled(*args):
            result=reserve(*args);self.runtime.pause();return result
        self.runtime.retry_policy.reserve_recovery=cancelled
        with self.assertRaises(PermissionError):self.runtime.retry(self.peer)
        self.drain();self.assertEqual(self.f.calls,[])
        self.assertEqual(len(self.runtime.retry_policy.recoveries),1)

    def test_late_admission_after_pause_never_reserves_or_mints(self):
        self.requester()
        def late(pair):self.runtime.pause();return self.recovery_grant()
        self.runtime.api.admission=late
        with self.assertRaises(PermissionError):self.runtime.retry(self.peer)
        self.assertEqual(self.runtime.retry_policy.recoveries,{})
        self.assertEqual(self.f.calls,[])

    def test_lost_recovery_mint_response_never_replays_or_auto_mints(self):
        self.requester();self.runtime.api.admission=lambda pair:self.recovery_grant();attempts=[]
        def lost(*args):attempts.append(args);raise OSError('lost synthetic response')
        self.runtime.api.mint=lost
        self.runtime.retry(self.peer);self.drain()
        self.assertEqual(len(attempts),1)
        self.clock+=400
        for _ in range(10):self.runtime.reconcile();self.drain()
        self.assertEqual(len(attempts),1)
        with self.assertRaises(PermissionError):self.runtime.retry(self.peer)
        self.assertEqual(len(attempts),1)

    def test_late_mint_response_after_pause_cannot_ensure_session(self):
        self.requester();self.runtime.api.admission=lambda pair:self.recovery_grant();ensures=[]
        def late(*args):self.runtime.pause();return {'attempt_id':self.f.attempt}
        self.runtime.api.mint=late;self.runtime.api.ensure_call=lambda *args:ensures.append(args)
        self.runtime.retry(self.peer);self.drain()
        self.assertEqual(ensures,[])
        self.assertEqual(len(self.runtime.retry_policy.recoveries),1)

    def audio_ready(self):
        self.requester();self.offer();self.runtime.ready.add(self.f.sid)
        self.native_generation='c'*32;self.native_active=True;self.mints=[]
        def exchange(peer,request):
            if request['capability']=='calls.read':return {'state':'complete','result':{'calls':[
                {'id':'native-call','generation':self.native_generation,'phase':'active'}] if self.native_active else []}}
            return {'state':'complete','result':{'prepared':True}}
        self.runtime.exchange=exchange
        self.runtime.audio.prepare=lambda *args:None
        self.runtime.audio.allowed=lambda session:True
        self.runtime.api.admission=lambda pair:dict(self.recovery_grant(),purposes=['audio-signaling'])
        def mint(pair,purpose,recovery_id=None):
            self.mints.append((purpose,recovery_id));return {'attempt_id':self.f.attempt}
        self.runtime.api.mint=mint
        self.runtime.api.ensure_call=lambda *args:SimpleNamespace()
        self.runtime.retry_policy.rate_limited(self.f.pair,86400,'call_attempt_limit')

    def test_audio_recovery_requires_explicit_current_call_and_is_once_only(self):
        self.audio_ready();self.runtime.start_audio(self.native_generation)
        self.assertEqual(self.mints,[('audio-signaling',self.recovery_grant()['recovery_id'])])
        with self.assertRaises(PermissionError):self.runtime.start_audio(self.native_generation)
        self.assertEqual(len(self.mints),1)

    def test_audio_recovery_never_overrides_general_cooldown(self):
        self.audio_ready();self.runtime.retry_policy.rate_limited(self.f.pair,60,'rate_limited')
        with self.assertRaises(PermissionError):self.runtime.start_audio(self.native_generation)
        self.assertEqual(self.mints,[])
        self.assertEqual(self.runtime.retry_policy.recoveries,{})

    def test_audio_call_change_after_admission_never_reserves_or_mints(self):
        self.audio_ready()
        def changed(pair):
            self.native_active=False;return dict(self.recovery_grant(),purposes=['audio-signaling'])
        self.runtime.api.admission=changed
        with self.assertRaises(PermissionError):self.runtime.start_audio(self.native_generation)
        self.assertEqual(self.mints,[]);self.assertEqual(self.runtime.retry_policy.recoveries,{})

    def test_audio_cancel_after_reservation_burns_credit_without_mint(self):
        self.audio_ready();reserve=self.runtime.retry_policy.reserve_recovery
        def cancelled(*args):
            result=reserve(*args);self.runtime.pause();return result
        self.runtime.retry_policy.reserve_recovery=cancelled
        with self.assertRaises(PermissionError):self.runtime.start_audio(self.native_generation)
        self.assertEqual(self.mints,[]);self.assertEqual(len(self.runtime.retry_policy.recoveries),1)

    def test_observed_late_session_is_cancelled_when_authority_remains(self):
        self.requester();self.runtime.api.admission=lambda pair:self.recovery_grant();closed=[]
        session=SimpleNamespace(session_id=self.f.sid)
        def late(*args):self.runtime.generation+=1;return session
        self.runtime.api.ensure_call=late;self.runtime.api.close_call=closed.append
        self.runtime.retry(self.peer);self.drain()
        self.assertEqual(closed,[session])

    def test_explicit_capacity_restore_uses_normal_mint_without_resetting_budget(self):
        self.requester();grant=self.recovery_grant()
        self.runtime.retry_policy.reserve_recovery(self.f.pair,grant,'call-control')
        self.runtime.retry_policy.rate_limited(self.f.pair,86400,'call_attempt_limit');self.clock+=20
        self.runtime.api.admission_status=lambda pair:dict(recovery=grant,normal_capacity_available=True)
        self.runtime.retry(self.peer);self.drain()
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['capacity_until'],0)
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['attempts'],4)
        self.assertEqual(len(self.runtime.retry_policy.recoveries),1)
        mint=[c for c in self.f.calls if c[1].endswith('/call-relay-attempts')]
        self.assertEqual(len(mint),1);self.assertNotIn('recovery_id',mint[0][2])

    def test_capacity_restore_does_not_bypass_general_rate_deadline(self):
        self.requester();self.runtime.retry_policy.rate_limited(self.f.pair,86400,'call_attempt_limit')
        self.runtime.retry_policy.rate_limited(self.f.pair,600,'rate_limited')
        self.runtime.api.admission_status=lambda pair:dict(recovery=None,normal_capacity_available=True)
        self.runtime.retry(self.peer);self.drain()
        self.assertEqual(self.f.calls,[])
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['server_until'],1600)
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['capacity_until'],0)

    def test_cancelled_capacity_status_never_releases_suppression(self):
        self.requester();self.runtime.retry_policy.rate_limited(self.f.pair,86400,'call_attempt_limit')
        def stale(pair):self.runtime.pause();return dict(recovery=None,normal_capacity_available=True)
        self.runtime.api.admission_status=stale
        with self.assertRaises(PermissionError):self.runtime.retry(self.peer)
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['capacity_until'],87400)
        self.assertEqual(self.f.calls,[])

    def test_legacy_null_grant_never_infers_capacity_restoration(self):
        self.requester();self.runtime.retry_policy.rate_limited(self.f.pair,86400,'call_attempt_limit')
        self.runtime.retry(self.peer);self.drain()
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['capacity_until'],87400)
        self.assertEqual(self.f.calls,[])

    def test_explicit_active_audio_can_use_authenticated_restored_normal_capacity(self):
        self.audio_ready()
        self.runtime.api.admission_status=lambda pair:dict(recovery=None,normal_capacity_available=True)
        self.runtime.start_audio(self.native_generation)
        self.assertEqual(self.mints,[('audio-signaling',None)])
        self.assertEqual(self.runtime.retry_policy.recoveries,{})
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['capacity_until'],0)


    def test_cancel_at_capacity_write_keeps_original_suppression(self):
        self.requester();self.runtime.retry_policy.rate_limited(self.f.pair,86400,'call_attempt_limit')
        self.runtime.api.admission_status=lambda pair:dict(recovery=None,normal_capacity_available=True)
        original=self.runtime.retry_policy.capacity_restored
        def cancelled(*args,**kw):self.runtime.pause();return original(*args,**kw)
        self.runtime.retry_policy.capacity_restored=cancelled
        with self.assertRaises(PermissionError):self.runtime.retry(self.peer)
        self.assertEqual(self.runtime.retry_policy.rows[self.f.pair]['capacity_until'],87400)
        self.assertEqual(self.f.calls,[])
