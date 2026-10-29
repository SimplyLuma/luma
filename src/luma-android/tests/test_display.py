# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from luma_android.display import wait_for_wayland
from luma_android.errors import RuntimeUnavailableError


class DisplayReadiness(unittest.TestCase):
    def test_delayed_real_compositor_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wayland-test'
            with socket.socket(socket.AF_UNIX) as server:
                def listen():
                    time.sleep(.08)
                    server.bind(str(path))
                    # Cover a socket inode that exists before listen succeeds.
                    time.sleep(.1)
                    server.listen(4)
                worker = threading.Thread(target=listen)
                worker.start()
                try:
                    with patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory, 'WAYLAND_DISPLAY': path.name}):
                        wait_for_wayland(timeout=2)
                finally:
                    worker.join()
                connection, _ = server.accept()
                connection.close()

    def test_stale_socket_regular_file_and_symlink_fail_within_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wayland-test'
            for kind in ('missing', 'regular', 'stale', 'symlink'):
                with self.subTest(kind=kind):
                    if kind == 'regular':
                        path.write_text('not a compositor')
                    if kind == 'stale':
                        with socket.socket(socket.AF_UNIX) as stale:
                            stale.bind(str(path))
                    if kind == 'symlink':
                        path.symlink_to(Path(directory) / 'other')
                    started = time.monotonic()
                    with patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory, 'WAYLAND_DISPLAY': path.name}):
                        with self.assertRaises(RuntimeUnavailableError):
                            wait_for_wayland(timeout=.15)
                    self.assertLess(time.monotonic() - started, 1)
                    if path.exists() or path.is_symlink():
                        path.unlink()

    def test_absolute_listening_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wayland-test'
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(path))
                server.listen(1)
                with patch.dict(os.environ, {'WAYLAND_DISPLAY': str(path)}):
                    wait_for_wayland(timeout=.2)
                connection, _ = server.accept()
                connection.close()


if __name__ == '__main__':
    unittest.main()
