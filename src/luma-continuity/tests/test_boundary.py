import copy
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from luma_continuity.policy import Journal, Denied
from luma_continuity import transport

PEER = "a" * 64
EPOCH = "b" * 32


def request():
    return dict(version=1, epoch=EPOCH, id="c" * 32, account=None,
                capability="messages.send", expires=1100, payload={"body": "synthetic"})


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.db"
        self.journal = Journal(self.path)
        self.calls = []
        self.handler = lambda cap, payload: self.calls.append(payload) or {"sent": True}

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def approve(self, **kwargs):
        self.journal.approve(PEER, EPOCH, ["messages.send"], **kwargs)

    def run_request(self, value=None, peer=PEER):
        return self.journal.dispatch(peer, value or request(), self.handler, now=1000)

    def test_unpaired_wrong_device_and_capability(self):
        with self.assertRaises(Denied): self.run_request()
        self.approve()
        with self.assertRaises(Denied): self.run_request(peer="d" * 64)
        value = request(); value["capability"] = "screen.control"
        with self.assertRaises(Denied): self.run_request(value)
        self.assertEqual(self.calls, [])

    def test_revoked_does_not_return_cached_private_result(self):
        self.approve(); self.run_request(); self.journal.revoke(PEER)
        with self.assertRaises(Denied): self.run_request()
        self.assertEqual(len(self.calls), 1)

    def test_wrong_account(self):
        self.approve(account="issuer|subject")
        with self.assertRaises(Denied): self.run_request()
        value = request(); value["account"] = "issuer|subject"
        self.run_request(value)
        self.assertEqual(len(self.calls), 1)

    def test_duplicate_reconnect_restart(self):
        self.approve(); first = self.run_request()
        self.journal.close(); self.journal = Journal(self.path)
        self.assertEqual(self.run_request(), first)
        self.assertEqual(len(self.calls), 1)

    def test_conflicting_replay(self):
        self.approve(); self.run_request()
        value = request(); value["payload"]["body"] = "changed"
        with self.assertRaises(Denied): self.run_request(value)
        self.assertEqual(len(self.calls), 1)

    def test_uncertain_send_never_repeated(self):
        self.approve()
        def interrupted(*args):
            self.calls.append("side effect")
            raise RuntimeError("crash")
        with self.assertRaises(RuntimeError):
            self.journal.dispatch(PEER, request(), interrupted, now=1000)
        self.journal.close(); self.journal = Journal(self.path)
        self.assertEqual(self.run_request()["state"], "unknown")
        self.assertEqual(len(self.calls), 1)

    def test_revocation_epoch_and_expiry(self):
        self.approve(); self.journal.revoke(PEER)
        with self.assertRaises(ValueError): self.approve()
        self.journal.approve(PEER, "d" * 32, ["messages.send"])
        with self.assertRaises(Denied): self.run_request()
        value = request(); value["epoch"] = "d" * 32; value["expires"] = 900
        with self.assertRaises(Denied): self.run_request(value)

    def test_grant_removal(self):
        self.approve(); self.journal.set_grants(PEER, [])
        with self.assertRaises(Denied): self.run_request()


class CertificateFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        for name in ("phone", "desktop", "stranger"):
            subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1", "-subj", f"/CN={name}",
                "-addext", "basicConstraints=critical,CA:FALSE", "-addext", "keyUsage=critical,digitalSignature", "-addext", "extendedKeyUsage=serverAuth,clientAuth",
                "-keyout", str(cls.root / f"{name}.key"), "-out", str(cls.root / f"{name}.pem")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def ctx(self, name, peer, server):
        return transport.context(self.root / f"{name}.pem", self.root / f"{name}.key", self.root / f"{peer}.pem", server=server)

    def pin(self, name): return transport.fingerprint((self.root / f"{name}.pem").read_text())



class TransportTests(CertificateFixture):
    def exchange(self, name="desktop", expected="desktop"):
        listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1)
        address = listener.getsockname(); results = []; errors = []
        def server():
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(3)
                    with self.ctx("phone", "desktop", True).wrap_socket(raw, server_side=True) as stream:
                        transport.authenticate(stream, self.pin(expected))
                        value = transport.receive(stream); results.append(value)
                        transport.send(stream, {"ack": True})
            except (ssl.SSLError, PermissionError, OSError) as error: errors.append(type(error).__name__)
            finally: listener.close()
        worker = threading.Thread(target=server); worker.start()
        try:
            with socket.create_connection(address, timeout=3) as raw:
                with self.ctx(name, "phone", False).wrap_socket(raw) as stream:
                    transport.authenticate(stream, self.pin("phone"))
                    transport.send(stream, {"synthetic": "SMS and MMS metadata"})
                    transport.receive(stream)
        except (ssl.SSLError, EOFError, OSError): pass
        worker.join(5); self.assertFalse(worker.is_alive())
        return results, errors

    def test_mutual_tls_exchange(self):
        results, errors = self.exchange()
        self.assertEqual(len(results), 1); self.assertEqual(errors, [])

    def test_untrusted_certificate(self):
        results, errors = self.exchange("stranger")
        self.assertEqual(results, []); self.assertTrue(errors)

    def test_exact_leaf_pin_required(self):
        results, errors = self.exchange(expected="stranger")
        self.assertEqual(results, []); self.assertEqual(errors, ["PermissionError"])

    def test_no_environment_key_logging(self):
        prior = os.environ.get("SSLKEYLOGFILE")
        os.environ["SSLKEYLOGFILE"] = str(self.root / "should-not-exist")
        try: self.assertIsNone(self.ctx("phone", "desktop", True).keylog_filename)
        finally:
            if prior is None: os.environ.pop("SSLKEYLOGFILE")
            else: os.environ["SSLKEYLOGFILE"] = prior

    def test_bad_frames(self):
        import io, struct
        class Reader:
            def __init__(self, data): self.stream = io.BytesIO(data)
            def recv(self, size): return self.stream.read(size)
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'):
            with self.assertRaises(ValueError): transport.receive(Reader(struct.pack("!I", len(data)) + data))
        with self.assertRaises(ValueError): transport.receive(Reader(struct.pack("!I", transport.MAX_FRAME + 1)))
        with self.assertRaises(EOFError): transport.receive(Reader(b'\x00'))


if __name__ == "__main__": unittest.main()

class RelayTests(CertificateFixture):
    def test_opaque_relay_inner_tls(self):
        import queue
        from luma_continuity.relay import TLSRelayStream
        a, b = queue.Queue(), queue.Queue()
        captured = []
        class Endpoint:
            def __init__(self, incoming, outgoing): self.incoming, self.outgoing = incoming, outgoing
            def send(self, data): captured.append(data); self.outgoing.put(data)
            def recv(self, timeout): return self.incoming.get(timeout=timeout)
            def close(self): pass
        client = TLSRelayStream(Endpoint(a, b), self.ctx("desktop", "phone", False), self.pin("phone"), server=False)
        server = TLSRelayStream(Endpoint(b, a), self.ctx("phone", "desktop", True), self.pin("desktop"), server=True)
        failures = []
        def run():
            try:
                server.handshake()
                value = transport.receive(server)
                transport.send(server, {"echo": value})
            except Exception as error: failures.append(error)
        worker = threading.Thread(target=run); worker.start()
        client.handshake()
        fixture = {"private": "synthetic secret message body"}
        transport.send(client, fixture)
        self.assertEqual(transport.receive(client), {"echo": fixture})
        worker.join(5)
        self.assertFalse(worker.is_alive()); self.assertEqual(failures, [])
        self.assertNotIn(b"synthetic secret message body", b"".join(captured))

class QueueTests(unittest.TestCase):
    def test_restart_expiry_revoke_and_unknown(self):
        from luma_continuity.queue import Outbox
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.db"
            box = Outbox(path)
            operation = box.enqueue(PEER, EPOCH, "messages.send", {"body": "synthetic"}, now=1000)
            first = box.pending(PEER, EPOCH, now=1000)
            box.close(); box = Outbox(path)
            try:
                self.assertEqual(first, box.pending(PEER, EPOCH, now=1000))
                box.acknowledge(operation, {"state": "unknown", "result": None})
                self.assertEqual(box.pending(PEER, EPOCH, now=1000), [])
                box.enqueue(PEER, EPOCH, "messages.send", {}, lifetime=1, now=1000)
                self.assertEqual(box.pending(PEER, EPOCH, now=1002), [])
                box.enqueue(PEER, EPOCH, "messages.send", {"body": "private"}, now=1000)
                box.revoke(PEER)
                self.assertEqual(box.pending(PEER, EPOCH, now=1000), [])
                self.assertFalse(any('private' in row[0] for row in box.db.execute("SELECT envelope FROM outbox")))
                with self.assertRaises(ValueError): box.enqueue(PEER, EPOCH, "screen.control", {}, now=1000)
                with self.assertRaises(ValueError): box.enqueue(PEER, EPOCH, "calls.control", {}, now=1000)
            finally: box.close()

class BootstrapTests(unittest.TestCase):
    def test_explicit_fingerprint_and_no_overwrite(self):
        from luma_continuity.bootstrap import create_identity, approve_peer
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = create_identity(root / "first")
            second = create_identity(root / "second")
            with self.assertRaises(FileExistsError): create_identity(root / "first")
            with self.assertRaises(PermissionError):
                approve_peer(root / "first", root / "second/device.pem", first, EPOCH, ["messages.read"])
            self.assertEqual(approve_peer(root / "first", root / "second/device.pem", second, EPOCH, ["messages.read"]), second)
            journal = Journal(root / "first/continuity.db")
            try:
                journal.revoke(second)
                journal.approve(second, "d" * 32, ["messages.read"])
                with self.assertRaises(ValueError): journal.approve(second, EPOCH, ["messages.read"])
            finally: journal.close()

class ConcurrentRevocationTests(unittest.TestCase):
    def test_revoke_waits_for_admitted_effect_then_blocks_next(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.db'
            journal = Journal(path); journal.approve(PEER, EPOCH, ['messages.send']); journal.close()
            entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
            failures, effects = [], []
            def run_effect():
                local = Journal(path)
                try:
                    def handler(*_):
                        entered.set()
                        if not release.wait(2): raise TimeoutError()
                        effects.append('once'); return {'accepted': True}
                    local.dispatch(PEER, request(), handler, now=1000)
                except Exception as error: failures.append(error)
                finally: local.close()
            def revoke():
                local = Journal(path)
                try: local.revoke(PEER); revoked.set()
                except Exception as error: failures.append(error)
                finally: local.close()
            worker = threading.Thread(target=run_effect); worker.start()
            self.assertTrue(entered.wait(2))
            revoker = threading.Thread(target=revoke); revoker.start()
            self.assertFalse(revoked.wait(0.05))
            release.set(); worker.join(3); revoker.join(3)
            self.assertFalse(worker.is_alive() or revoker.is_alive())
            self.assertEqual(failures, []); self.assertTrue(revoked.is_set())
            local = Journal(path)
            try:
                next_request = request(); next_request['id'] = 'd'*32
                with self.assertRaises(Denied):
                    local.dispatch(PEER, next_request, lambda *_: effects.append('unexpected'), now=1000)
                self.assertEqual(effects, ['once'])
            finally: local.close()
