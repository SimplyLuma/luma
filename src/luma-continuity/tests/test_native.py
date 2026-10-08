"""Run with LUMA_NATIVE_SOURCE pointing to the reviewed canonical prairie-core.
Only synthetic temporary stores and an injected fake modem are opened.
"""
import base64
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from luma_continuity.native import NativeMessages

SOURCE = os.environ.get("LUMA_NATIVE_SOURCE")
if not SOURCE:
    installed = importlib.util.find_spec("prairie_apps")
    if installed and installed.submodule_search_locations:
        SOURCE = str(Path(next(iter(installed.submodule_search_locations))).parent)
if SOURCE:
    sys.path.insert(0, SOURCE)
    from prairie_apps.messages_backend import MessageStore, MessagingCapability
    from prairie_apps.messages_reply import ReplySender


@unittest.skipUnless(SOURCE, "set LUMA_NATIVE_SOURCE to canonical prairie-core source")
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MessageStore(self.root / "messages.db")
        self.sent = []
        calls = self.sent
        class Modem:
            def inspect(self):
                class Capability: available = True
                return Capability()
            def send(self, address, body): calls.append((address, body)); return "synthetic-modem-id"
        self.allowed = True
        self.adapter = NativeMessages(self.store, ReplySender(self.store.path, Modem),
            pair_token="e" * 32, authorized=lambda: self.allowed)

    def tearDown(self): self.store.close(); self.temp.cleanup()

    def test_native_send_deduplicates(self):
        request = {"address": "+12025550123", "body": "Synthetic test only", "operation": "f" * 32}
        result = self.adapter("messages.send", request)
        self.assertEqual(result["state"], "sent")
        self.assertEqual(self.adapter("messages.send", request), result)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.store.message(result["uid"]).body, request["body"])

    def test_native_attachment_read_has_no_path(self):
        # Valid tiny PNG header accepted by the canonical store's content checks.
        import struct, zlib
        def chunk(kind, body): return struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind + body))
        data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b'')
        source = self.root / "fixture.png"; source.write_bytes(data)
        attachment = self.store.attach_file("+12025550123", source)
        message = self.store.add("+12025550123", "Synthetic picture", direction="incoming", attachment_uids=(attachment.uid,))
        response = self.adapter("messages.read", {"address": message.address, "limit": 10})
        row = response["messages"][0]
        self.assertNotIn("transport_id", row)
        self.assertNotIn("storage_key", row["attachments"][0])
        response = self.adapter("messages.read", {"message": message.uid, "attachment": attachment.uid, "offset": 0, "length": 100})
        self.assertEqual(base64.b64decode(response["data"]), data)
        with self.assertRaises(ValueError):
            self.adapter("messages.read", {"message": message.uid, "attachment": "../fixture.png", "offset": 0, "length": 100})

    def test_revoked_native_dispatch(self):
        self.allowed = False
        with self.assertRaises(PermissionError):
            self.adapter("messages.send", {"address": "+12025550123", "body": "Synthetic", "operation": "f" * 32})
        self.assertEqual(self.sent, [])

    def test_native_slice_over_encrypted_relay_with_lost_ack(self):
        import queue
        import threading
        from luma_continuity import transport
        from luma_continuity.relay import TLSRelayStream
        from luma_continuity.policy import Journal
        from luma_continuity.queue import Outbox
        from luma_continuity.bootstrap import create_identity
        phone_dir, desktop_dir = self.root / "phone", self.root / "desktop"
        phone_pin = create_identity(phone_dir); desktop_pin = create_identity(desktop_dir)
        phone_context = transport.context(phone_dir / "device.pem", phone_dir / "device.key", desktop_dir / "device.pem", server=True)
        desktop_context = transport.context(desktop_dir / "device.pem", desktop_dir / "device.key", phone_dir / "device.pem", server=False)
        epoch = "b" * 32
        outbox = Outbox(desktop_dir / "queue.db")
        try:
            operation = outbox.enqueue(phone_pin, epoch, "messages.send",
                {"address": "+12025550123", "body": "Synthetic encrypted native SMS"})
            failures = []
            captured = []
            for reconnect in (False, True):
                a, b = queue.Queue(), queue.Queue()
                class Endpoint:
                    def __init__(self, incoming, outgoing): self.incoming, self.outgoing = incoming, outgoing
                    def send(self, data): captured.append(data); self.outgoing.put(data)
                    def recv(self, timeout): return self.incoming.get(timeout=timeout)
                    def close(self): pass
                client = TLSRelayStream(Endpoint(a, b), desktop_context, phone_pin, server=False)
                def server():
                    store = MessageStore(self.store.path)
                    journal = Journal(phone_dir / "journal.db")
                    adapter = NativeMessages(store, self.adapter.sender, pair_token="e" * 32, authorized=lambda: True)
                    try:
                        if not reconnect: journal.approve(desktop_pin, epoch, ["messages.send"])
                        stream = TLSRelayStream(Endpoint(b, a), phone_context, desktop_pin, server=True)
                        stream.handshake()
                        incoming = transport.receive(stream)
                        from luma_continuity.session import Receiver
                        response = Receiver(journal, {"messages.send": adapter}).handle(desktop_pin, incoming)
                        transport.send(stream, response)
                    except Exception as error: failures.append(error)
                    finally: journal.close(); store.close()
                worker = threading.Thread(target=server); worker.start()
                client.handshake()
                pending = outbox.pending(phone_pin, epoch)
                self.assertEqual(len(pending), 1)
                transport.send(client, pending[0])
                receipt = transport.receive(client)
                if reconnect: outbox.acknowledge(operation, receipt)
                # First acknowledgement deliberately lost before durable local receipt.
                worker.join(5)
                self.assertFalse(worker.is_alive()); self.assertEqual(failures, [])
            self.assertEqual(outbox.pending(phone_pin, epoch), [])
            self.assertEqual(len(self.sent), 1)
            self.assertNotIn(b"Synthetic encrypted native SMS", b"".join(captured))
        finally: outbox.close()

    def test_native_picture_send_is_queued_not_delivered(self):
        from prairie_apps.messages_mms import MmsMessagingTransport, MmsCapability
        import struct, zlib
        def chunk(kind, body): return struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind + body))
        image = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b'')
        calls = []
        class Mms:
            validate_parts = staticmethod(MmsMessagingTransport.validate_parts)
            def inspect(self): return MmsCapability(True, "Synthetic", "/org/ofono/mms/test", 512*1024, 5)
            def send(self, recipients, parts):
                calls.append((recipients, [Path(part[2]).read_bytes() for part in parts]))
                return "/org/ofono/mms/test/message1"
        self.adapter.mms_transport = Mms()
        request = {"address": "+12025550123", "body": "Synthetic picture", "operation": "f" * 32,
                   "attachments": [{"name": "fixture.png", "data": base64.b64encode(image).decode()}]}
        response = self.adapter("messages.send", request)
        self.assertEqual(response["state"], "queued")
        self.assertEqual(calls[0][1], [b"Synthetic picture", image])
        self.assertEqual(self.adapter("messages.send", request)["state"], "unknown")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.message(response["uid"]).state, "queued")

    def test_contacts_use_native_records_and_revoke(self):
        from prairie_apps.eds_backend import ContactRecord
        from luma_continuity.native import NativeContacts
        adapter = NativeContacts(lambda query: (ContactRecord("fixture", "Synthetic Contact", "+12025550123"),), authorized=lambda: self.allowed)
        result = adapter("contacts.read", {"search": "", "limit": 10})
        self.assertEqual(result["contacts"][0]["name"], "Synthetic Contact")
        self.allowed = False
        with self.assertRaises(PermissionError): adapter("contacts.read", {"search": "", "limit": 10})

    def test_calls_use_current_native_identity_and_one_shot(self):
        from prairie_apps.phone_backend import NativeCall, CallPhase
        from luma_continuity.native import NativeCalls
        calls = [NativeCall("native-fixture", "+12025550123", "incoming", CallPhase.INCOMING, started_at=123)]
        actions = []
        class Phone:
            def calls(self): return tuple(calls)
            def accept(self, uid): actions.append(("answer", uid))
            def hangup(self, uid): actions.append(("hangup", uid))
        adapter = NativeCalls(Phone(), authorized=lambda: self.allowed, control_authorized=lambda:self.allowed, now=lambda: 100)
        token = adapter("calls.read", {"operation": "snapshot"})["calls"][0]["id"]
        self.assertNotEqual(token, "native-fixture")
        adapter("calls.control", {"operation": "answer", "call": token})
        with self.assertRaises(PermissionError): adapter("calls.control", {"operation": "answer", "call": token})
        token = adapter("calls.read", {"operation": "snapshot"})["calls"][0]["id"]
        calls[0] = NativeCall("native-fixture", "+12025550123", "incoming", CallPhase.ACTIVE, started_at=123)
        with self.assertRaises(PermissionError): adapter("calls.control", {"operation": "answer", "call": token})
        self.assertEqual(actions, [("answer", "native-fixture")])

    def test_shared_ui_provider_queues_offline_and_uses_stable_id(self):
        from luma_continuity.client import QueuedMessages
        provider = QueuedMessages(self.root / 'device-cache', 'a'*64, 'b'*32,
            label='Synthetic phone', approved=lambda: self.allowed, store_factory=MessageStore)
        cache = MessageStore(provider.store_path)
        try:
            record = cache.add('+12025550123', 'Synthetic queued UI message', direction='outgoing')
            provider.send_message(record.uid); provider.send_message(record.uid)
            requests = []
            def exchange(request):
                requests.append(request)
                return {'state':'complete','result':{'uid':'remote-fixture','state':'sent'}}
            provider.flush(exchange)
            self.assertEqual(len(requests),1)
            self.assertEqual(requests[0]['id'],record.uid)
            self.assertEqual(cache.message(record.uid).state,'sent')
            provider.flush(exchange)
            self.assertEqual(len(requests),1)
        finally: cache.close()

    def test_shared_provider_flush_over_real_paired_socket(self):
        import socket, threading
        from luma_continuity.bootstrap import create_identity, approve_peer
        from luma_continuity.local import PairedExchange, bounded_stream
        from luma_continuity.client import QueuedMessages
        from luma_continuity.policy import Journal
        from luma_continuity.session import Receiver
        from luma_continuity import transport
        desktop, phone = self.root / 'desktop-peer', self.root / 'phone-peer'
        desktop_pin, phone_pin = create_identity(desktop), create_identity(phone)
        epoch = 'd' * 32
        approve_peer(desktop, phone / 'device.pem', phone_pin, epoch, ['messages.send'])
        approve_peer(phone, desktop / 'device.pem', desktop_pin, epoch, ['messages.send'])
        provider = QueuedMessages(desktop / 'cache', phone_pin, epoch, label='Synthetic test phone',
                                  approved=lambda: self.allowed, store_factory=MessageStore)
        cache = MessageStore(provider.store_path)
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(3)
        port = listener.getsockname()[1]
        failures = []
        def serve():
            journal = Journal(phone / 'continuity.db')
            store = MessageStore(self.store.path)
            # Only modem is synthetic; both stores, sender and network are real.
            sender = ReplySender(store.path, lambda: self.adapter.sender.transport_factory())
            adapter = NativeMessages(store, sender, pair_token=epoch, authorized=lambda: self.allowed)
            try:
                tls = transport.context(phone / 'device.pem', phone / 'device.key',
                    phone / 'peers' / (desktop_pin + '.pem'), server=True)
                raw, _ = listener.accept()
                with raw, bounded_stream(raw, tls, server=True, timeout=2) as stream:
                    Receiver(journal, {'messages.send': adapter}).serve_one(stream,
                        transport.authenticate(stream, desktop_pin))
            except Exception as error: failures.append(error)
            finally: journal.close(); store.close(); listener.close()
        worker = threading.Thread(target=serve)
        try:
            record = cache.add('+12025550123', 'Synthetic socket test', direction='outgoing')
            provider.send_message(record.uid)
            worker.start()
            provider.flush(PairedExchange(desktop, phone_pin, epoch, '127.0.0.1', port, timeout=2))
            worker.join(4)
            self.assertEqual(failures, []); self.assertFalse(worker.is_alive())
            self.assertEqual(cache.message(record.uid).state, 'sent')
            self.assertEqual(self.sent, [('+12025550123', 'Synthetic socket test')])
            provider.flush(lambda _: self.fail('completed send must not reconnect'))
        finally:
            listener.close()
            if worker.ident is not None: worker.join(4)
            cache.close()

    def test_shared_provider_imports_native_incoming_without_duplicate(self):
        from luma_continuity.client import QueuedMessages
        from luma_continuity.policy import Journal
        from luma_continuity.session import Receiver
        incoming = self.store.add('+12025550123', 'Synthetic incoming phone message', direction='incoming')
        provider = QueuedMessages(self.root / 'device-cache', 'a'*64, 'b'*32,
            label='Synthetic phone', approved=lambda: self.allowed, store_factory=MessageStore)
        journal = Journal(self.root / 'receiver.db')
        try:
            journal.approve('c'*64, 'b'*32, ['messages.read'])
            receiver = Receiver(journal, {'messages.read':self.adapter})
            import json
            exchange = lambda request: json.loads(json.dumps(receiver.handle('c'*64, request)))
            self.assertEqual(provider.sync_recent(exchange)['imported'],1)
            self.assertEqual(provider.sync_recent(exchange)['imported'],0)
            cache = MessageStore(provider.store_path)
            try:
                self.assertEqual(len(cache.thread(incoming.address)),1)
                self.assertEqual(cache.thread(incoming.address)[0].body,incoming.body)
                self.assertNotEqual(cache.path,self.store.path)
            finally: cache.close()
        finally: journal.close()
