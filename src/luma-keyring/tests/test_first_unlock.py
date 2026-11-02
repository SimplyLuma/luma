# SPDX-License-Identifier: MPL-2.0
"""The first unlock: when Luma asks, what each answer does, and READY."""

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from luma_keyring import autounlock, first_unlock, ready, repair  # noqa: E402
from luma_keyring.first_unlock import Kind  # noqa: E402
from test_autounlock import KEY, PASSWORD, FakeKeyring, Paths, cannot_seal, seal, unseal, verify  # noqa: E402

KEYRING_PASSWORD = "older-keyring-password"


class Keyring(FakeKeyring):
    def supports_repair(self):
        return True


class FirstUnlock(Paths):
    def setUp(self):
        super().setUp()
        self.keyring_file()
        self.keyring = Keyring(password=PASSWORD, locked=True, files=self.keyrings)

    def unlock(self, password=PASSWORD, automatic=True, keyring=None, seal_with=seal):
        return first_unlock.unlock(password, automatic, keyring or self.keyring,
                                   seal=seal_with, verify=verify)

    def state_files(self):
        folder = autounlock.state_dir()
        return self.slot_names() if folder.exists() else []

    # -- when it asks ------------------------------------------------------
    def test_asks_only_for_a_locked_unenrolled_automatic_sign_in(self):
        self.assertTrue(first_unlock.should_ask(self.keyring, autologin=True))
        self.assertFalse(first_unlock.should_ask(self.keyring, autologin=False))
        self.keyring.locked = False
        self.assertFalse(first_unlock.should_ask(self.keyring, autologin=True))

    def test_an_enrolled_account_is_never_asked(self):
        self.slots()
        self.assertFalse(first_unlock.should_ask(self.keyring, autologin=True))

    def test_no_keyring_file_or_daemon_is_not_asked_about(self):
        self.keyrings.joinpath("login.keyring").unlink()
        self.assertFalse(first_unlock.should_ask(self.keyring, autologin=True))
        self.assertFalse(first_unlock.should_ask(Keyring(running=False), autologin=True))

    # -- the answers -------------------------------------------------------
    def test_switch_on_unlocks_and_enrols(self):
        result = self.unlock()
        self.assertIs(result.kind, Kind.AUTOMATIC)
        self.assertFalse(self.keyring.locked)
        self.assertNotEqual(self.keyring.password, PASSWORD)
        self.assertEqual(self.state_files(), ["login.cred", "login.wrap"])
        key = unseal(autounlock.credential_path())
        self.assertEqual(self.keyring.password, key)
        self.assertEqual(autounlock.unwrap_key(autounlock.wrap_path().read_bytes(), PASSWORD), key)
        # The next session opens with no question.
        self.keyring.reboot()
        self.assertEqual(self.ensure(self.keyring), autounlock.Result.UNLOCKED)

    def test_the_password_is_never_written(self):
        self.unlock()
        for path in autounlock.state_dir().iterdir():
            self.assertNotIn(PASSWORD.encode(), path.read_bytes(), path.name)

    def test_switch_off_unlocks_this_session_only(self):
        result = self.unlock(automatic=False)
        self.assertIs(result.kind, Kind.UNLOCKED)
        self.assertFalse(self.keyring.locked)
        self.assertEqual(self.keyring.password, PASSWORD)
        self.assertEqual(self.state_files(), [])
        self.keyring.reboot()
        self.assertTrue(first_unlock.should_ask(self.keyring, autologin=True))

    def test_a_wrong_password_changes_nothing(self):
        for automatic in (True, False):
            result = self.unlock(password="nope", automatic=automatic)
            self.assertIs(result.kind, Kind.WRONG_PASSWORD)
            self.assertTrue(self.keyring.locked)
            self.assertEqual(self.keyring.password, PASSWORD)
            self.assertEqual(self.state_files(), [])
        self.assertIs(self.unlock(password="").kind, Kind.WRONG_PASSWORD)

    def test_an_unlocked_keyring_still_checks_the_password(self):
        self.keyring.locked = False
        self.assertIs(self.unlock(password="nope").kind, Kind.NOT_CHANGED)
        self.assertEqual(self.keyring.password, PASSWORD)
        self.assertEqual(self.state_files(), [])
        self.assertIs(self.unlock().kind, Kind.AUTOMATIC)

    def test_a_keyring_on_another_password_unlocks_but_is_not_enrolled(self):
        keyring = Keyring(password=KEYRING_PASSWORD, locked=True, files=self.keyrings)
        result = self.unlock(password=KEYRING_PASSWORD, keyring=keyring)
        self.assertIs(result.kind, Kind.UNLOCKED_ONLY)
        self.assertFalse(keyring.locked)
        self.assertEqual(keyring.password, KEYRING_PASSWORD)
        self.assertEqual(self.state_files(), [])

    def test_a_device_that_cannot_seal_fails_closed(self):
        result = self.unlock(seal_with=cannot_seal)
        self.assertIs(result.kind, Kind.UNLOCKED_ONLY)
        self.assertFalse(self.keyring.locked)
        self.assertEqual(self.keyring.password, PASSWORD)
        self.assertEqual(self.state_files(), [])

    def test_no_daemon_is_said_plainly(self):
        result = self.unlock(keyring=Keyring(password=None, files=self.keyrings))
        self.assertIs(result.kind, Kind.UNAVAILABLE)

    def test_a_weak_password_is_said_after_enrolling(self):
        self.patch(repair, "password_quality_note", lambda password: " Your password is weak.")
        result = self.unlock()
        self.assertIs(result.kind, Kind.AUTOMATIC)
        self.assertEqual(result.message, "Your password is weak.")

    # -- the switch in the window ------------------------------------------
    def test_turning_it_off_puts_the_keyring_back_on_the_password(self):
        self.unlock()
        result = first_unlock.set_automatic(False, PASSWORD, self.keyring, unseal=unseal,
                                            verify=verify)
        self.assertIs(result.kind, Kind.CHANGED)
        self.assertEqual(self.keyring.password, PASSWORD)
        self.assertEqual(self.state_files(), [])

    def test_turning_it_off_with_a_wrong_password_changes_nothing(self):
        self.unlock()
        key = self.keyring.password
        result = first_unlock.set_automatic(False, "nope", self.keyring, unseal=unseal,
                                            verify=verify)
        self.assertIs(result.kind, Kind.WRONG_PASSWORD)
        self.assertEqual(self.keyring.password, key)
        self.assertEqual(self.state_files(), ["login.cred", "login.wrap"])

    def test_turning_it_on_from_an_unlocked_keyring(self):
        self.keyring.locked = False
        result = first_unlock.set_automatic(True, PASSWORD, self.keyring, seal=seal, verify=verify)
        self.assertIs(result.kind, Kind.CHANGED)
        self.assertEqual(self.state_files(), ["login.cred", "login.wrap"])


class DeviceNoun(unittest.TestCase):
    def test_tv_posture_is_this_tv(self):
        old = os.environ.get("LUMA_POSTURE")
        try:
            os.environ["LUMA_POSTURE"] = "tv"
            self.assertTrue(first_unlock._tv_posture())
            os.environ["LUMA_POSTURE"] = "desktop"
            self.assertFalse(first_unlock._tv_posture())
        finally:
            if old is None:
                os.environ.pop("LUMA_POSTURE", None)
            else:
                os.environ["LUMA_POSTURE"] = old
        self.assertRegex(first_unlock.device_noun(), r"^this ")


class Ready(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, ready, "_ready_sent", False)
        ready._ready_sent = False
        old = os.environ.get("NOTIFY_SOCKET")
        self.addCleanup(lambda: os.environ.__setitem__("NOTIFY_SOCKET", old) if old
                        else os.environ.pop("NOTIFY_SOCKET", None))

    def test_ready_is_sent_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "notify")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
                server.bind(path)
                server.settimeout(1)
                os.environ["NOTIFY_SOCKET"] = path
                self.assertFalse(ready.notify_ready())
                self.assertEqual(server.recv(64), b"READY=1")
                ready.notify_ready()
                with self.assertRaises(socket.timeout):
                    server.recv(64)

    def test_only_the_session_service_decides(self):
        os.environ.pop("NOTIFY_SOCKET", None)
        self.assertFalse(ready.decide(["luma-keyring", "--check"]))


if __name__ == "__main__":
    unittest.main()
