"""Real loopback mutual TLS, explicit audio grants, ephemeral signaling only."""
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from luma_continuity.bootstrap import create_identity,approve_peer
from luma_continuity.call_signaling import PairedAudioSignals
from luma_continuity import transport


class AudioSignalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.dirs=[Path(self.tmp.name)/name for name in ('client','server')]
        self.pins=[create_identity(p) for p in self.dirs]
        for i in range(2):approve_peer(self.dirs[i],self.dirs[1-i]/'device.pem',self.pins[1-i],
            'e'*32,['calls.audio'] if i else [],account='account',outgoing_grants=[] if i else ['calls.audio'])
        contexts=[transport.context(p/'device.pem',p/'device.key',p/'peers'/(self.pins[1-i]+'.pem'),server=bool(i)) for i,p in enumerate(self.dirs)]
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(3)
        holder=[];errors=[]
        def accept():
            try:
                raw,_=listener.accept();raw.settimeout(3);holder.append(contexts[1].wrap_socket(raw,server_side=True))
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=accept);thread.start()
        client=contexts[0].wrap_socket(socket.create_connection(listener.getsockname(),timeout=3))
        thread.join(4);listener.close();self.assertFalse(errors);self.assertEqual(len(holder),1)
        self.streams=[client,holder[0]];self.channels=[];self.allowed=True
        self.callbacks=queue.Queue();self.received=[[],[]];self.failures=[]
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for c in self.channels:c.close()
        for c in self.channels:
            if c.worker.ident is not None:c.worker.join(2)
        for stream in self.streams:stream.close()

    def channel(self,i,session='s'):
        c=PairedAudioSignals(self.streams[i],self.dirs[i],self.pins[1-i],account='account',epoch='e'*32,
            call='c'*32,session='a'*32 if session=='s' else 'b'*32,incoming=bool(i),authorized=lambda:self.allowed,
            receive=self.received[i].append,failed=lambda:self.failures.append(i),dispatch=self.callbacks.put)
        self.channels.append(c);return c

    def wait(self,predicate):
        deadline=time.monotonic()+4
        while not predicate():
            if time.monotonic()>deadline:raise AssertionError('signal timeout')
            try:self.callbacks.get(timeout=.02)()
            except queue.Empty:pass

    def test_bidirectional_signals_over_real_mutual_tls(self):
        a,b=self.channel(0),self.channel(1);a.start();b.start()
        offer={'type':'offer','sdp':'synthetic'};answer={'type':'answer','sdp':'synthetic'}
        a.send(offer);b.send(answer)
        self.wait(lambda:all(self.received))
        self.assertEqual(self.received,[[answer],[offer]]);self.assertEqual(self.failures,[])

    def test_different_call_session_never_dispatches(self):
        a,b=self.channel(0),self.channel(1,'different');a.start();b.start();a.send({'type':'offer','sdp':'synthetic'})
        self.wait(lambda:self.failures)
        self.assertEqual(self.received,[[],[]])

    def test_permission_loss_before_dispatch_discards_received_signal(self):
        a,b=self.channel(0),self.channel(1);a.start();b.start();a.send({'type':'offer','sdp':'synthetic'})
        deadline=time.monotonic()+3
        while self.callbacks.empty() and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(self.callbacks.empty());self.allowed=False
        self.wait(lambda:self.failures);self.assertEqual(self.received,[[],[]])

    def test_wrong_pin_and_missing_consent_reject_before_threads(self):
        with self.assertRaises(PermissionError):
            PairedAudioSignals(self.streams[0],self.dirs[0],'f'*64,account='account',epoch='e'*32,
                call='c'*32,session='a'*32,incoming=False,authorized=lambda:True,receive=lambda _:None,
                failed=lambda:None,dispatch=self.callbacks.put)
        self.allowed=False
        with self.assertRaises(PermissionError):self.channel(0)
