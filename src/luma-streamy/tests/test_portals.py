# SPDX-License-Identifier: Apache-2.0
"""How a share ends, without a bus: a refused or failed restore token falls back to the
picker once, every other ending reaches ``failed`` with a reason the window words."""
from __future__ import annotations

import unittest

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from luma_streamy import portals  # noqa: E402


class Refused(Exception):
    pass


class FakeBus:
    """Scripted portal: ``answers[(interface, method)]`` is a list of (code, results) or an error
    message, used in turn; properties come from ``props``."""

    def __init__(self, answers, props):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.props = props
        self.calls = []
        self.subscriptions = {}

    def signal_subscribe(self, _bus, _iface, _signal, path, _arg, _flags, callback):
        self.subscriptions[path] = callback
        return len(self.subscriptions)

    def signal_unsubscribe(self, _subscription):
        pass

    def call(self, _bus, path, interface, method, params, _reply, _flags, _timeout, _cancel, callback, *_data):
        if interface == "org.freedesktop.portal.Session":
            self.calls.append((interface, method, {}))
            return
        last = params.get_child_value(params.n_children() - 1)
        options = {}
        for i in range(last.n_children()):
            entry = last.get_child_value(i)
            options[entry.get_child_value(0).get_string()] = entry.get_child_value(1).get_variant()
        self.calls.append((interface, method, options))
        answer = self.answers[(interface, method)].pop(0)
        if isinstance(answer, str):
            callback(self, Refused(answer))
            return
        callback(self, None)
        handle = f"/org/freedesktop/portal/desktop/request/sender/{options['handle_token'].unpack()}"
        code, results = answer
        self.subscriptions[handle](self, None, handle, None, None, GLib.Variant("(ua{sv})", (code, results)))

    def call_finish(self, result):
        if isinstance(result, Refused):
            raise GLib.Error.new_literal(GLib.quark_from_string("portal"), str(result), 0)

    def call_with_unix_fd_list(self, *args):
        args[-1](self, None)

    def call_with_unix_fd_list_finish(self, _result):
        class Fds:
            @staticmethod
            def get(_index):
                return 99
        return GLib.Variant("(h)", (0,)), Fds()

    def call_sync(self, _bus, _path, _iface, _method, params, *_rest):
        interface, name = params.unpack()
        if (interface, name) not in self.props:
            raise GLib.Error.new_literal(GLib.quark_from_string("portal"), "no such property", 0)
        return GLib.Variant("(v)", (GLib.Variant("u", self.props[(interface, name)]),))


SC, AC = portals.SCREENCAST, portals.AUDIOCAPTURE
SESSION = (0, {"session_handle": GLib.Variant("s", "/org/freedesktop/portal/desktop/session/sender/s")})
SELECTED = (0, {})


def started(token="new-token", **props):
    streams = [(52, {"pipewire-serial": GLib.Variant("t", 4031), **props})]
    return (0, {"streams": GLib.Variant("a(ua{sv})", streams), "restore_token": GLib.Variant("s", token)})


def make(answers, props=None):
    p = portals.Portals.__new__(portals.Portals)
    p.bus = FakeBus(answers, props if props is not None else {(SC, "version"): 5, (SC, "AvailableCursorModes"): 7})
    p.sender = "sender"
    p.captures = {}
    p.on_ready = None
    return p


class ShareTests(unittest.TestCase):
    def run_share(self, p, **kwargs):
        out = {}
        p.screencast("w", window=False, done=lambda c: out.setdefault("done", c),
                     failed=lambda r: out.setdefault("failed", r), **kwargs)
        return out

    def selects(self, p, interface=SC):
        return [o for i, m, o in p.bus.calls if i == interface and m == "SelectSources"]

    def test_a_fresh_pick(self):
        p = make({(SC, "CreateSession"): [SESSION], (SC, "SelectSources"): [SELECTED],
                  (SC, "Start"): [started()]})
        out = self.run_share(p, restore_token=None)
        capture = out["done"]
        self.assertEqual((capture.node, capture.restore_token, capture.pool), ("4031", "new-token", [99]))
        self.assertNotIn("restore_token", self.selects(p)[0])
        self.assertEqual(self.selects(p)[0]["persist_mode"].unpack(), portals.PERSIST_UNTIL_REVOKED)

    def test_a_refused_token_falls_back_to_the_picker(self):
        p = make({(SC, "CreateSession"): [SESSION, SESSION],
                  (SC, "SelectSources"): ["Restore token is not valid", SELECTED],
                  (SC, "Start"): [started()]})
        out = self.run_share(p, restore_token="bad token")
        self.assertIn("done", out)
        first, second = self.selects(p)
        self.assertEqual(first["restore_token"].unpack(), "bad token")
        self.assertNotIn("restore_token", second)

    def test_a_restore_that_fails_falls_back_once(self):
        p = make({(SC, "CreateSession"): [SESSION, SESSION], (SC, "SelectSources"): [SELECTED, SELECTED],
                  (SC, "Start"): [(2, {}), started()]})
        self.assertIn("done", self.run_share(p, restore_token="old"))
        p = make({(SC, "CreateSession"): [SESSION, SESSION], (SC, "SelectSources"): [SELECTED, SELECTED],
                  (SC, "Start"): [(2, {}), (2, {})]})
        self.assertEqual(self.run_share(p, restore_token="old"), {"failed": "failed"})

    def test_cancel_is_the_persons_and_never_retried(self):
        p = make({(SC, "CreateSession"): [SESSION], (SC, "SelectSources"): [SELECTED], (SC, "Start"): [(1, {})]})
        self.assertEqual(self.run_share(p, restore_token="old"), {"failed": "cancelled"})
        self.assertEqual(len(self.selects(p)), 1)

    def test_a_picker_that_timed_out_says_so(self):
        clock = iter([100.0, 125.1])
        real = portals.time.monotonic
        portals.time.monotonic = lambda: next(clock)
        try:
            p = make({(SC, "CreateSession"): [SESSION], (SC, "SelectSources"): [SELECTED],
                      (SC, "Start"): [(1, {})]})
            self.assertEqual(self.run_share(p, restore_token=None), {"failed": "timeout"})
        finally:
            portals.time.monotonic = real

    def test_a_bus_error_is_a_failure_not_its_message(self):
        p = make({(SC, "CreateSession"): ["org.freedesktop.DBus.Error.ServiceUnknown"]})
        self.assertEqual(self.run_share(p, restore_token=None), {"failed": "failed"})


class AppAudioTests(unittest.TestCase):
    def test_without_the_portal_it_is_unavailable(self):
        p = make({})
        out = {}
        p.app_audio("a", restore_token=None, done=out.setdefault, failed=lambda r: out.setdefault("failed", r))
        self.assertEqual(out, {"failed": "unavailable"})

    def test_the_chosen_apps_come_with_the_capture(self):
        p = make({(AC, "CreateSession"): [SESSION], (AC, "SelectSources"): [SELECTED],
                  (AC, "Start"): [started(applications=GLib.Variant("as", ["Firefox", "Spotify"]),
                                          source_type=GLib.Variant("u", 1))]},
                 {(AC, "AvailableSourceTypes"): 3, (AC, "version"): 1})
        out = {}
        p.app_audio("a", restore_token=None, done=lambda c: out.setdefault("done", c),
                    failed=lambda r: out.setdefault("failed", r))
        self.assertEqual(out["done"].applications, ["Firefox", "Spotify"])
        (select,) = [o for i, m, o in p.bus.calls if m == "SelectSources"]
        self.assertEqual(select["types"].unpack(), portals.APPLICATIONS)
        self.assertEqual(select["audio_mode"].unpack(), portals.EXCLUDE_SELF)
        self.assertTrue(select["multiple"].unpack())


if __name__ == "__main__":
    unittest.main()
