#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Host mailbox isolation, descriptor imports and once-only delivery requests.

These are storage/protocol tests. Carrier hardware and signed application
process admission have separate installed gates; a fake carrier proves only
the once-only dispatch contract here.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from prairie_apps.messages_app_rpc import ApplicationMailbox, arguments, payload
from prairie_apps.messages_native_mailbox import NativeMailbox, import_mms
from prairie_apps.messages_backend import MessageStore


class MailboxTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.agent = SimpleNamespace(accounts=SimpleNamespace(root=self.root/'accounts'), services={},
            _native_store_path=lambda: self.root/'messages.db', _schedule_count=lambda: None, agent=None)
        self.mailbox = ApplicationMailbox(self.agent)
        self.addCleanup(self.cleanup_owner)

    def cleanup_owner(self):
        # Tests invoke owner methods on this one thread; production executors
        # create and close their own independent SQLite connections.
        for store in (*self.mailbox.stores.values(), *self.mailbox.delivery_stores.values()): store.close()
        self.mailbox.stores.clear(); self.mailbox.delivery_stores.clear()
        self.mailbox.worker.shutdown(wait=True)
        self.mailbox.transport_worker.shutdown(wait=True)

    def execute(self, operation, *args, **kwargs):
        return self.mailbox.execute('native', operation, json.dumps({'args': args, 'kwargs': kwargs}))

    def test_native_mailbox_uses_existing_data_and_phone_identity(self):
        original = MessageStore(self.root/'messages.db')
        record = original.add('2025550123', 'existing conversation', direction='incoming')
        original.close()
        read = self.execute('message', record.uid)
        self.assertEqual(read.body, 'existing conversation')
        self.assertEqual(self.execute('canonical_address', '+12025550123'), '+12025550123')
        self.assertEqual(self.execute('thread', '+12025550123')[0].uid, record.uid)
        self.assertEqual(self.mailbox.store('native').path, self.root/'messages.db')

    def test_store_api_refuses_sql_arbitrary_paths_and_credentials(self):
        for operation in ('_connection', 'execute', 'attach_file', 'claim_send', 'authorized_token', 'complete_media'):
            with self.subTest(operation=operation), self.assertRaises(ValueError): self.execute(operation, '/etc/passwd')
        for account in ('../accounts', '/tmp/other.db', 'not-an-account'):
            with self.subTest(account=account), self.assertRaises((ValueError, KeyError)): self.mailbox.store(account)

    def test_descriptor_import_preserves_shared_offset_and_roundtrips(self):
        source = self.root/'chosen.txt'; data = b'attachment bytes selected by the person'
        source.write_bytes(data)
        fd = os.open(source, os.O_RDONLY)
        try:
            os.lseek(fd, 7, os.SEEK_SET)
            attachment = self.mailbox.import_attachment('native', '+12025550123', '../chosen.txt', fd)
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 7)
            self.assertEqual(attachment.name, 'chosen.txt')
            result = self.mailbox.attachment('native', attachment.uid, '')
            try: self.assertEqual(os.pread(result, 1000, 0), data)
            finally: os.close(result)
            self.assertNotIn(str(self.root), payload(attachment))
        finally: os.close(fd)
        with self.assertRaises(KeyError): self.mailbox.attachment('native', source.name, '')

    def test_nonregular_descriptor_and_unknown_preview_are_refused(self):
        read, write = os.pipe()
        try:
            with self.assertRaises(ValueError): self.mailbox.import_attachment('native', '+12025550123', 'pipe', read)
        finally: os.close(read); os.close(write)
        with self.assertRaises(KeyError): self.mailbox.attachment('native', 'unknown', '0')

    def test_changed_descriptor_does_not_publish_a_draft_attachment(self):
        source = self.root/'changed.txt'; source.write_bytes(b'original')
        fd = os.open(source, os.O_RDONLY); original_read = os.pread
        def changed(descriptor, size, offset):
            data = original_read(descriptor, size, offset)
            if offset == 0: source.write_bytes(b'changed!')
            return data
        try:
            with patch('prairie_apps.messages_app_rpc.os.pread', side_effect=changed):
                with self.assertRaises(ValueError): self.mailbox.import_attachment('native', '+12025550123', 'changed.txt', fd)
            self.assertEqual(self.execute('draft_attachments', '+12025550123'), ())
        finally: os.close(fd)

    def test_native_delivery_claim_is_used_once_and_explicit_retry_is_required(self):
        sent = []
        self.mailbox.modem = SimpleNamespace(send=lambda address, body: sent.append((address, body)))
        record = self.execute('add', '+12025550123', 'one press', direction='outgoing')
        token = self.execute('authorize_send', record.uid)
        self.assertEqual(self.mailbox.native_send(record.uid, token)['state'], 'sent')
        self.assertEqual(self.mailbox.native_send(record.uid, token)['state'], 'sent')
        self.assertEqual(sent, [('+12025550123', 'one press')])
        with self.assertRaises(ValueError): self.execute('authorize_send', record.uid)
        with self.assertRaises(ValueError): self.execute('authorize_send', record.uid, retry=True)
        self.assertEqual(len(sent), 1)

    def test_uncertain_native_failure_does_not_resend_or_recover_automatically(self):
        attempts = []
        def ambiguous(address, body): attempts.append(body); raise OSError('lost carrier reply')
        self.mailbox.modem = SimpleNamespace(send=ambiguous)
        record = self.execute('add', '+12025550123', 'possibly accepted', direction='outgoing')
        token = self.execute('authorize_send', record.uid)
        self.assertEqual(self.mailbox.native_send(record.uid, token)['state'], 'failed')
        self.execute('recover_interrupted')
        self.mailbox.native_send(record.uid, token)
        self.assertEqual(attempts, ['possibly accepted'])
        retry = self.execute('authorize_send', record.uid, retry=True)
        self.mailbox.native_send(record.uid, retry)
        self.assertEqual(attempts, ['possibly accepted', 'possibly accepted'])

    def test_incoming_record_cannot_authorize_native_send(self):
        record = self.execute('add', '+12025550123', 'received', direction='incoming')
        with self.assertRaises(ValueError): self.execute('authorize_send', record.uid)

    def test_native_import_does_not_manufacture_an_outgoing_carrier_event(self):
        store = self.mailbox.store('native')
        import_mms(store, '/org/ofono/mms/one/message', {'Status': 'sent', 'Sender': '+12025550123'})
        self.assertEqual(store.threads(), ())
        import_mms(store, '/org/ofono/mms/one/message', {'Status': 'received', 'Sender': '+12025550123',
            'Subject': 'received once', 'Recipients': []})
        import_mms(store, '/org/ofono/mms/one/message', {'Status': 'received', 'Sender': '+12025550123',
            'Subject': 'received once', 'Recipients': []})
        self.assertEqual(len(store.thread('+12025550123')), 1)

    def test_native_remote_api_has_no_arbitrary_carrier_command(self):
        for operation in ('send_command', 'send_file', 'mms_send', 'open', 'mms_delete'):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.mailbox.native(operation, json.dumps({'args': ['/etc/passwd'], 'kwargs': {}}))

    def test_carrier_event_overflow_rescans_without_unbounded_futures_or_loss(self):
        other = ApplicationMailbox(SimpleNamespace(accounts=self.agent.accounts, services={}, agent=None,
            _native_store_path=lambda: self.root/'overflow.db', _schedule_count=lambda: None))
        cache = {f'/org/ofono/mms/one/{n:04}': {'Status': 'received', 'Sender': '+12025550123',
                 'Subject': f'message {n}', 'Recipients': []} for n in range(180)}
        other.mms = SimpleNamespace(_messages=cache, cached_message=lambda path: cache.get(path))
        gate = threading.Event(); started = threading.Event()
        other.worker.submit(lambda: (started.set(), gate.wait(5)))
        self.assertTrue(started.wait(2))
        for path, properties in cache.items(): other.native_arrived(path, properties)
        self.assertLessEqual(len(other.native_pending), 64)
        self.assertIsNotNone(other.native_scan)
        # Exactly the initial blocker and one importer are queued/running;
        # 180 events do not manufacture 180 worker futures.
        self.assertEqual(other.worker._work_queue.qsize(), 1)
        gate.set()
        deadline = time.monotonic() + 10
        while other.native_import_running and time.monotonic() < deadline: time.sleep(.01)
        self.assertFalse(other.native_import_running)
        count = other.worker.submit(lambda: len(other.store('native').thread('+12025550123'))).result(timeout=5)
        self.assertEqual(count, 180)
        other.worker.submit(lambda: [store.close() for store in other.stores.values()]).result(timeout=5)
        other.stores.clear()
        other.worker.shutdown(wait=True); other.transport_worker.shutdown(wait=True)

    def test_legacy_async_api_admits_at_most_four_calls(self):
        from prairie_apps.messages_agent import MessagesAgent
        owner = MessagesAgent.__new__(MessagesAgent)
        from concurrent.futures import ThreadPoolExecutor
        owner.worker = ThreadPoolExecutor(max_workers=2)
        owner._call_slots = threading.BoundedSemaphore(4)
        gate = threading.Event(); refused = []
        invocation = SimpleNamespace(return_dbus_error=lambda name, message: refused.append(name))
        try:
            for _ in range(4): self.assertTrue(owner._submit_call(invocation, lambda: gate.wait(5)))
            self.assertFalse(owner._submit_call(invocation, lambda: None))
            self.assertEqual(refused, ['org.projectluma.Messages.Agent1.Error.Busy'])
            self.assertLessEqual(owner.worker._work_queue.qsize(), 4)
        finally: gate.set(); owner.worker.shutdown(wait=True)


class ProtocolTest(unittest.TestCase):
    def test_legacy_luma_input_is_bounded_before_decode_or_provider_access(self):
        from prairie_apps.messages_agent import MessagesAgent
        from gi.repository import GLib
        owner = MessagesAgent.__new__(MessagesAgent)
        refused = []
        invocation = SimpleNamespace(return_dbus_error=lambda name, message: refused.append(name))
        # Authentication is covered separately by signed installed-process tests.
        with patch('prairie_apps.messages_app_rpc.authenticate_caller'), \
             patch.object(owner, '_provider', side_effect=AssertionError('oversized input reached provider')), \
             patch('prairie_apps.messages_agent.json.loads', side_effect=AssertionError('oversized input reached decoder')):
            owner._call(None, ':1.2', None, None, 'Luma',
                        GLib.Variant('(sss)', ('account', 'command', '\u00e9'*65537)), invocation)
        self.assertEqual(refused, ['org.projectluma.Messages.Agent1.Error.InvalidInput'])

    def test_account_removal_preparation_failure_preserves_provider_and_capacity(self):
        from prairie_apps.messages_agent import MessagesAgent
        owner = MessagesAgent.__new__(MessagesAgent)
        owner._call_slots = threading.BoundedSemaphore(4)
        provider = object(); owner.services = {'account': provider}
        owner.accounts = SimpleNamespace(list=lambda **_: (_ for _ in ()).throw(OSError('private path')))
        refused = []
        invocation = SimpleNamespace(return_dbus_error=lambda name, message: refused.append((name, message)))
        for _ in range(6): owner._remove(invocation, 'account', True)
        self.assertIs(owner.services['account'], provider)
        self.assertEqual(len(refused), 6)
        self.assertTrue(all(name.endswith('.Unavailable') and 'private' not in message for name, message in refused))
        for _ in range(4): self.assertTrue(owner._call_slots.acquire(blocking=False))
        self.assertFalse(owner._call_slots.acquire(blocking=False))
        for _ in range(4): owner._call_slots.release()

    def test_legacy_deep_json_is_refused_without_dispatching_to_provider(self):
        from prairie_apps.messages_agent import MessagesAgent
        from gi.repository import GLib
        owner = MessagesAgent.__new__(MessagesAgent)
        refused = []
        invocation = SimpleNamespace(return_dbus_error=lambda name, message: refused.append(name))
        nested = '[' * 3000 + '0' + ']' * 3000
        with patch('prairie_apps.messages_app_rpc.authenticate_caller'), \
             patch.object(owner, '_provider', return_value=object()), \
             patch.object(owner, '_luma', side_effect=AssertionError('invalid JSON dispatched')):
            owner._call(None, ':1.2', None, None, 'Luma',
                GLib.Variant('(sss)', ('account', 'command', nested)), invocation)
        self.assertEqual(refused, ['org.projectluma.Messages.Agent1.Error.InvalidInput'])

    def test_invalid_input_bounds_are_enforced(self):
        invalid = [json.dumps({'args': [], 'kwargs': {}, 'sql': 'delete'}),
                   json.dumps({'args': [list(range(257))], 'kwargs': {}}),
                   '{"args":[NaN],"kwargs":{}}',
                   json.dumps({'args': ['x'*131073], 'kwargs': {}})]
        for text in invalid:
            with self.subTest(length=len(text)), self.assertRaises(ValueError): arguments(text)
        deep = None
        for _ in range(12): deep = [deep]
        with self.assertRaises(ValueError): arguments(json.dumps({'args': [deep], 'kwargs': {}}))
        self.assertEqual(arguments('{"args":[1,true,null],"kwargs":{}}'), ([1, True, None], {}))

    def test_preview_record_does_not_disclose_host_path(self):
        encoded = payload({'preview': Path('/private/host/path'), 'result': 'ready'})
        self.assertNotIn('/private/host', encoded)
        self.assertIn('$preview', encoded)


if __name__ == '__main__': unittest.main()
