"""An application reaches X through the launcher and never holds the cookie."""
from __future__ import annotations

import os
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import capsule_display as display


def entry(family: int, address: bytes, number: bytes, name: bytes, data: bytes) -> bytes:
    return struct.pack(">H", family) + b"".join(struct.pack(">H", len(f)) + f for f in (address, number, name, data))


class Cookies(unittest.TestCase):
    def test_the_cookie_of_the_sessions_display_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth"
            path.write_bytes(entry(256, b"host", b"1", display.COOKIE, b"B" * 16)
                             + entry(0, b"\x7f\0\0\1", b"0", display.COOKIE, b"T" * 16)
                             + entry(256, b"host", b"0", b"XDM-AUTHORIZATION-1", b"X" * 16)
                             + entry(256, b"host", b"0", display.COOKIE, b"A" * 16))
            self.assertEqual(display.read_cookie(path, 0), b"A" * 16)
            self.assertEqual(display.read_cookie(path, 1), b"B" * 16)
            self.assertIsNone(display.read_cookie(path, 2))
            path.write_bytes(entry(65535, b"", b"", display.COOKIE, b"W" * 16))
            self.assertEqual(display.read_cookie(path, 5), b"W" * 16)
            path.write_bytes(b"\x01\x00\x00\x09trunc")
            self.assertIsNone(display.read_cookie(path, 0))
            self.assertIsNone(display.read_cookie(Path(directory) / "missing", 0))

    def test_only_a_local_display_is_relayed(self):
        self.assertEqual(display.session_display(":0"), 0)
        self.assertEqual(display.session_display(":1.0"), 1)
        self.assertEqual(display.session_display("unix:12"), 12)
        for value in ("", "remote:0", "localhost:10.0", "/tmp/socket", ":x"):
            self.assertIsNone(display.session_display(value))


class Setup(unittest.TestCase):
    def test_the_cookie_replaces_whatever_the_client_sent_in_either_byte_order(self):
        for order, mark in (("<", b"l"), (">", b"B")):
            prefix = mark + b"\0" + struct.pack(order + "HHHHH", 11, 0, 5, 3, 0)
            setup, discard = display.authorised_setup(prefix, b"C" * 16)
            self.assertEqual(discard, 8 + 4)
            self.assertEqual(struct.unpack(order + "HHHH", setup[2:10]), (11, 0, 18, 16))
            self.assertEqual(setup[12:], display.COOKIE + b"\0\0" + b"C" * 16)
            bare, _ = display.authorised_setup(prefix, None)
            self.assertEqual(struct.unpack(order + "HHHH", bare[2:10]), (11, 0, 0, 0))
            self.assertEqual(len(bare), 12)

    def test_bytes_that_are_not_a_setup_are_refused(self):
        self.assertIsNone(display.authorised_setup(b"GET / HTTP/1", b"C" * 16))
        self.assertIsNone(display.authorised_setup(b"l\0" + struct.pack("<HHHHH", 11, 0, 60000, 60000, 0), b"C"))
        self.assertIsNone(display.authorised_setup(b"l\0short", b"C"))


class Relaying(unittest.TestCase):
    def test_the_server_sees_the_cookie_and_the_application_never_does(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(root / "X0"))
            server.listen(1)
            relay = display.DisplayRelay(root / "app", root / "X0", b"S" * 16)
            relay.start()
            try:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(5)
                client.connect(str(root / "app"))
                # The application has no cookie to offer.
                client.sendall(b"l\0" + struct.pack("<HHHHH", 11, 0, 0, 0, 0) + b"first request")
                upstream, _ = server.accept()
                upstream.settimeout(5)
                expected = (b"l\0" + struct.pack("<HHHHH", 11, 0, 18, 16, 0) + display.COOKIE + b"\0\0" + b"S" * 16
                            + b"first request")
                received = b""
                while len(received) < len(expected):
                    received += upstream.recv(4096)
                self.assertEqual(received, expected)
                upstream.sendall(b"\x01accepted")
                self.assertEqual(client.recv(64), b"\x01accepted")
                reader, writer = os.pipe()
                socket.send_fds(client, [b"shm"], [writer])
                os.close(writer)
                data, descriptors, _f, _a = socket.recv_fds(upstream, 16, 2)
                self.assertEqual((data, len(descriptors)), (b"shm", 1))
                os.close(descriptors[0])
                os.close(reader)
                client.close()
                self.assertEqual(upstream.recv(16), b"")
                upstream.close()
            finally:
                relay.stop()
                server.close()

    def test_there_is_no_relay_without_a_local_display(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DISPLAY": "remote:0"}):
                self.assertIsNone(display.relay_for_session(Path(directory) / "app", Path(directory)))
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(display.relay_for_session(Path(directory) / "app", Path(directory)))

    def test_the_cookie_comes_only_from_the_sessions_own_runtime_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, elsewhere = Path(directory) / "run", Path(directory) / "home"
            runtime.mkdir()
            elsewhere.mkdir()
            sockets = Path(directory) / "x11"
            sockets.mkdir()
            (sockets / "X0").touch()
            for folder in (runtime, elsewhere):
                (folder / "auth").write_bytes(entry(256, b"h", b"0", display.COOKIE, b"K" * 16))
            with patch.object(display, "SOCKETS", sockets):
                with patch.dict(os.environ, {"DISPLAY": ":0", "XAUTHORITY": str(runtime / "auth")}):
                    self.assertEqual(display.relay_for_session(runtime / "app", runtime).cookie, b"K" * 16)
                with patch.dict(os.environ, {"DISPLAY": ":0", "XAUTHORITY": str(elsewhere / "auth")}):
                    self.assertIsNone(display.relay_for_session(runtime / "app", runtime).cookie)


if __name__ == "__main__":
    unittest.main()
