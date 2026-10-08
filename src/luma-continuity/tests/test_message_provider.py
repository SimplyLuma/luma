"""Synthetic native stores only; no actual device, account or carrier access."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
if os.environ.get('LUMA_NATIVE_SOURCE'): sys.path.insert(0, os.environ['LUMA_NATIVE_SOURCE'])
from prairie_apps.messages_backend import MessageStore
from luma_continuity.bootstrap import create_identity
from luma_continuity.message_provider import MessageProvider, SelectedPhone
from luma_continuity.policy import Journal
from luma_continuity.queue import Outbox


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.device = self.root/'device'
        create_identity(self.device)
        self.peer, self.epoch = 'a'*64, 'b'*32
        self.account = 'https://identity.example/realm|synthetic-subject'
        j=Journal(self.device/'continuity.db')
        j.approve(self.peer,self.epoch,[],account=self.account,outgoing_grants=['messages.read','messages.send']);j.close()
        self.observation={'account':self.account,'signed_in':True,'stale':False}
        self.requests=[];self.receipts={};self.history=[];self.offline=False;self.lose_ack=False
        self.callbacks=[];self.notices=[];self.timers=[];self.clock=[1000.0]
        def factory(*_args,**_kwargs):return self.exchange
        self.provider=self.make_provider(factory,startup_grace=0)
        self.addCleanup(self.provider.close)

    def make_provider(self, factory, **kwargs):
        class Timer:
            def __init__(timer, delay, callback):timer.delay,timer.callback,timer.cancelled=delay,callback,False
            def cancel(timer):timer.cancelled=True
        def start(delay, callback):
            timer=Timer(delay,callback);self.timers.append(timer);return timer
        return MessageProvider(self.device,SelectedPhone(self.peer,self.epoch,self.account,'Synthetic phone','127.0.0.1',12345),
            account_observation=lambda:self.observation,store_factory=MessageStore,dispatch=self.callbacks.append,exchange_factory=factory,
            timer=start,monotonic=lambda:self.clock[0],**kwargs)

    def settle(self):
        for _ in range(10):
            pending=[f for f in (self.provider._validation,self.provider._active) if f is not None and not f.done()]
            for future in pending:future.result(5)
            if not self.callbacks and not pending:return
            self.publish()

    def exchange(self, request):
        if self.offline:raise OSError('synthetic offline')
        self.assertEqual(request['account'],self.account)
        self.requests.append(request)
        if request['capability']=='messages.send':
            if request['id'] not in self.receipts:
                row=dict(uid='remote:'+request['id'],address=request['payload']['address'],body=request['payload']['body'],
                         timestamp=123,direction='outgoing',state='sent',attachments=[])
                self.history.append(row)
                self.receipts[request['id']]={'state':'complete','result':{'uid':row['uid'],'state':'sent'}}
            if self.lose_ack:self.lose_ack=False;raise OSError('synthetic lost receipt')
            return self.receipts[request['id']]
        payload=request['payload']
        result={'threads':[{'address':'+12025550123','display_name':'Synthetic'}], 'truncated':False} if 'query' in payload else {'messages':self.history}
        return {'state':'complete','result':result}

    def publish(self):
        callbacks=self.callbacks[:];self.callbacks.clear()
        for callback in callbacks:callback()

    def test_locked_account_preserves_cache_and_recovers_without_pairing(self):
        self.provider.refresh().result(5);self.publish()
        prior=len(self.requests)
        self.observation.update(signed_in=False,stale=True,locked=True)
        result=self.provider.refresh().result(5);self.publish()
        self.assertEqual(result['state'],'locked')
        self.assertEqual(len(self.requests),prior)
        self.assertFalse(self.provider.allowed('messages.send'))
        self.assertIn('Unlock',self.provider.sms_transport().inspect().reason)
        self.observation.update(signed_in=True,stale=False,locked=False)
        result=self.provider.refresh().result(5);self.publish()
        self.assertEqual(result['state'],'ready')
        self.assertTrue(self.provider.allowed('messages.send'))
        self.assertEqual(self.provider.sms_transport().inspect().reason,'')

    def test_offline_phone_is_retried_with_backoff_until_it_answers(self):
        self.offline=True
        self.provider.start(self.notices.append).result(5);self.settle()
        self.assertEqual(self.notices[-1]['state'],'offline')
        self.assertIn('Waiting for your phone',self.provider.sms_transport().inspect().reason)
        self.assertEqual([t.delay for t in self.timers],[2])
        self.timers[-1].callback();self.settle()
        self.assertEqual([t.delay for t in self.timers],[2,4])
        self.offline=False
        self.timers[-1].callback();self.settle()
        self.assertEqual(self.notices[-1]['state'],'ready')
        self.assertEqual(len(self.timers),2)  # ready stops retrying
        self.offline=True;self.provider.refresh().result(5);self.settle()
        self.assertEqual(self.timers[-1].delay,2)  # backoff starts over after recovery

    def test_login_race_locked_then_unlocked_recovers_without_a_signal(self):
        provider=self.provider;provider.close()
        self.provider=self.make_provider(lambda *_a,**_k:self.exchange,startup_grace=15);self.addCleanup(self.provider.close)
        self.observation.update(signed_in=False,stale=True,locked=True)
        self.provider.start(self.notices.append).result(5);self.settle()
        self.assertEqual(self.notices[-1]['state'],'connecting')
        self.assertNotIn('Unlock',self.provider.sms_transport().inspect().reason)
        self.clock[0]+=16;self.timers[-1].callback();self.settle()
        self.assertEqual(self.notices[-1]['state'],'locked')
        # PAM unlocked the keyring; Connect published nothing new. Retry still recovers.
        self.observation.update(signed_in=True,stale=False,locked=False)
        pending=self.timers[-1];self.provider.retry();self.settle()
        self.assertTrue(pending.cancelled)
        self.assertEqual(self.notices[-1]['state'],'ready')

    def test_close_cancels_pending_retry(self):
        self.offline=True
        self.provider.start(self.notices.append).result(5);self.settle()
        timer=self.timers[-1];self.provider.close()
        self.assertTrue(timer.cancelled)
        count=len(self.notices);timer.callback();self.settle()
        self.assertEqual(len(self.notices),count)

    def test_incoming_and_repeated_history_are_idempotent(self):
        self.history=[dict(uid='remote-in',address='+12025550123',body='Synthetic incoming',timestamp=1,direction='incoming',state='received',attachments=[])]
        self.provider.start(self.notices.append).result(2);self.publish()
        self.provider.refresh().result(2);self.publish()
        store=MessageStore(self.provider.store_path)
        try:self.assertEqual(len(store.thread('+12025550123')),1)
        finally:store.close()
        self.assertEqual(self.notices[-1]['imported'],0)

    def test_offline_compose_lost_ack_reconnect_matches_one_local_record(self):
        self.offline=True
        store=MessageStore(self.provider.store_path)
        try:
            record=store.add('+12025550123','Synthetic outgoing',direction='outgoing')
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as tx: tx.submit(self.provider.send_message, record.uid).result(2)
            self.provider._active.result(2)
            self.assertEqual(store.message(record.uid).state,'queued')
            self.offline=False;self.lose_ack=True
            self.provider.refresh().result(2)
            self.provider.refresh().result(2);self.publish()
            self.assertEqual(len(self.receipts),1)
            self.assertEqual(len(store.thread('+12025550123')),1)
            self.assertEqual(store.message(record.uid).state,'sent')
            sends=[r for r in self.requests if r['capability']=='messages.send']
            self.assertEqual(sends[0],sends[1])
        finally:store.close()

    def test_revocation_withholds_returned_history_and_late_ui(self):
        original=self.exchange
        def revoke(request):
            response=original(request)
            self.observation['stale']=True
            return response
        self.provider.exchange_factory=lambda *_a,**_k:revoke
        result=self.provider.start(self.notices.append).result(2)
        self.assertEqual(result['state'],'unavailable')
        self.publish();self.assertEqual(self.notices[-1]['state'],'unavailable')
        store=MessageStore(self.provider.store_path)
        try:self.assertEqual(store.threads(),())
        finally:store.close()

    def test_unknown_receipt_reconciles_from_native_sent_without_another_send(self):
        from concurrent.futures import ThreadPoolExecutor
        original=self.exchange
        def uncertain(request):
            response=original(request)
            if request['capability']=='messages.send':
                self.history[-1]['state']='queued'
                response['result']['state']='unknown'
            return response
        self.provider.exchange_factory=lambda *_a,**_k:uncertain
        self.provider.refresh().result(2);self.publish()
        store=MessageStore(self.provider.store_path)
        try:
            record=store.add('+12025550123','Synthetic uncertain send',direction='outgoing')
            with ThreadPoolExecutor(max_workers=1) as tx:tx.submit(self.provider.send_message,record.uid).result(2)
            self.provider._active.result(2);self.publish()
            self.assertEqual(self.provider.status['state'],'attention')
            box=Outbox(self.provider.outbox_path)
            try:original_receipt=box.db.execute('SELECT result FROM outbox WHERE id=?',(record.uid,)).fetchone()[0]
            finally:box.close()
            # Native history learns the modem result later; the old receipt is immutable.
            self.history[-1]['state']='sent'
            self.provider.refresh().result(2);self.publish()
            self.assertEqual(self.provider.status['state'],'ready')
            self.assertEqual(store.message(record.uid).state,'sent')
            self.assertEqual(len([r for r in self.requests if r['capability']=='messages.send']),1)
            box=Outbox(self.provider.outbox_path)
            try:
                self.assertEqual(box.db.execute('SELECT result FROM outbox WHERE id=?',(record.uid,)).fetchone()[0],original_receipt)
                self.assertEqual(box.db.execute('SELECT state FROM reconciled_sends WHERE id=?',(record.uid,)).fetchone(),('sent',))
            finally:box.close()
        finally:store.close()

    def test_reconciliation_cannot_cross_identity_or_revocation(self):
        box=Outbox(self.provider.outbox_path)
        try:
            op=box.enqueue(self.peer,self.epoch,'messages.send',{'address':'+12025550123','body':'Synthetic'},account=self.account)
            box.acknowledge(op,{'state':'complete','result':{'uid':'native-fixture','state':'unknown'}})
            for peer,epoch,account,remote in ((self.peer,self.epoch,'other','native-fixture'),
                    ('c'*64,self.epoch,self.account,'native-fixture'),(self.peer,'d'*32,self.account,'native-fixture'),
                    (self.peer,self.epoch,self.account,'wrong-native-uid')):
                self.assertFalse(box.reconcile_sent(op,peer,epoch,remote,account=account))
            self.assertEqual(box.db.execute('SELECT count(*) FROM reconciled_sends').fetchone()[0],0)
            box.revoke(self.peer)
            self.assertFalse(box.reconcile_sent(op,self.peer,self.epoch,'native-fixture',account=self.account))
        finally:box.close()

    def test_logout_after_worker_before_publish_invalidates_ready_result(self):
        self.provider.start(self.notices.append).result(2)
        self.observation['signed_in']=False
        self.provider.invalidate()  # source-owned account signal on main loop
        self.assertFalse(self.provider.allowed("messages.send"))
        self.provider._validation.result(2)
        self.publish()
        self.assertEqual(self.notices[-1]['state'],'unavailable')
        self.assertFalse(self.provider.allowed('messages.send'))

    def test_grant_change_after_worker_before_publish_invalidates_ready_result(self):
        self.provider.start(self.notices.append).result(2)
        journal=Journal(self.device/'continuity.db');journal.revoke(self.peer);journal.close()
        self.provider.invalidate()  # source-owned grant invalidation event
        self.assertFalse(self.provider.allowed("messages.read"))
        self.provider._validation.result(2)
        self.publish()
        self.assertEqual(self.notices[-1]['state'],'unavailable')

    def test_refresh_coalesces_and_close_suppresses_late_result(self):
        entered,release=threading.Event(),threading.Event()
        def slow(request):entered.set();release.wait(2);return self.exchange(request)
        self.provider.exchange_factory=lambda *_a,**_k:slow
        first=self.provider.start(self.notices.append);self.assertTrue(entered.wait(1))
        self.assertIs(first,self.provider.refresh())
        self.provider.close();release.set();first.result(2);self.publish()
        self.assertEqual(self.notices,[])
        self.assertIsNone(self.provider.refresh())

    def test_expired_and_unknown_are_attention_not_automatic_resend(self):
        box=Outbox(self.provider.outbox_path)
        try:
            op=box.enqueue(self.peer,self.epoch,'messages.send',{'address':'+12025550123','body':'Synthetic'},account=self.account,now=0)
            other=box.enqueue(self.peer,self.epoch,'messages.send',{'address':'+12025550123','body':'Synthetic unknown'},account=self.account)
            box.acknowledge(other,{'state':'unknown','result':None})
        finally:box.close()
        result=self.provider.refresh().result(2)
        self.assertEqual(result['state'],'attention');self.assertEqual(result['needs_review'],2)
        self.assertFalse(any(r['capability']=='messages.send' for r in self.requests))

    def test_wrong_account_and_send_grant_are_fail_closed(self):
        self.observation['account']='different-account'
        self.assertEqual(self.provider.refresh().result(2)['state'],'unavailable')
        self.assertEqual(self.requests,[])
        self.observation['account']=self.account
        j=Journal(self.device/'continuity.db');j.set_grants(self.peer,[],outgoing_grants=['messages.read']);j.close()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as tx:
            with self.assertRaises(PermissionError):tx.submit(self.provider.send_message,'c'*32).result(2)

    def test_unchanged_events_revalidate_and_recover_without_unavailable(self):
        self.provider.start(self.notices.append).result(2);self.publish()
        for _ in range(100):self.provider.invalidate()
        self.assertFalse(self.provider.allowed('messages.read'))
        # Event bursts coalesce. A validation overtaken by later events is
        # discarded and repeated before it can restore admission.
        for _ in range(5):
            if self.provider._validation is None:break
            self.provider._validation.result(2);self.publish()
        self.provider._active.result(2);self.publish()
        self.assertTrue(self.provider.allowed('messages.read'))
        self.assertEqual(self.notices[-1]['state'],'ready')
        self.assertNotIn('unavailable',[n['state'] for n in self.notices])

    def test_read_only_grant_is_described_as_read_only(self):
        j=Journal(self.device/'continuity.db');j.set_grants(self.peer,[],outgoing_grants=['messages.read']);j.close()
        self.provider.start(self.notices.append).result(2);self.publish()
        capability=self.provider.sms_transport().inspect()
        self.assertFalse(capability.available)
        self.assertEqual(capability.reason,'Read-only messages via Synthetic phone.')

    def test_real_glib_monitor_churn_recovers_and_revocation_stops_access(self):
        from gi.repository import Gio,GLib
        loop=GLib.MainLoop();states=[]
        self.provider.dispatch=GLib.idle_add
        monitor=Gio.File.new_for_path(str(self.device)).monitor_directory(Gio.FileMonitorFlags.NONE,None)
        self.addCleanup(monitor.cancel)
        monitor.connect('changed',self.provider.invalidate)
        self.provider.start(lambda state:states.append(state['state']))
        remaining=[8]
        def event():
            self.provider.invalidate();remaining[0]-=1
            return remaining[0]>0
        GLib.timeout_add(50,event)
        GLib.timeout_add(1200,lambda:(loop.quit(),False)[1]);loop.run()
        self.assertTrue(states)
        self.assertEqual(states[-1],'ready')
        self.assertNotIn('unavailable',states)
        states.clear()
        j=Journal(self.device/'continuity.db');j.revoke(self.peer);j.close()
        GLib.timeout_add(800,lambda:(loop.quit(),False)[1]);loop.run()
        self.assertEqual(states[-1],'unavailable')
        self.assertFalse(self.provider.allowed('messages.read'))

    def test_send_revoked_between_queued_requests_does_not_send_second(self):
        store=MessageStore(self.provider.store_path);box=Outbox(self.provider.outbox_path)
        try:
            for text in ('Synthetic first','Synthetic second'):
                row=store.add('+12025550123',text,direction='outgoing')
                box.enqueue(self.peer,self.epoch,'messages.send',{'address':row.address,'body':row.body},operation_id=row.uid,account=self.account)
        finally:box.close();store.close()
        original=self.exchange
        def exchange(request):
            result=original(request)
            if request['capability']=='messages.send':
                j=Journal(self.device/'continuity.db');j.set_grants(self.peer,[],outgoing_grants=['messages.read']);j.close()
            return result
        self.provider.exchange_factory=lambda *_a,**_kw:exchange
        self.provider.refresh().result(2);self.publish()
        self.assertEqual(len(self.receipts),1)
        self.assertEqual(len([r for r in self.requests if r['capability']=='messages.send']),1)

    def test_picture_capability_tracks_async_permission_and_revocation(self):
        capabilities=[]
        mms=self.provider.mms_transport()
        mms.start(lambda *_:None,lambda value:capabilities.append(value.available))
        self.assertFalse(capabilities[-1])
        self.provider.start(self.notices.append).result(2);self.publish()
        self.assertTrue(capabilities[-1])
        self.provider.invalidate()
        self.assertFalse(capabilities[-1])
        j=Journal(self.device/'continuity.db');j.set_grants(self.peer,[],outgoing_grants=['messages.read']);j.close()
        self.provider._validation.result(2);self.publish()
        self.provider._active.result(2);self.publish()
        self.assertFalse(capabilities[-1])
        mms.stop()
