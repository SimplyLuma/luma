# SPDX-License-Identifier: Apache-2.0
"""The D-Bus method table, its polkit mapping, and Apply().

Apply() used to reboot through logind as root whenever anything at all was
staged or a rollback was the default. logind never asks polkit about root and,
before systemd 257, ignored block inhibitors for root, so a person allowed only
"restart to finish updating" could restart past other people's sessions and
inhibitors, and could finish a deployment the agent never prepared.
"""

import json
from pathlib import Path
import re
import threading
import unittest
import xml.etree.ElementTree as ET

import fakes
from fakes import commit, graph_doc, release
from luma_update.dbus_interface import (ACTION_APPLY, ACTION_CHANNEL, ACTION_CHECK, ACTION_DOWNLOAD,
                                        ACTION_PREVIEW, ACTION_ROLLBACK, ERRORS, LOGIN1_REBOOT,
                                        LOGIN1_REBOOT_MULTIPLE_SESSIONS, METHODS, introspection_xml)
from luma_update.engine import Busy, NothingToDo, NotOurs

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    from luma_update import daemon as daemonmod, system
except (ImportError, ValueError):  # no GObject introspection here
    daemonmod = None

PACKAGE = Path(__file__).resolve().parents[1]
CONTRACT = PACKAGE.parents[1] / "docs" / "os" / "luma-update.md"

EXPECTED_ACTIONS = {
    "Check": ACTION_CHECK, "Download": ACTION_DOWNLOAD, "Cancel": ACTION_DOWNLOAD, "Apply": ACTION_APPLY, "SetChannel": ACTION_CHANNEL,
    "SetChannelNow": ACTION_CHANNEL, "Rollback": ACTION_ROLLBACK, "EnrollPreview": ACTION_PREVIEW,
    "LeavePreview": ACTION_PREVIEW, "AcknowledgeRollback": ACTION_CHECK,
    "SetAutomaticDownload": ACTION_DOWNLOAD, "IgnoreVersion": ACTION_CHECK,
    "ClearIgnoredVersion": ACTION_CHECK, "AdoptChannel": ACTION_CHANNEL, "Automatic": None,
}


class MethodTable(unittest.TestCase):
    def introspected(self):
        root = ET.fromstring(introspection_xml().split("\n", 2)[2])
        return {m.get("name") for m in root.find("interface").findall("method")}

    def test_every_introspected_method_is_dispatched_with_its_polkit_action(self):
        self.assertEqual(set(METHODS), self.introspected())
        self.assertEqual({name: action for name, (action, _wait) in METHODS.items()}, EXPECTED_ACTIONS)

    def test_every_action_exists_in_the_policy_and_apply_implies_nothing(self):
        policy = ET.parse(PACKAGE / "data/polkit/org.projectluma.update.policy").getroot()
        actions = {a.get("id"): a for a in policy.findall("action")}
        self.assertLessEqual({a for a in EXPECTED_ACTIONS.values() if a}, set(actions))
        for action in actions.values():
            self.assertEqual(action.findall("annotate"), [], action.get("id"))
        defaults = actions[ACTION_APPLY].find("defaults")
        self.assertEqual([defaults.find(k).text for k in ("allow_any", "allow_inactive", "allow_active")],
                         ["auth_admin_keep", "auth_admin_keep", "yes"])

    @unittest.skipUnless(CONTRACT.exists(), "the repository's docs are not beside the package")
    def test_contract_documents_the_same_table(self):
        text = CONTRACT.read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| `(\w+)\([^)]*\)` \| ([^|]+?) \|", text, re.M))
        documented = {name: (None if "uid 0" in cell else re.search(r"`([\w.-]+)`", cell).group(1))
                      for name, cell in rows.items()}
        self.assertEqual({k: v for k, v in documented.items() if k in METHODS}, EXPECTED_ACTIONS)
        self.assertLessEqual(set(METHODS), set(documented))
        for name in ERRORS:
            self.assertIn(f"`{name}`", text)


class ApplyPreconditions(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()
        self.restarts = []

    def tearDown(self):
        self.rig.close()

    def apply(self):
        return self.engine.apply(lambda: self.restarts.append(True))

    def test_nothing_waiting(self):
        with self.assertRaises(NothingToDo):
            self.apply()
        self.assertEqual(self.restarts, [])

    def test_own_staged_update_restarts(self):
        self.engine.automatic()
        self.assertEqual(self.apply(), "update")
        self.assertEqual(self.restarts, [True])

    def test_deployment_staged_by_something_else_is_not_ours(self):
        self.rig.backend.list.insert(0, fakes.FakeDeployment(commit(77), "44.1", "luma:luma/1/x86_64/stable",
                                                             staged=True))
        with self.assertRaises(NotOurs):
            self.apply()
        self.assertEqual(self.restarts, [])

    def test_own_rollback_restarts_but_a_foreign_one_does_not(self):
        self.rig.backend.list.append(fakes.FakeDeployment(commit(0), "0.9.0", "luma:luma/1/x86_64/stable"))
        self.engine.rollback()
        self.assertEqual(self.apply(), "rollback")
        # Someone else changes the default afterwards (rpm-ostree rollback twice, then once more).
        self.rig.backend.list = [fakes.FakeDeployment(commit(5), "0.8.0", "luma:luma/1/x86_64/stable"),
                                 *[d for d in self.rig.backend.list if d.booted]]
        with self.assertRaises(NotOurs):
            self.apply()
        self.assertEqual(self.restarts, [True])

    def test_busy_while_an_operation_or_transaction_runs(self):
        self.engine.automatic()
        self.rig.backend.active = ("Cleanup", ":1.9", "/")
        with self.assertRaises(Busy):
            self.apply()
        self.rig.backend.active = None
        self.engine._operation.acquire()
        try:
            with self.assertRaises(Busy):
                self.apply()
        finally:
            self.engine._operation.release()
        self.assertEqual(self.restarts, [])

    def test_a_refused_restart_leaves_the_update_staged(self):
        self.engine.automatic()

        def refuse():
            raise PermissionError("not allowed")
        with self.assertRaises(PermissionError):
            self.engine.apply(refuse)
        self.assertEqual(self.engine.status().state, "staged")
        self.assertFalse(self.engine._operation.locked())


class FakeInvocation:
    def __init__(self):
        self.errors = []
        self.values = []

    def return_dbus_error(self, name, message):
        self.errors.append((name, message))

    def return_value(self, value):
        self.values.append(value)


@unittest.skipIf(daemonmod is None, "PyGObject is not available")
class Dispatch(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.daemon = daemonmod.Daemon.__new__(daemonmod.Daemon)
        self.daemon.engine = self.rig.engine()
        self.daemon.settings = self.rig.settings
        self.daemon.connection = None
        self.daemon.workers = 0
        self.daemon.workers_lock = threading.Lock()
        self.daemon.last_activity = 0.0
        self.started = []
        self.asked = []
        self.allow = True
        self.daemon._start = lambda work, invocation, wait: self.started.append((work, wait))
        self.daemon._authorize = lambda sender, action, callback: (self.asked.append((sender, action)),
                                                                   callback(self.allow))

    def tearDown(self):
        self.rig.close()

    def call(self, method, args=()):
        signature = {"SetChannel": "(s)", "SetChannelNow": "(s)", "EnrollPreview": "(ss)"}.get(method, "()")
        invocation = FakeInvocation()
        self.daemon._method_call(None, ":1.42", "/org/projectluma/Update1", "org.projectluma.Update1", method,
                                 GLib.Variant(signature, args), invocation)
        return invocation

    def test_each_method_asks_polkit_for_its_action_before_starting(self):
        arguments = {"SetChannel": ("beta",), "SetChannelNow": ("stable",), "EnrollPreview": ("beta", "token-123")}
        for method, (action, wait) in METHODS.items():
            if action is None:
                continue
            self.started.clear()
            self.asked.clear()
            self.call(method, arguments.get(method, ()))
            self.assertEqual(self.asked, [(":1.42", action)], method)
            self.assertEqual([w for _work, w in self.started], [wait], method)

    def test_refused_methods_never_start(self):
        self.allow = False
        for method in ("Download", "Apply", "Rollback", "SetChannelNow"):
            self.started.clear()
            invocation = self.call(method, ("stable",) if method == "SetChannelNow" else ())
            self.assertEqual(self.started, [], method)
            self.assertEqual(invocation.errors[0][0], "org.projectluma.Update1.Error.NotAuthorized")

    def test_automatic_is_for_root_only(self):
        from unittest import mock
        with mock.patch.object(system, "caller_uid", return_value=1000):
            invocation = self.call("Automatic")
        self.assertEqual(self.started, [])
        self.assertEqual(invocation.errors[0][0], "org.projectluma.Update1.Error.NotAuthorized")
        with mock.patch.object(system, "caller_uid", return_value=0):
            self.call("Automatic")
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.asked, [])

    def test_unknown_method(self):
        invocation = FakeInvocation()
        self.daemon._method_call(None, ":1.42", "/", "org.projectluma.Update1", "Reboot", GLib.Variant("()", ()),
                                 invocation)
        self.assertEqual(invocation.errors[0][0], "org.projectluma.Update1.Error.InvalidArgument")


class FakeSystemBus:
    """logind, polkit and the bus daemon, answering like the real services."""

    def __init__(self, sessions, allowed=(), inhibited=False):
        self.sessions = sessions      # [(id, uid, class)]
        self.allowed = set(allowed)
        self.inhibited = inhibited
        self.calls = []

    def call_sync(self, bus, path, interface, method, parameters, reply_type, flags, timeout, cancellable):
        values = parameters.unpack() if parameters is not None else ()
        self.calls.append((bus, method, values))
        if method == "GetConnectionUnixUser":
            return GLib.Variant("(u)", (1000,))
        if method == "ListSessions":
            return GLib.Variant("(a(susso))", ([(sid, uid, f"user{uid}", "seat0", f"/org/freedesktop/login1/session/{sid}")
                                                for sid, uid, _class in self.sessions],))
        if method == "Get" and values[1] == "Class":
            sid = path.rsplit("/", 1)[1]
            return GLib.Variant("(v)", (GLib.Variant("s", next(c for s, _u, c in self.sessions if s == sid)),))
        if method == "CheckAuthorization":
            (_subject, action, _details, flags_value, _cancel) = values
            self.interactive = flags_value
            return GLib.Variant("((bba{ss}))", ((action in self.allowed, False, {}),))
        if method == "RebootWithFlags":
            if self.inhibited:
                raise Gio.DBusError.new_for_dbus_error("org.freedesktop.login1.BlockedByInhibitorLock",
                                                       "Operation denied due to active block inhibitor")
            return None
        raise AssertionError(f"unexpected call {method}")


@unittest.skipIf(daemonmod is None, "PyGObject is not available")
class RestartAuthorization(unittest.TestCase):
    def test_alone_needs_the_reboot_action(self):
        bus = FakeSystemBus([("2", 1000, "user"), ("c1", 60, "greeter"), ("5", 1001, "background")],
                            allowed={LOGIN1_REBOOT})
        self.assertEqual(system.authorize_restart(bus, ":1.42"), LOGIN1_REBOOT)
        self.assertEqual(bus.interactive, 1)

    def test_other_people_logged_in_needs_the_multiple_sessions_action(self):
        bus = FakeSystemBus([("2", 1000, "user"), ("7", 1001, "user")], allowed={LOGIN1_REBOOT})
        with self.assertRaises(system.NotAuthorized):
            system.authorize_restart(bus, ":1.42")
        checked = [values[1] for _bus, method, values in bus.calls if method == "CheckAuthorization"]
        self.assertEqual(checked, [LOGIN1_REBOOT_MULTIPLE_SESSIONS])
        bus = FakeSystemBus([("2", 1000, "user"), ("7", 1001, "user-early")],
                            allowed={LOGIN1_REBOOT_MULTIPLE_SESSIONS})
        self.assertEqual(system.authorize_restart(bus, ":1.42"), LOGIN1_REBOOT_MULTIPLE_SESSIONS)

    def test_reboot_honours_inhibitors_for_root(self):
        bus = FakeSystemBus([])
        system.reboot(bus)
        self.assertEqual(bus.calls[-1], ("org.freedesktop.login1", "RebootWithFlags", (1,)))
        with self.assertRaises(system.Inhibited):
            system.reboot(FakeSystemBus([], inhibited=True))
        self.assertNotIn("Reboot", [method for _bus, method, _values in bus.calls])


if __name__ == "__main__":
    unittest.main()
