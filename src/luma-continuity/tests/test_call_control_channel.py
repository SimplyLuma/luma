"""Real paired TLS carries native one-use call controls and incoming hints."""
from pathlib import Path
import secrets
import time
from types import SimpleNamespace
import unittest
import test_call_signaling as signal_fixture
from luma_continuity.call_control_channel import CallControlChannel
from luma_continuity.native import NativeCalls
from luma_continuity.policy import Journal


class CallControlChannelTests(unittest.TestCase):
    def test_typed_dial_once_incoming_hint_and_no_durable_command(self):
        fixture=signal_fixture.AudioSignalTests();fixture.setUp()
        effects=[];hints=[];channels=[]
        try:
            for i in range(2):
                journal=Journal(fixture.dirs[i]/'continuity.db')
                journal.set_grants(fixture.pins[1-i],['calls.read','calls.control'] if i else [],
                    outgoing_grants=[] if i else ['calls.read','calls.control']);journal.close()
            native=NativeCalls(SimpleNamespace(calls=lambda:[],dial=effects.append),authorized=lambda:fixture.allowed,
                control_authorized=lambda:fixture.allowed,now=time.time)
            for i in range(2):
                channels.append(CallControlChannel(fixture.streams[i],fixture.dirs[i],fixture.pins[1-i],
                    account='account',epoch='e'*32,session='a'*32,incoming=bool(i),authorized=lambda:fixture.allowed,
                    dispatch=fixture.callbacks.put,changed=hints.append,failed=lambda:fixture.failures.append(True),
                    adapter=native if i else None))
            for channel in channels:channel.start()
            def request(capability,payload):
                return dict(version=1,account='account',epoch='e'*32,id=secrets.token_hex(16),
                    capability=capability,expires=int(time.time())+10,payload=payload)
            channels[1].hint(True);fixture.wait(lambda:hints)
            self.assertEqual(hints,[True]);self.assertEqual(effects,[])
            snapshot=channels[0].request(request('calls.read',{'operation':'snapshot'}))
            command=request('calls.control',{'operation':'dial','token':snapshot['result']['dial_token'],'address':'+12025550123'})
            result=channels[0].request(command)
            self.assertEqual(result['result'],{'accepted':True});self.assertEqual(effects,['+12025550123'])
            with self.assertRaises(PermissionError):channels[0].request(command)
            self.assertEqual(effects,['+12025550123'])
            for directory in fixture.dirs:
                journal=Journal(directory/'continuity.db')
                try:self.assertEqual(journal.db.execute('SELECT count(*) FROM requests').fetchone()[0],0)
                finally:journal.close()
        finally:
            for channel in channels:channel.close()
            for channel in channels:
                channel.channel.worker.join(3);channel.executor.shutdown(wait=True,cancel_futures=True)
                self.assertFalse(channel.channel.worker.is_alive())
            self.assertTrue(fixture.doCleanups())

    def paired_channels(self,adapter=None):
        fixture=signal_fixture.AudioSignalTests();fixture.setUp();channels=[];observed=[]
        def cleanup():
            for channel in channels:channel.close()
            for channel in channels:
                channel.channel.worker.join(3);channel.executor.shutdown(wait=True,cancel_futures=True)
                self.assertFalse(channel.channel.worker.is_alive())
            self.assertTrue(fixture.doCleanups())
        self.addCleanup(cleanup)
        for i in range(2):
            journal=Journal(fixture.dirs[i]/'continuity.db')
            journal.set_grants(fixture.pins[1-i],['calls.read'] if i else [],outgoing_grants=[] if i else ['calls.read'])
            journal.close()
        def calls():observed.append('snapshot');return []
        native=adapter or NativeCalls(SimpleNamespace(calls=calls),authorized=lambda:fixture.allowed,now=time.time)
        for i in range(2):
            channels.append(CallControlChannel(fixture.streams[i],fixture.dirs[i],fixture.pins[1-i],
                account='account',epoch='e'*32,session='a'*32,incoming=bool(i),authorized=lambda:fixture.allowed,
                dispatch=fixture.callbacks.put,changed=lambda value:None,failed=lambda:fixture.failures.append(True),
                adapter=native if i else None))
        for channel in channels:channel.start()
        return fixture,channels,observed

    def test_normal_provider_recovers_after_correlated_expiry_denial_on_same_tls(self):
        from luma_continuity.call_provider import CallProvider
        from luma_continuity.policy import Denied
        fixture,channels,observed=self.paired_channels();malformed=[True]
        def exchange(request):
            if malformed:
                malformed.pop();request=dict(request,expires=int(time.time())+30)
            return channels[0].request(request)
        provider=CallProvider(exchange,account='account',epoch='e'*32,authorized=lambda:True,
            control_authorized=lambda:False,subscribe=lambda callback:lambda:None,dispatch=lambda callback:callback())
        self.addCleanup(provider.close)
        with self.assertRaises(Denied):provider._snapshot()
        self.assertEqual(observed,[])
        self.assertTrue(all(not channel.closed for channel in channels))
        result=provider._snapshot()
        self.assertEqual(result['calls'],[]);self.assertEqual(observed,['snapshot'])
        self.assertTrue(all(not channel.closed for channel in channels));self.assertEqual(fixture.failures,[])
        for directory in fixture.dirs:
            journal=Journal(directory/'continuity.db')
            try:self.assertEqual(journal.db.execute('select count(*) from requests').fetchone()[0],0)
            finally:journal.close()

    def test_unknown_native_dispatch_failure_still_closes_channel(self):
        from luma_continuity.call_provider import CallProvider
        observed=[]
        def failing(capability,payload):observed.append('dispatch');raise RuntimeError('synthetic unknown result')
        fixture,channels,_=self.paired_channels(failing)
        provider=CallProvider(channels[0].request,account='account',epoch='e'*32,authorized=lambda:True,
            control_authorized=lambda:False,subscribe=lambda callback:lambda:None,dispatch=lambda callback:callback())
        self.addCleanup(provider.close)
        with self.assertRaises((PermissionError,TimeoutError)):provider._snapshot()
        self.assertEqual(observed,['dispatch']);self.assertTrue(channels[1].closed)

    def test_wrong_session_envelope_still_closes_authenticated_carrier(self):
        fixture,channels,_=self.paired_channels()
        channels[1].channel.send(dict(channels[1].binding,session='b'*32,sequence=0,kind='changed',body={'incoming':False,'active_generations':[]}))
        fixture.wait(lambda:fixture.failures)
        self.assertTrue(channels[0].closed)

    def test_read_grant_loss_still_closes_instead_of_denial_receipt(self):
        from luma_continuity.call_provider import CallProvider
        fixture,channels,observed=self.paired_channels()
        journal=Journal(fixture.dirs[1]/'continuity.db');journal.set_grants(fixture.pins[0],[],outgoing_grants=[]);journal.close()
        provider=CallProvider(channels[0].request,account='account',epoch='e'*32,authorized=lambda:True,
            control_authorized=lambda:False,subscribe=lambda callback:lambda:None,dispatch=lambda callback:callback())
        self.addCleanup(provider.close)
        with self.assertRaises((PermissionError,TimeoutError)):provider._snapshot()
        self.assertEqual(observed,[])
        fixture.wait(lambda:fixture.failures);self.assertTrue(channels[1].closed)


    def test_request_account_mismatch_still_closes_even_with_valid_outer_binding(self):
        fixture,channels,observed=self.paired_channels()
        request=dict(version=1,account='other-account',epoch='e'*32,id=secrets.token_hex(16),
            capability='calls.read',expires=int(time.time())+10,payload={'operation':'snapshot'})
        channels[0].channel.send(dict(channels[0].binding,sequence=0,kind='request',body=request))
        fixture.wait(lambda:fixture.failures)
        self.assertTrue(channels[1].closed);self.assertEqual(observed,[])

    def test_adapter_cannot_label_dispatched_work_as_predispatch_denial(self):
        from luma_continuity.call_receiver import CallRequestDenied
        from luma_continuity.call_provider import CallProvider
        effects=[]
        def failing(capability,payload):effects.append('dispatch');raise CallRequestDenied('synthetic post-dispatch')
        fixture,channels,_=self.paired_channels(failing)
        provider=CallProvider(channels[0].request,account='account',epoch='e'*32,authorized=lambda:True,
            control_authorized=lambda:False,subscribe=lambda callback:lambda:None,dispatch=lambda callback:callback())
        self.addCleanup(provider.close)
        with self.assertRaises((PermissionError,TimeoutError)):provider._snapshot()
        self.assertEqual(effects,['dispatch']);self.assertTrue(channels[1].closed)
