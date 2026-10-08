"""Real loopback TLS and journals; synthetic records, no device services."""
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from luma_continuity.bootstrap import create_identity, approve_peer
from luma_continuity.local import PairedExchange, bounded_stream
from luma_continuity.policy import Journal, Denied
from luma_continuity.session import Receiver
from luma_continuity import transport


class LocalExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client, self.server = (Path(self.temp.name) / name for name in ('client', 'server'))
        self.client_pin = create_identity(self.client)
        self.server_pin = create_identity(self.server)
        self.epoch = 'e' * 32
        approve_peer(self.client, self.server / 'device.pem', self.server_pin, self.epoch, ['messages.read'])
        approve_peer(self.server, self.client / 'device.pem', self.client_pin, self.epoch, ['messages.read'])

    def request(self):
        return dict(version=1, epoch=self.epoch, id='a' * 32, account=None,
                    capability='messages.read', expires=int(time.time()) + 60, payload={})

    def serve(self, handler, count=1):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(2); listener.settimeout(3)
        port = listener.getsockname()[1]
        errors = []
        def worker():
            try:
                tls = transport.context(self.server / 'device.pem', self.server / 'device.key',
                    self.server / 'peers' / (self.client_pin + '.pem'), server=True)
                journal = Journal(self.server / 'continuity.db')
                try:
                    receiver = Receiver(journal, {'messages.read': handler})
                    for _ in range(count):
                        raw, _ = listener.accept()
                        with raw, bounded_stream(raw, tls, server=True, timeout=2) as stream:
                            peer = transport.authenticate(stream, self.client_pin)
                            receiver.serve_one(stream, peer)
                finally: journal.close()
            except Exception as error: errors.append(error)
            finally: listener.close()
        thread = threading.Thread(target=worker); thread.start()
        self.addCleanup(thread.join, 4)
        return PairedExchange(self.client, self.server_pin, self.epoch, '127.0.0.1', port, timeout=2), thread, errors

    def test_real_tls_reconnect_uses_durable_receipt(self):
        calls = []
        exchange, thread, errors = self.serve(lambda *args: calls.append(args) or {'records': []}, count=2)
        request = self.request()
        first = exchange(request)
        self.assertEqual(first, exchange(request))
        thread.join(4)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(first, {'state': 'complete', 'result': {'records': []}})
        self.assertEqual(len(calls), 1)

    def test_invitation_pairing_real_tls_and_reconnect(self):
        from luma_continuity.pairing import create_offer, accept_offer, finish_offer
        offer = create_offer(self.client, a_to_b=['messages.read'], b_to_a=[])
        response = accept_offer(self.server, offer, verified_a_pin=self.client_pin,
                                a_to_b=['messages.read'], b_to_a=[], replace=True)
        result = finish_offer(self.client, response, verified_b_pin=self.server_pin, replace=True)
        self.epoch = result['epoch']
        calls = []
        exchange, thread, errors = self.serve(lambda *args: calls.append(args) or {'records': []}, count=2)
        request = self.request()
        self.assertEqual(exchange(request), exchange(request))
        thread.join(4)
        self.assertEqual(errors, []); self.assertEqual(len(calls), 1)
        reverse = PairedExchange(self.server, self.client_pin, self.epoch, '127.0.0.1', 1)
        with self.assertRaises(Denied): reverse(self.request())

    def test_account_revoked_during_tls_handshake_sends_no_request(self):
        from unittest.mock import patch
        calls=[]
        exchange, thread, errors=self.serve(lambda *_: calls.append('sent') or {})
        current=[True]
        exchange.authorized=lambda: current[0]
        real=transport.authenticate
        def authenticate(stream, pin):
            result=real(stream,pin)
            if pin == self.server_pin: current[0]=False
            return result
        with patch('luma_continuity.transport.authenticate',side_effect=authenticate):
            with self.assertRaises(Denied): exchange(self.request())
            thread.join(4)
        self.assertEqual(calls,[])

    def test_revoked_before_connect_and_wrong_epoch_fail_closed(self):
        exchange = PairedExchange(self.client, self.server_pin, self.epoch, '127.0.0.1', 1)
        wrong = self.request(); wrong['epoch'] = 'b' * 32
        with self.assertRaises(Denied): exchange(wrong)
        journal = Journal(self.client / 'continuity.db')
        journal.revoke(self.server_pin); journal.close()
        with self.assertRaises(Denied): exchange(self.request())

    def test_local_revocation_withholds_inflight_private_result(self):
        def handler(*_):
            journal = Journal(self.client / 'continuity.db')
            journal.revoke(self.server_pin); journal.close()
            return {'private': 'synthetic'}
        exchange, thread, errors = self.serve(handler)
        with self.assertRaises(Denied): exchange(self.request())
        thread.join(4); self.assertEqual(errors, [])

    def test_silent_tls_peer_is_bounded(self):
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1)
        def silent():
            with listener:
                raw, _ = listener.accept()
                with raw: time.sleep(.4)
        thread = threading.Thread(target=silent); thread.start()
        exchange = PairedExchange(self.client, self.server_pin, self.epoch, '127.0.0.1',
                                  listener.getsockname()[1], timeout=.1)
        started = time.monotonic()
        with self.assertRaises((OSError, EOFError)): exchange(self.request())
        self.assertLess(time.monotonic() - started, 1)
        thread.join(2)

    def test_revoke_waits_for_admitted_send(self):
        from unittest.mock import patch
        entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
        original_send = transport.send
        def paused_send(stream, value):
            if 'capability' in value:
                entered.set()
                if not release.wait(2): raise TimeoutError('test send release')
            return original_send(stream, value)
        exchange, server_thread, errors = self.serve(lambda *_: {'records': []})
        outcomes, worker_errors = [], []
        def call():
            try: outcomes.append(exchange(self.request()))
            except Denied: outcomes.append('revoked')
            except Exception as error: worker_errors.append(error)
        def revoke():
            journal = Journal(self.client / 'continuity.db')
            try: journal.revoke(self.server_pin); revoked.set()
            finally: journal.close()
        with patch('luma_continuity.local.transport.send', side_effect=paused_send):
            worker = threading.Thread(target=call); worker.start()
            self.addCleanup(worker.join, 4)
            self.addCleanup(release.set)
            self.assertTrue(entered.wait(2))
            revoker = threading.Thread(target=revoke); revoker.start()
            self.addCleanup(revoker.join, 4)
            self.assertFalse(revoked.wait(.1))
            release.set(); worker.join(4); revoker.join(4); server_thread.join(4)
        self.assertTrue(revoked.is_set()); self.assertEqual(errors, [])
        self.assertEqual(worker_errors, []); self.assertEqual(len(outcomes), 1)
        self.assertFalse(worker.is_alive()); self.assertFalse(revoker.is_alive())
        with self.assertRaises(Denied): exchange(self.request())
