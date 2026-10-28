# SPDX-License-Identifier: Apache-2.0
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

import gi

gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib
from luma_tester.display import FrameStream

NATIVE = struct.pack('>BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
OTHER = struct.pack('>BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 0, 8, 16)


def take(conn, count):
    data = b''
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


class Server(threading.Thread):
    """Minimal RFB server that sends one scripted message per update request."""

    def __init__(self, path, width, height, pixel_format, script):
        super().__init__(daemon=True)
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(path))
        self.listener.listen(1)
        self.width, self.height, self.format, self.script = width, height, pixel_format, script
        self.requests, self.client_formats = [], []

    def run(self):
        conn, _ = self.listener.accept()
        conn.sendall(b'RFB 003.008\n')
        take(conn, 12)
        conn.sendall(b'\x01\x01')
        take(conn, 1)
        conn.sendall(struct.pack('>I', 0))
        take(conn, 1)
        conn.sendall(struct.pack('>HH', self.width, self.height) + self.format + struct.pack('>I', 4) + b'test')
        try:
            while True:
                kind = take(conn, 1)[0]
                if kind == 0:
                    self.client_formats.append(take(conn, 19))
                elif kind == 2:
                    take(conn, 1)
                    take(conn, 4 * struct.unpack('>H', take(conn, 2))[0])
                elif kind == 3:
                    self.requests.append(struct.unpack('>BHHHH', take(conn, 9)))
                    if self.script:
                        self.script.pop(0)(conn)
        except (EOFError, OSError):
            pass
        finally:
            conn.close()


def rect(x, y, width, height, pixel):
    return struct.pack('>HHHHi', x, y, width, height, 0) + pixel * (width * height)


class FrameStreamTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'display'
        self.frames = []

    def tearDown(self):
        self.temporary.cleanup()

    def stream(self, server, done):
        server.start()
        frames = self.frames

        def published(_resized, error):
            self.assertIsNone(error)
            frame = stream.latest()
            if frame:
                frames.append(frame)
            return False

        stream = FrameStream(self.path, published)
        stream.start()
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while not (frames and done(frames[-1])) and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.005)
        stream.close()
        self.assertTrue(frames and done(frames[-1]))
        return stream

    def test_publishes_only_complete_updates(self):
        red, blue = b'\x00\x00\xff\x00', b'\xff\x00\x00\x00'

        def split_update(conn):
            conn.sendall(struct.pack('>BxH', 0, 2) + rect(0, 0, 4, 1, red))
            time.sleep(0.2)  # a reader painting now would show half a frame
            conn.sendall(rect(0, 1, 4, 1, blue))

        server = Server(self.path, 4, 2, NATIVE, [split_update])
        self.stream(server, lambda frame: True)
        self.assertEqual(len(self.frames), 1)
        width, height, format, data = self.frames[0]
        self.assertEqual((width, height, format), (4, 2, Gdk.MemoryFormat.B8G8R8X8))
        self.assertEqual(data, red * 4 + blue * 4)
        self.assertEqual(server.requests[0][0], 0)
        self.assertEqual(server.client_formats, [])

    def test_resize_reallocates_and_requests_full_frame(self):
        grey = b'\x80\x80\x80\x00'

        def resize(conn):
            conn.sendall(struct.pack('>BxH', 0, 1) + struct.pack('>HHHHi', 0, 0, 2, 2, -223))

        def full(conn):
            conn.sendall(struct.pack('>BxH', 0, 1) + rect(0, 0, 2, 2, grey))

        server = Server(self.path, 4, 2, NATIVE, [resize, full])
        # Frames published faster than the main loop drains coalesce: the
        # blank resized frame may be replaced by the full one before painting.
        self.stream(server, lambda frame: frame[3] == grey * 4)
        self.assertEqual(self.frames[-1][:2], (2, 2))
        self.assertEqual(server.requests[1], (0, 0, 0, 2, 2))

    def test_non_native_server_format_requests_rgbx(self):
        server = Server(self.path, 1, 1, OTHER,
                        [lambda conn: conn.sendall(struct.pack('>BxH', 0, 1) + rect(0, 0, 1, 1, b'\x01\x02\x03\x00'))])
        self.stream(server, lambda frame: True)
        self.assertEqual(self.frames[0][2], Gdk.MemoryFormat.R8G8B8X8)
        self.assertEqual(len(server.client_formats), 1)

    def test_rejects_update_outside_framebuffer(self):
        server = Server(self.path, 2, 2, NATIVE,
                        [lambda conn: conn.sendall(struct.pack('>BxH', 0, 1) + rect(1, 1, 2, 2, b'\0\0\0\0'))])
        server.start()
        errors = []
        stream = FrameStream(self.path, lambda _resized, error: errors.append(error) or False)
        stream.start()
        deadline = time.monotonic() + 5
        while not errors and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        stream.close()
        self.assertIn('exceeds the framebuffer', errors[0])


if __name__ == '__main__':
    unittest.main()
