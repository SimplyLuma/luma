"""Real paired TLS subscriptions; no native service or carrier operation."""
from pathlib import Path
import queue
import secrets
import socket
import tempfile
import time
import unittest
from luma_continuity.bootstrap import create_identity,approve_peer
from luma_continuity.call_events import CallListener,CallExchange,CallSubscription
from luma_continuity.policy import Journal


class CallEventsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.client,self.server=(Path(temporary.name)/name for name in ('client','server'))
        self.cp=create_identity(self.client);self.sp=create_identity(self.server)
        self.epoch='e'*32;self.account='synthetic-account';self.invocations=[];self.invalidated=0
        for directory,other,pin in ((self.client,self.server,self.sp),(self.server,self.client,self.cp)):
            approve_peer(directory,other/'device.pem',pin,self.epoch,['calls.read','calls.control'],account=self.account)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
        owner=self
        class Adapter:
            def __call__(self,capability,payload):
                owner.invocations.append((capability,payload))
                return {'calls':[],'dial_token':None}
            def invalidate(self):owner.invalidated+=1
            def incoming(self):return False
        self.listener=CallListener(self.server,self.cp,address='127.0.0.1',port=port,authorized=lambda:True,
            adapter_factory=lambda *_:(Adapter(),lambda:None))
        self.listener.start();self.addCleanup(self.listener.stop)
        self.exchange=CallExchange(self.client,self.sp,self.epoch,'127.0.0.1',port,
            account=self.account,timeout=2,authorized=lambda:True)
        self.events=queue.Queue();self.subscription=CallSubscription(self.exchange,self.events.put)
        self.subscription.start();self.addCleanup(self.subscription.stop)
        self.assertIs(self.events.get(timeout=3),True)

    def request(self):
        return dict(version=1,account=self.account,epoch=self.epoch,id=secrets.token_hex(16),
                    capability='calls.read',expires=int(time.time())+10,payload={'operation':'snapshot'})

    def test_authenticated_events_and_requests_coexist_without_durable_calls(self):
        self.assertEqual(self.invocations,[])  # subscribe neither reads native state nor mints a token
        self.listener.changed()
        self.assertIs(self.events.get(timeout=3),True)
        self.assertEqual(self.exchange(self.request())['state'],'complete')
        self.assertEqual(len(self.invocations),1)
        journal=Journal(self.server/'continuity.db')
        try:self.assertEqual(journal.db.execute('SELECT count(*) FROM requests').fetchone()[0],0)
        finally:journal.close()

    def test_native_owner_replacement_disconnects_and_invalidates(self):
        self.listener.owner_changed()
        self.assertIs(self.events.get(timeout=3),False)
        self.assertEqual(self.invalidated,1)

    def test_revoked_read_emits_no_further_event(self):
        journal=Journal(self.server/'continuity.db')
        try:journal.set_grants(self.cp,[],outgoing_grants=[])
        finally:journal.close()
        self.listener.changed()
        self.assertIs(self.events.get(timeout=3),False)
        self.assertEqual(self.invocations,[])

    def test_idle_stop_interrupts_stream(self):
        self.listener.stop()
        self.assertIs(self.events.get(timeout=3),False)
        self.subscription.thread.join(1)
        self.assertFalse(self.subscription.thread.is_alive())


if __name__=='__main__':unittest.main()
