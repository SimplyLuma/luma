# SPDX-License-Identifier: MPL-2.0
import tempfile
import time
import unittest
from pathlib import Path

import _paths

if _paths.have_gi():
    from gi.repository import GLib
    from luma_audio_devices import airplay, raop
    from luma_audio_devices.state import RememberedReceiver, Store

MAC_TXT = {"tp": "UDP", "et": "0,3,5", "cn": "0,1,2,3", "am": "Mac15,9"}


class FakeControl:
    def __init__(self):
        self.modules = {}
        self.alive = {}
        self.defaults = []

    def load_raop_sink(self, handle, args):
        self.modules[handle] = args
        self.alive[handle] = True
        return True

    def unload(self, handle):
        self.modules.pop(handle, None)
        self.alive.pop(handle, None)

    def loaded(self, handle):
        return handle in self.modules

    def module_alive(self, handle):
        return self.alive.get(handle, False)

    def set_default_sink(self, node_name):
        self.defaults.append(node_name)
        return True


class FakeDiscovery:
    def __init__(self):
        self.found = {}
        self.browsing = False
        self.watched = {}

    def receivers(self):
        return dict(self.found)

    def set_browsing(self, browsing):
        self.browsing = browsing

    def set_watched(self, watched):
        self.watched = dict(watched)


class FakePasswords:
    def __init__(self):
        self.stored = {}
        self.cleared = []

    def lookup(self, receiver_id, callback):
        callback(self.stored.get(receiver_id))

    def store(self, receiver_id, label, password):
        self.stored[receiver_id] = password

    def clear(self, receiver_id):
        self.stored.pop(receiver_id, None)
        self.cleared.append(receiver_id)


def spin(predicate=lambda: False, seconds=2.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@unittest.skipUnless(_paths.have_gi(), "PyGObject is not installed")
class Manager(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store.load(Path(self.directory.name) / "state.json")
        self.control = FakeControl()
        self.discovery = FakeDiscovery()
        self.passwords = FakePasswords()
        self.failures = []
        self.probe_results = {}
        self.manager = airplay.AirPlayManager(
            self.store, self.control, self.passwords, self.discovery, lambda: None, self.failures.append,
            probe=lambda address, port, password=None: self.probe_results.get(password, raop.ProbeResult(raop.PROBE_OK)))
        self.mac = raop.Receiver("0615378145AF@Receiver One’s MacBook Pro", "mac.local", "192.0.2.20", 7000, 2,
                                 dict(MAC_TXT))
        self.discovery.found[self.mac.id] = self.mac

    def tearDown(self):
        self.directory.cleanup()

    def connect(self, password=None):
        replies = []
        self.manager.connect(self.mac.id, password, replies.append)
        spin(lambda: self.control.loaded(self.mac.id) or replies)
        if self.control.loaded(self.mac.id) and not replies:
            self.manager.outputs_changed({"alsa_output.speaker", self.mac.node_name})
        spin(lambda: replies)
        return replies[0]

    def test_browsing_lists_everything_only_while_the_picker_is_open(self):
        self.assertEqual(self.manager.receivers(), [])
        self.manager.start_browsing(":1.5")
        self.assertTrue(self.discovery.browsing)
        self.assertEqual([r["id"] for r in self.manager.receivers()], [self.mac.id])
        self.manager.stop_browsing(":1.5")
        self.assertFalse(self.discovery.browsing)
        self.assertEqual(self.manager.receivers(), [])

    def test_two_pickers_keep_browsing_until_both_close(self):
        self.manager.start_browsing(":1.5")
        self.manager.start_browsing(":1.9")
        self.manager.stop_browsing(":1.5")
        self.assertTrue(self.discovery.browsing)
        self.manager.stop_browsing(":1.9")
        self.assertFalse(self.discovery.browsing)

    def test_choosing_a_receiver_creates_one_sink_uses_it_and_remembers_it(self):
        self.assertIsNone(self.connect())
        self.assertEqual(list(self.control.modules), [self.mac.id])
        self.assertEqual(self.control.defaults, [self.mac.node_name])
        self.assertIn(self.mac.id, Store.load(self.store.path).receivers)
        self.assertEqual(self.discovery.watched, {self.mac.id: self.mac.service_name})
        entry = self.manager.receivers()[0]
        self.assertEqual((entry["state"], entry["remembered"]), ("connected", True))

    def test_password_required(self):
        self.probe_results[None] = raop.ProbeResult(raop.PROBE_PASSWORD_REQUIRED)
        error = self.connect()
        self.assertEqual(error.code, "PasswordRequired")
        self.assertIn("needs a password", error.message)
        self.assertEqual(self.control.modules, {})
        self.assertEqual(self.store.receivers, {})

    def test_password_accepted_is_kept_in_the_keyring_not_the_state_file(self):
        self.probe_results[None] = raop.ProbeResult(raop.PROBE_PASSWORD_REQUIRED)
        self.assertIsNone(self.connect("hunter2"))
        self.assertEqual(self.passwords.stored[self.mac.id], "hunter2")
        self.assertEqual(self.control.modules[self.mac.id]["raop.password"], "hunter2")
        self.assertNotIn("hunter2", self.store.path.read_text(encoding="utf-8"))

    def test_wrong_password(self):
        self.probe_results["nope"] = raop.ProbeResult(raop.PROBE_PASSWORD_INCORRECT)
        self.assertEqual(self.connect("nope").code, "PasswordIncorrect")

    def test_stale_stored_password_asks_again(self):
        self.passwords.stored[self.mac.id] = "old"
        self.probe_results["old"] = raop.ProbeResult(raop.PROBE_PASSWORD_INCORRECT)
        self.assertEqual(self.connect().code, "PasswordRequired")
        self.assertIn(self.mac.id, self.passwords.cleared)

    def test_owner_only_receiver(self):
        self.probe_results[None] = raop.ProbeResult(raop.PROBE_DENIED)
        error = self.connect()
        self.assertEqual(error.code, "Denied")
        self.assertEqual(self.manager.receivers(), [])  # not browsing, not remembered

    def test_receiver_that_left(self):
        del self.discovery.found[self.mac.id]
        self.assertEqual(self.connect().code, "NotFound")

    def test_remembered_receiver_returns_without_taking_the_sound(self):
        self.store.receivers[self.mac.id] = RememberedReceiver(self.mac.id, self.mac.name, self.mac.service_name)
        self.manager.reconcile()
        self.assertIn(self.mac.id, self.control.modules)
        self.assertEqual(self.control.defaults, [])

    def test_remembered_receiver_leaving_removes_its_output_after_a_grace_period(self):
        self.store.receivers[self.mac.id] = RememberedReceiver(self.mac.id, self.mac.name, self.mac.service_name)
        self.manager.reconcile()
        original = airplay.ABSENT_GRACE_MS
        airplay.ABSENT_GRACE_MS = 50
        try:
            del self.discovery.found[self.mac.id]
            self.manager.reconcile()
            self.assertIn(self.mac.id, self.control.modules)
            self.assertTrue(spin(lambda: self.mac.id not in self.control.modules, 2))
        finally:
            airplay.ABSENT_GRACE_MS = original

    def test_address_change_recreates_the_sink(self):
        self.store.receivers[self.mac.id] = RememberedReceiver(self.mac.id, self.mac.name, self.mac.service_name)
        self.manager.reconcile()
        moved = raop.Receiver(self.mac.service_name, "mac.local", "192.0.2.77", 7000, 2, dict(MAC_TXT))
        self.discovery.found[self.mac.id] = moved
        self.manager.reconcile()
        self.assertEqual(self.control.modules[self.mac.id]["raop.ip"], "192.0.2.77")

    def test_remembered_password_receiver_without_a_stored_password_waits(self):
        self.store.receivers[self.mac.id] = RememberedReceiver(self.mac.id, self.mac.name, self.mac.service_name,
                                                               has_password=True)
        self.manager.reconcile()
        self.assertEqual(self.control.modules, {})
        self.assertEqual(self.manager.receivers()[0]["error"], raop.PROBE_PASSWORD_REQUIRED)

    def test_sink_that_destroyed_itself_is_reported(self):
        self.assertIsNone(self.connect())
        self.control.alive[self.mac.id] = False
        self.manager.outputs_changed({"alsa_output.speaker"})
        self.assertEqual(self.failures, ["Receiver One’s MacBook Pro"])
        self.assertEqual(self.control.modules, {})
        self.assertEqual(self.manager.receivers()[0]["error"], "failed")

    def test_forget(self):
        self.probe_results[None] = raop.ProbeResult(raop.PROBE_PASSWORD_REQUIRED)
        self.connect("hunter2")
        self.assertTrue(self.manager.forget(self.mac.id))
        self.assertEqual(self.control.modules, {})
        self.assertEqual(Store.load(self.store.path).receivers, {})
        self.assertEqual(self.discovery.watched, {})
        self.assertNotIn(self.mac.id, self.passwords.stored)
        self.assertFalse(self.manager.forget(self.mac.id))


if __name__ == "__main__":
    unittest.main()
