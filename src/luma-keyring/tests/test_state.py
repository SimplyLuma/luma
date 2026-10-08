# SPDX-License-Identifier: MPL-2.0
"""What the state machine says, without a keyring daemon in sight."""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luma_keyring import state  # noqa: E402
from luma_keyring.secrets import Collection  # noqa: E402


class FakeKeyring:
    def __init__(self, running=True, collection=None, repair=True):
        self._running = running
        self._collection = collection
        self._repair = repair

    def running(self):
        return self._running

    def collection(self, path=None):
        return self._collection

    def supports_repair(self):
        return self._repair


class StateTests(unittest.TestCase):
    def setUp(self):
        self._real_path = state.keyring_path
        self.addCleanup(setattr, state, "keyring_path", self._real_path)

    def _with_file(self, exists: bool):
        state.keyring_path = lambda: types.SimpleNamespace(exists=lambda: exists)

    def test_no_daemon_is_not_a_mismatch(self):
        reading = state.read(FakeKeyring(running=False))
        self.assertIs(reading.state, state.State.NO_DAEMON)
        self.assertFalse(reading.state.is_broken)

    def test_unlocked_says_so(self):
        self._with_file(True)
        reading = state.read(FakeKeyring(
            collection=Collection("/login", locked=False, label="Login")))
        self.assertIs(reading.state, state.State.UNLOCKED)
        self.assertIn("unlock when you log in", reading.sentence)

    def test_locked_keyring_is_the_broken_state(self):
        self._with_file(True)
        reading = state.read(FakeKeyring(
            collection=Collection("/login", locked=True, label="Login")))
        self.assertIs(reading.state, state.State.LOCKED)
        self.assertTrue(reading.state.is_broken)
        self.assertTrue(reading.repairable)
        self.assertIn("older password", reading.sentence)

    def test_locked_without_a_keyring_file_is_not_reported(self):
        self._with_file(False)
        reading = state.read(FakeKeyring(
            collection=Collection("/login", locked=True, label="Login")))
        self.assertIs(reading.state, state.State.NO_KEYRING)
        self.assertFalse(reading.state.is_broken)

    def test_a_keyring_that_cannot_be_re_keyed_says_that_too(self):
        self._with_file(True)
        reading = state.read(FakeKeyring(
            collection=Collection("/login", locked=True, label="Login"), repair=False))
        self.assertTrue(reading.state.is_broken)
        self.assertFalse(reading.repairable)
        self.assertIn("cannot be re-keyed", reading.sentence)


if __name__ == "__main__":
    unittest.main()
