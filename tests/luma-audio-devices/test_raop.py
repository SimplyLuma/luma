# SPDX-License-Identifier: MPL-2.0
import hashlib
import re
import socket
import threading
import unittest

import _paths  # noqa: F401
from luma_audio_devices import raop

# TXT records as a Mac with AirPlay Receiver enabled announces them (macOS 26).
MAC_TXT = [b"vv=0", b"vs=960.13.1", b"vn=65537", b"tp=UDP", b"pk=b0c8c1f0", b"am=Mac15,9", b"md=0,1,2",
           b"sf=0x204", b"ft=0x4A7FCFD5,0x38174FDE", b"et=0,3,5", b"da=true", b"cn=0,1,2,3"]


class Identity(unittest.TestCase):
    def test_device_id_comes_from_the_instance_name(self):
        self.assertEqual(raop.parse_service_name("0615378145AF@Receiver One’s MacBook Pro"),
                         ("0615378145af", "Receiver One’s MacBook Pro"))

    def test_renamed_receiver_keeps_its_id(self):
        a = raop.parse_service_name("0615378145AF@Kitchen")[0]
        b = raop.parse_service_name("0615378145AF@Kitchen (2)")[0]
        self.assertEqual(a, b)

    def test_names_without_an_id_get_a_stable_digest_not_an_address(self):
        receiver_id, name = raop.parse_service_name("Living Room")
        self.assertEqual(name, "Living Room")
        self.assertRegex(receiver_id, r"^n[0-9a-f]{12}$")
        self.assertEqual(receiver_id, raop.parse_service_name("Living Room")[0])

    def test_dns_escaping(self):
        self.assertEqual(raop.dns_escape_instance("AA@Mr. Speaker\\x"), "AA@Mr\\. Speaker\\\\x")


class TxtMapping(unittest.TestCase):
    """Must match module-raop-discover's pw_properties_from_avahi_string."""

    def receiver(self, txt):
        return raop.Receiver("0615378145AF@Mac", "Mac.local", "192.0.2.20", 7000, 2, raop.txt_to_dict(txt))

    def test_mac(self):
        receiver = self.receiver(MAC_TXT)
        self.assertEqual(receiver.transport, "udp")
        self.assertEqual(receiver.encryption, "fp_sap25")
        self.assertEqual(receiver.codec, "PCM")
        self.assertEqual(receiver.kind, "computer")
        self.assertFalse(receiver.announces_password)
        self.assertTrue(receiver.supported)

    def test_encryption_preference_order(self):
        for et, expected in (("0", "none"), ("0,1", "RSA"), ("0,4", "auth_setup"), ("1,4,5", "fp_sap25")):
            with self.subTest(et=et):
                self.assertEqual(self.receiver([f"et={et}".encode()]).encryption, expected)

    def test_codec_and_support(self):
        self.assertEqual(self.receiver([b"cn=1"]).codec, "ALAC")
        self.assertFalse(self.receiver([b"cn=2"]).supported)

    def test_password_and_pin_flags(self):
        self.assertTrue(self.receiver([b"pw=true"]).announces_password)
        self.assertTrue(self.receiver([b"sf=0x80"]).announces_password)
        self.assertFalse(self.receiver([b"sf=0x8"]).supported)

    def test_txt_decoding(self):
        self.assertEqual(raop.txt_to_dict([[0x61, 0x6d, 0x3d, 0x58], b"TP=TCP", b"flag"]),
                         {"am": "X", "tp": "TCP", "flag": ""})

    def test_sink_arguments(self):
        receiver = self.receiver(MAC_TXT)
        args = raop.sink_module_args(receiver, "secret")
        self.assertEqual(args["raop.ip"], "192.0.2.20")
        self.assertEqual(args["raop.port"], "7000")
        self.assertEqual(args["raop.name"], "0615378145AF@Mac")
        self.assertEqual(args["node.name"], "luma_airplay.0615378145af")
        self.assertEqual(args["raop.password"], "secret")
        self.assertEqual(args["stream.props"]["luma.airplay.id"], "0615378145af")
        self.assertIs(args["stream.props"]["node.network"], True)
        self.assertNotIn("raop.password", raop.sink_module_args(receiver))

    def test_link_local_ipv6_is_scoped(self):
        receiver = raop.Receiver("AABBCCDDEEFF@X", "x.local", "fe80::1", 7000, 3, {})
        self.assertEqual(receiver.connect_address(), "fe80::1%3")


class FakeReceiver(threading.Thread):
    """A minimal RTSP responder: OPTIONS with optional Digest authentication."""

    def __init__(self, password=None, status=None):
        super().__init__(daemon=True)
        self.password = password
        self.status = status
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.requests = []

    def run(self):
        try:
            self.serve()
        finally:
            self.server.close()

    def serve(self):
        connection, _ = self.server.accept()
        with connection:
            buffer = b""
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n\r\n" in buffer:
                    request, buffer = buffer.split(b"\r\n\r\n", 1)
                    text = request.decode()
                    self.requests.append(text)
                    cseq = re.search(r"CSeq: (\d+)", text).group(1)
                    connection.sendall(self.respond(text, cseq).encode())

    def respond(self, text, cseq):
        if self.status:
            return f"RTSP/1.0 {self.status} Nope\r\nCSeq: {cseq}\r\n\r\n"
        if self.password is None:
            return f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nPublic: ANNOUNCE, SETUP\r\n\r\n"
        match = re.search(r'Authorization: Digest .*nonce="([^"]+)".*response="([^"]+)"', text)
        if match:
            ha1 = hashlib.md5(f"iTunes:raop:{self.password}".encode()).hexdigest()
            ha2 = hashlib.md5(b"OPTIONS:*").hexdigest()
            if match.group(2) == hashlib.md5(f"{ha1}:{match.group(1)}:{ha2}".encode()).hexdigest():
                return f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n"
        return (f"RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n"
                f'WWW-Authenticate: Digest realm="raop", nonce="abc123"\r\n\r\n')


class Probe(unittest.TestCase):
    def probe(self, server, password=None):
        server.start()
        result = raop.probe("127.0.0.1", server.port, password, timeout=3)
        server.join(3)
        return result

    def test_open_receiver(self):
        self.assertEqual(self.probe(FakeReceiver()).status, raop.PROBE_OK)

    def test_password_required(self):
        self.assertEqual(self.probe(FakeReceiver(password="hunter2")).status, raop.PROBE_PASSWORD_REQUIRED)

    def test_password_accepted(self):
        self.assertEqual(self.probe(FakeReceiver(password="hunter2"), "hunter2").status, raop.PROBE_OK)

    def test_password_rejected(self):
        self.assertEqual(self.probe(FakeReceiver(password="hunter2"), "wrong").status,
                         raop.PROBE_PASSWORD_INCORRECT)

    def test_owner_only_receiver(self):
        self.assertEqual(self.probe(FakeReceiver(status=403)).status, raop.PROBE_DENIED)

    def test_nothing_listening(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertEqual(raop.probe("127.0.0.1", port, timeout=1).status, raop.PROBE_UNREACHABLE)


if __name__ == "__main__":
    unittest.main()
