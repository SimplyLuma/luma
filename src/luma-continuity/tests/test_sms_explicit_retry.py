"""Explicit SMS recovery preserves identity and never blindly replays a send."""
import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
if os.environ.get('LUMA_NATIVE_SOURCE'):sys.path.insert(0,os.environ['LUMA_NATIVE_SOURCE'])
from prairie_apps.messages_backend import MessageStore
from prairie_apps.messages_reply import ReplySender
from luma_continuity.client import QueuedMessages
from luma_continuity.native import NativeMessages
from luma_continuity.queue import Outbox

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.calls=[];self.allowed=True
        calls=self.calls
        class Modem:
            def inspect(self):
                class Capability:available=True
                return Capability()
            def send(self,address,body):
                calls.append((address,body))
                if len(calls)==1:raise TimeoutError('synthetic uncertain modem result')
                return 'synthetic'
        self.native_store=MessageStore(root/'native.db');self.addCleanup(self.native_store.close)
        self.native=NativeMessages(self.native_store,ReplySender(root/'native.db',Modem),pair_token='e'*32,authorized=lambda:self.allowed)
        self.client=QueuedMessages(root/'client','a'*64,'b'*32,label='Synthetic',approved=lambda:self.allowed,account='account',store_factory=MessageStore)
        self.store=MessageStore(self.client.store_path);self.addCleanup(self.store.close)
        self.record=self.store.add('+12025550123','Synthetic only',direction='outgoing')
        self.lose_ack=False
    def exchange(self,request):
        payload=dict(request['payload'])
        if request['capability']=='messages.send':payload['operation']=request['id']
        result=self.native(request['capability'],payload)
        if self.lose_ack and request['capability']=='messages.send':
            self.lose_ack=False;raise OSError('synthetic lost acknowledgement')
        return json.loads(json.dumps(dict(state='complete',result=result)))
    def uncertain(self):
        self.client.send_message(self.record.uid);self.client.flush(self.exchange)
        self.assertEqual(self.store.message(self.record.uid).send_status,'uncertain')
    def test_explicit_retry_lost_ack_preserves_one_message_and_deduplicates(self):
        self.uncertain();self.client.flush(self.exchange);self.assertEqual(len(self.calls),1)
        self.client.retry_message(self.record.uid);self.client.retry_message(self.record.uid)
        box=Outbox(self.client.outbox_path)
        self.assertEqual(len(box.pending('a'*64,'b'*32)),1);box.close()
        self.lose_ack=True
        with self.assertRaises(OSError):self.client.flush(self.exchange)
        self.client.flush(self.exchange);self.client.sync_recent(self.exchange)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(len(self.store.thread(self.record.address)),1)
        self.assertEqual(self.store.message(self.record.uid).state,'sent')
        self.assertEqual(self.store.message(self.record.uid).send_status,'')
        self.client.retry_message(self.record.uid);self.assertEqual(len(self.calls),2)
    def test_revocation_and_changed_body_block_retry(self):
        self.uncertain();self.allowed=False
        with self.assertRaises(PermissionError):self.client.retry_message(self.record.uid)
        self.allowed=True
        self.store._connection.execute('UPDATE messages SET body=? WHERE uid=?',('Changed',self.record.uid));self.store._connection.commit()
        with self.assertRaises(ValueError):self.client.retry_message(self.record.uid)
        self.assertEqual(len(self.calls),1)
