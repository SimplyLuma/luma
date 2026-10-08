# SPDX-License-Identifier: GPL-3.0-only
"""Exercise anonymous-pipe lifecycle without GTK or a browser installation."""
from pathlib import Path
import sys
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
import signal
import tempfile
import threading
import unittest

from engine_pipe import EnginePipe


class EngineLifecycleTest(unittest.TestCase):
    def test_child_receives_supported_gtk3_and_retained_security_arguments(self):
        # Exercise actual child argv for both pipe modes. The compositor stub
        # keeps this a launcher test; it is not graphical qualification.
        with tempfile.TemporaryDirectory(prefix='viola-pipe-argv-') as directory:
            root = Path(directory)
            executable = root / 'engine'
            executable.write_text('#!' + sys.executable + '\n' + "import json, os, pathlib, sys\npathlib.Path(__file__).with_suffix('.argv.json').write_text(json.dumps(sys.argv[1:]))\nbuffer = b''\nwhile True:\n    data = os.read(3, 65536)\n    if not data:\n        break\n    buffer += data\n    while b'\\0' in buffer:\n        record, buffer = buffer.split(b'\\0', 1)\n        message = json.loads(record)\n        if message['method'] == 'Browser.close':\n            os._exit(0)\n        os.write(4, json.dumps({'id': message['id'], 'result': {'ok': True}}).encode() + b'\\0')\n")
            executable.chmod(0o700)
            for native in (False, True):
                with self.subTest(native_frame=native):
                    display = SimpleNamespace(environment=os.environ.copy, close=lambda: None)
                    with patch('engine_display.EngineDisplay', return_value=display):
                        engine = EnginePipe(executable, root / str(native), lambda _: None,
                                            native_frame_probe=native)
                    try:
                        self.assertEqual(engine.call('Ping'), {'ok': True})
                        argv = json.loads(executable.with_suffix('.argv.json').read_text())
                        self.assertEqual(argv.count('--gtk-version=3'), 1)
                        for flag in ('--enable-gpu', '--ozone-platform=wayland',
                                     '--remote-debugging-pipe', '--no-first-run',
                                     '--disable-default-apps', '--no-startup-window'):
                            self.assertIn(flag, argv)
                        self.assertEqual('--headless=new' in argv, not native)
                        self.assertEqual('--viola-native-frame-probe' in argv, native)
                        for flag in ('--no-sandbox', '--disable-gpu', '--disable-setuid-sandbox'):
                            self.assertNotIn(flag, argv)
                    finally:
                        engine.close()
                    self.assertEqual(engine.process.returncode, 0)

    def test_real_sigsegv_is_retained_after_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='viola-pipe-crash-') as directory:
            root = Path(directory)
            executable = root / 'engine'
            executable.write_text('#!' + sys.executable + '\n' +
                'import os, resource, signal\n'
                'resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n'
                'os.kill(os.getpid(), signal.SIGSEGV)\n')
            executable.chmod(0o700)
            closed = threading.Event()
            engine = EnginePipe(executable, root / 'profile', lambda _: None,
                                on_closed=closed.set)
            self.assertTrue(closed.wait(3))
            engine.close()
            self.assertEqual(engine.process.returncode, -signal.SIGSEGV)
            engine.close()
            self.assertEqual(engine.process.returncode, -signal.SIGSEGV)
            self.assertFalse(engine.reader.is_alive())

    def test_exit_fails_pending_and_closes_once(self):
        with tempfile.TemporaryDirectory(prefix='viola-pipe-lifecycle-') as directory:
            root = Path(directory)
            executable = root / 'engine'
            executable.write_text('#!' + sys.executable + '\n' + '''
import json, os
buffer = b''
while True:
    data = os.read(3, 65536)
    if not data:
        break
    buffer += data
    while b'\\0' in buffer:
        record, buffer = buffer.split(b'\\0', 1)
        message = json.loads(record)
        if message['method'] in ('Browser.close', 'Exit'):
            os._exit(0)
        if message['method'] == 'Hold':
            continue
        os.write(4, json.dumps({'id': message['id'], 'result': {'ok': True}}).encode() + b'\\0')
''')
            executable.chmod(0o700)
            closed = threading.Event()
            engine = EnginePipe(executable, root / 'profile', lambda _: None,
                                on_closed=closed.set)
            try:
                self.assertEqual(engine.call('Ping'), {'ok': True})
                pending = engine.request('Hold')
                engine.request('Exit')
                self.assertTrue(closed.wait(3))
                self.assertIsInstance(pending.exception(1), RuntimeError)
                with self.assertRaises(RuntimeError):
                    engine.request('Late')
            finally:
                engine.close()
                engine.close()
            self.assertFalse(engine.reader.is_alive())
            self.assertFalse(engine.pending)
            closed.clear()
            engine = EnginePipe(executable, root / 'normal', lambda _: None,
                                on_closed=closed.set)
            engine.close()
            self.assertFalse(closed.is_set())
            self.assertEqual(engine.process.returncode, 0)
            self.assertFalse(engine.reader.is_alive())
            sentinel = root / 'normal' / 'retained-profile-data'
            sentinel.write_text('retained')
            with self.assertRaises(ValueError):
                EnginePipe(executable, root / 'normal', lambda _: None)
            engine = EnginePipe(executable, root / 'normal', lambda _: None, reuse_profile=True)
            try:
                self.assertEqual(engine.call('Ping'), {'ok': True})
                self.assertEqual(sentinel.read_text(), 'retained')
            finally:
                engine.close()


if __name__ == '__main__':
    unittest.main()
