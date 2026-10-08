import unittest
from dataclasses import replace
from types import SimpleNamespace
from luma_continuity.account import AccountError
from luma_continuity.call_lease_owner import CallLeaseOwner
from luma_continuity.call_relay_directory import CallRelayDirectory
import test_call_relay_api as fixture


class LeaseOwnerTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CallRelayAPITests();self.f.setUp()
        self.now=1000;self.allowed=True;self.jobs=[];self.callbacks=[];self.timers={};self.channels=[]
        self.directory=CallRelayDirectory(self.f.device,[self.f.pair],self.f.origin,now=lambda:self.now)
        def later(delay,callback):
            key=object();self.timers[key]=(self.now+delay,callback);return key
        def factory(session,allowed):
            self.assertTrue(allowed())
            channel=SimpleNamespace(closed=False,start=lambda:None)
            channel.close=lambda:setattr(channel,'closed',True)
            self.channels.append(channel);return channel
        self.owner=CallLeaseOwner(self.f.api,self.directory,authorized=lambda _:self.allowed,
            factory=factory,dispatch=self.callbacks.append,later=later,cancel=lambda key:self.timers.pop(key,None),
            now=lambda:self.now,executor=SimpleNamespace(submit=self.jobs.append))
        self.addCleanup(self.owner.close)
        self.owner.apply('session_offered','first',self.f.metadata)

    def finish(self):
        self.jobs.pop(0)()
        self.callbacks.pop(0)()

    def test_refresh_rotates_current_bearer_without_replacing_channel(self):
        tokens=[];token=['before']
        self.f.api.bearer=lambda:token[0]
        original=self.f.api.api.request
        def request(method,path,bearer,body):
            tokens.append(bearer);return original(method,path,bearer,body)
        self.f.api.api.request=request
        token[0]='after';self.owner.account_refreshed();self.finish()
        self.assertEqual(tokens,['after']);self.assertEqual(len(self.channels),1)
        self.assertFalse(self.channels[0].closed)

    def test_consent_loss_while_renewal_in_flight_discards_result(self):
        self.owner.renew(self.f.sid);self.jobs.pop(0)();self.allowed=False
        self.callbacks.pop(0)()
        self.assertTrue(self.channels[0].closed);self.assertEqual(self.owner.active,{})

    def test_transient_failure_preserves_hard_expiry(self):
        def failed(_):raise AccountError('unavailable')
        self.f.api.renew=failed
        self.owner.renew(self.f.sid);self.finish()
        self.assertFalse(self.channels[0].closed)
        self.now=1601
        for _,callback in list(self.timers.values()):callback()
        self.assertTrue(self.channels[0].closed)

    def test_reconnect_preserves_valid_channel_until_fresh_snapshot(self):
        self.owner.events_disconnected();self.assertFalse(self.channels[0].closed)
        self.owner.apply('snapshot','fresh',dict(device_id=self.f.device,sessions=[],reset=True))
        self.assertTrue(self.channels[0].closed)

    def test_renewal_updates_directory_and_same_generation_in_place(self):
        session=self.owner.active[self.f.sid].session
        self.f.api.renew=lambda _:replace(session,expires=1800)
        self.owner.renew(self.f.sid);self.finish()
        self.assertEqual(self.directory.sessions[(self.f.pair,'call-control')].expires,1800)
        self.owner.apply('session_offered','same',dict(self.f.metadata,expires=1800))
        self.assertEqual(len(self.channels),1)
        self.assertEqual(self.owner.active[self.f.sid].session.expires,1800)
