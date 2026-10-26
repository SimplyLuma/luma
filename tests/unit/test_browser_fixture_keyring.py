# SPDX-License-Identifier: Apache-2.0
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('browser_fixture_keyring', ROOT / 'tests/os/gate/browser-fixture-keyring.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.uid = os.getuid()
        self.home = self.base / 'home' / M.USER
        self.runtime = self.base / 'run/user' / str(self.uid)
        self.home.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.runtime / 'bus'))
        self.addCleanup(self.sock.close)
        self.env = dict(HOME=str(self.home), USER=M.USER,
                        XDG_RUNTIME_DIR=str(self.runtime),
                        DBUS_SESSION_BUS_ADDRESS=f'unix:path={self.runtime}/bus')
        self.account = SimpleNamespace(pw_uid=self.uid, pw_dir=str(self.home))
        self.enterContext(patch.object(M, 'Path', lambda p: self.base / str(p).lstrip('/')))
        self.enterContext(patch.object(M.pwd, 'getpwnam', return_value=self.account))
        self.enterContext(patch.dict(os.environ, self.env))

    def test_dynamic_uid_and_real_owned_socket(self):
        self.assertEqual(M.validate_context(self.uid), self.home)

    def test_wrong_uid_refused(self):
        with self.assertRaises(AssertionError): M.validate_context(self.uid + 1)

    def test_alternate_home_or_bus_refused(self):
        for key in ('HOME', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'USER'):
            with self.subTest(key=key), patch.dict(os.environ, {key: 'other'}):
                with self.assertRaises(AssertionError): M.validate_context(self.uid)

    def test_existing_keyring_refused(self):
        keyrings = self.home / '.local/share/keyrings'
        keyrings.mkdir(parents=True)
        (keyrings / 'login.keyring').write_text('existing user data')
        with self.assertRaisesRegex(AssertionError, 'fresh'): M.validate_context(self.uid)
        self.assertEqual((keyrings / 'login.keyring').read_text(), 'existing user data')

    def test_symlinked_keyring_directory_refused(self):
        (self.home / '.local').symlink_to(self.runtime, target_is_directory=True)
        with self.assertRaises(AssertionError): M.validate_context(self.uid)

    def test_regular_bus_file_refused(self):
        (self.runtime / 'bus').unlink()
        (self.runtime / 'bus').write_text('not a bus')
        with self.assertRaises(AssertionError): M.validate_context(self.uid)


class ReadinessTests(unittest.TestCase):
    def run_helper(self, locked='false', owner_uid='1007', failed_init=False):
        self.calls = []
        def run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[0].endswith('gnome-keyring-daemon'):
                return SimpleNamespace(returncode=int(failed_init))
            if 'GetNameOwner' in argv: reply = 's ":1.42"'
            elif 'GetConnectionUnixUser' in argv: reply = 'u ' + owner_uid
            elif 'ReadAlias' in argv: reply = 'o "/org/freedesktop/secrets/collection/login"'
            elif 'Locked' in argv: reply = 'b ' + locked
            elif 'GetId' in argv: reply = 's "fixture-guid"'
            else: raise AssertionError(argv)
            return SimpleNamespace(stdout=reply)
        ticks = iter(range(100))
        with patch.object(M, 'validate_context'), patch.object(M.subprocess, 'run', side_effect=run), \
             patch.object(M.time, 'monotonic', side_effect=lambda: next(ticks)), patch.object(M.time, 'sleep'):
            return M.initialize(1007)

    def test_both_aliases_unlocked_and_random_secret_not_in_receipt(self):
        receipt = self.run_helper()
        self.assertEqual(set(receipt['collections']), {'default', 'login'})
        secret = self.calls[0][1]['input']
        self.assertGreaterEqual(len(secret), 32)
        self.assertNotIn(secret.decode(), repr(receipt))
        self.assertEqual(self.calls[0][1]['timeout'], 5)

    def test_locked_collection_has_bounded_failure(self):
        with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)): self.run_helper(locked='true')
        self.assertLess(len(self.calls), 20)

    def test_different_service_uid_has_bounded_failure(self):
        with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)): self.run_helper(owner_uid='1100')
        self.assertLess(len(self.calls), 20)

    def test_failed_initialization_refuses_before_readiness(self):
        with self.assertRaisesRegex(AssertionError, 'initialization failed'): self.run_helper(failed_init=True)
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__': unittest.main()
