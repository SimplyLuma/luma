from native_sender_fixture import NativeSender
"""Concurrent call observers queue reads; commands stay exclusive and unreplayed."""
from types import SimpleNamespace
import json,time,unittest
from luma_continuity.daemon import Daemon


class CallReadContentionTests(unittest.TestCase):
    def setUp(self):
        from gi.repository import GLib
        d=self.daemon=Daemon.__new__(Daemon)
        self.jobs=[];self.idle=[];self.exchanged=[]
        d.closed=False;d.enabled=True;d.credential_locked=False;d.environment_generation=0
        d.call_busy=False;d.call_reads=0
        d.GLib=SimpleNamespace(Variant=GLib.Variant,idle_add=lambda callback,*args:self.idle.append((callback,args)))
        d.call_worker=SimpleNamespace(submit=self.jobs.append)
        d.calls=SimpleNamespace(exchange_call=lambda request:self.exchanged.append(request) or {'state':'complete','result':{}})
        now=time.time()
        d.model=SimpleNamespace(snapshot=lambda:dict(account_status='signed_in',service_status='ready',stale=False,
            valid_until=now+60,lease_deadline_monotonic=time.monotonic()+60))
        self.GLib=GLib

    def call(self,capability):
        result={}
        invocation=SimpleNamespace(return_value=lambda value:result.setdefault('value',value),
            return_dbus_error=lambda name,message:result.setdefault('error',name))
        body=json.dumps({'capability':capability,'payload':{'operation':'snapshot'}})
        self.daemon._call(NativeSender(),':native-test',None,None,'ExchangeCall',self.GLib.Variant('(s)',(body,)),invocation)
        return result

    def drain(self):
        while self.jobs:
            self.jobs.pop(0)()
            while self.idle:
                callback,args=self.idle.pop(0);callback(*args)

    def test_two_observers_reading_the_same_call_are_not_reported_busy(self):
        # Connect43 rejected the second observer with Busy; Phone then showed
        # PHONE DISCONNECTED although the call and link were healthy.
        first=self.call('calls.read');second=self.call('calls.read')
        self.assertNotIn('error',first);self.assertNotIn('error',second)
        self.drain()
        self.assertIn('value',first);self.assertIn('value',second)
        self.assertEqual(len(self.exchanged),2)
        self.assertEqual((self.daemon.call_busy,self.daemon.call_reads),(False,0))

    def test_reads_are_bounded_and_commands_remain_exclusive(self):
        reads=[self.call('calls.read') for _ in range(5)]
        self.assertEqual([('error' in r) for r in reads],[False]*4+[True])
        control=self.call('calls.control');again=self.call('calls.control')
        self.assertNotIn('error',control);self.assertIn('error',again)
        self.drain()
        self.assertEqual(len(self.exchanged),5)  # four reads and exactly one command
        self.assertEqual((self.daemon.call_busy,self.daemon.call_reads),(False,0))
