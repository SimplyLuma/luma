"""Real inner TLS and durable journal, synthetic opaque carrier and records."""
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from luma_continuity.bootstrap import create_identity, approve_peer
from luma_continuity import transport
from luma_continuity.policy import Journal, Denied
from luma_continuity.relay import TLSRelayStream
from luma_continuity.relay_exchange import PairedRelayExchange
from luma_continuity.session import Receiver


class Endpoint:
    def __init__(self, incoming, outgoing): self.incoming,self.outgoing=incoming,outgoing
    def send(self,data): self.outgoing.put(data)
    def recv(self,timeout):
        value=self.incoming.get(timeout=timeout)
        if value is None: raise EOFError('closed')
        return value
    def close(self): self.outgoing.put(None);self.incoming.put(None)


class RelayExchangeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);self.client=root/'client';self.server=root/'server'
        self.client_pin=create_identity(self.client);self.server_pin=create_identity(self.server)
        self.epoch='e'*32
        approve_peer(self.client,self.server/'device.pem',self.server_pin,self.epoch,['messages.read'])
        approve_peer(self.server,self.client/'device.pem',self.client_pin,self.epoch,['messages.read'])
        self.calls=[];self.opens=0;self.errors=[];self.threads=[]
        self.allowed=True;self.drop=False;self.revoke_in_handler=False
        self.exchange=PairedRelayExchange(self.client,self.server_pin,self.epoch,
            websocket_factory=self.open,authorized=lambda:self.allowed,timeout=2)
        self.addCleanup(self.finish)

    def finish(self):
        self.exchange.close()
        for thread in self.threads: thread.join(3)

    def request(self,uid='a'*32):
        return dict(version=1,epoch=self.epoch,id=uid,account=None,capability='messages.read',expires=int(time.time())+60,payload={})

    def open(self):
        self.opens+=1;a,b=queue.Queue(),queue.Queue()
        client,server=Endpoint(a,b),Endpoint(b,a)
        def worker():
            journal=Journal(self.server/'continuity.db')
            def handler(*_args):
                self.calls.append('read')
                if self.revoke_in_handler:
                    local=Journal(self.client/'continuity.db');local.revoke(self.server_pin);local.close()
                return {'records':[]}
            try:
                tls=transport.context(self.server/'device.pem',self.server/'device.key',self.client/'device.pem',server=True)
                stream=TLSRelayStream(server,tls,self.client_pin,server=True,timeout=2);stream.handshake()
                receiver=Receiver(journal,{'messages.read':handler})
                while True:
                    stream.wait_for_request()
                    request=transport.receive(stream)
                    result=receiver.handle(self.client_pin,request)
                    if self.drop: self.drop=False;server.close();break
                    transport.send(stream,result)
            except (EOFError,queue.Empty): pass
            except Exception as error:self.errors.append(type(error).__name__)
            finally:journal.close();server.close()
        thread=threading.Thread(target=worker);thread.start();self.threads.append(thread)
        return client

    def test_reuses_session_and_deduplicates_operation(self):
        request=self.request()
        first=self.exchange(request)
        self.assertEqual(self.exchange(dict(request)),first)
        self.assertEqual(self.exchange(self.request('b'*32)),first)
        self.assertEqual(self.opens,1);self.assertEqual(len(self.calls),2)

    def test_lost_receipt_does_not_retry_and_reconnect_recovers_same_id(self):
        self.drop=True;request=self.request()
        with self.assertRaises(EOFError):self.exchange(request)
        self.assertEqual(self.opens,1);self.assertEqual(len(self.calls),1)
        result=self.exchange(dict(request))
        self.assertEqual(result['state'],'complete')
        self.assertEqual(self.opens,2);self.assertEqual(len(self.calls),1)

    def test_revoke_withholds_inflight_receipt(self):
        self.revoke_in_handler=True
        with self.assertRaises(Denied):self.exchange(self.request())
        self.assertEqual(self.opens,1)
        with self.assertRaises(Denied):self.exchange(self.request())
        self.assertEqual(self.opens,1)

    def test_account_stale_does_not_open_and_closed_exchange_does_not_reopen(self):
        self.allowed=False
        with self.assertRaises(Denied):self.exchange(self.request())
        self.assertEqual(self.opens,0)
        self.allowed=True;self.exchange.close()
        with self.assertRaises(PermissionError):self.exchange(self.request())
        self.assertEqual(self.opens,0)

    def test_invalidation_fences_session_open_in_progress(self):
        original=self.exchange.websocket_factory
        def obsolete():
            result=original();self.exchange.invalidate();return result
        self.exchange.websocket_factory=obsolete
        with self.assertRaises(PermissionError):self.exchange(self.request())
        self.assertEqual(self.calls,[])


if __name__=='__main__': unittest.main()
