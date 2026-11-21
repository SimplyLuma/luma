# SPDX-License-Identifier: Apache-2.0
"""The audio capture backend's own decisions, without a bus.

What matters: a restore token's data is honoured only when it still names a
choice this session may make; nothing is captured before a choice; the
capture is closed with the session; and the caller is always answered.
"""

import unittest

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from luma_portal import audiocapture  # noqa: E402


class Invocation:
    def __init__(self, method=""):
        self.method = method
        self.value = None

    def return_value(self, value):
        self.value = value

    def get_method_name(self):
        return self.method


class Result:
    def __init__(self, value):
        self.value = value


class Source:
    def __init__(self, value):
        self.value = value

    def call_finish(self, result):
        if isinstance(result.value, GLib.Error):
            raise result.value
        return result.value


class Connection:
    """Records registrations and calls; answers CreateCapture at once."""

    def __init__(self, capture_error=None):
        self.calls = []
        self.registered = {}
        self.capture_error = capture_error
        self._next = 1

    def register_object(self, path, _iface, call, _get, _set):
        self._next += 1
        self.registered[path] = call
        return self._next

    def unregister_object(self, _registration):
        return True

    def emit_signal(self, *_args):
        pass

    def signal_subscribe(self, *_args):
        return 1

    def call(self, name, path, iface, method, parameters, reply_type, flags, timeout,
             cancellable, callback, user_data):
        if method == "GetActive":
            return  # the session lock (lock.py): no screen shield answers here
        self.calls.append((method, parameters.unpack() if parameters else None))
        if callback is not None and method == "CreateCapture":
            value = self.capture_error or GLib.Variant("(sut)", ("cap1", 77, 1077))
            callback(Source(value), Result(value), user_data)


def call(portal, method, *args):
    invocation = Invocation(method)
    portal._call(None, ":1.5", "/", "", method, GLib.Variant(_SIGNATURES[method], args), invocation)
    return invocation.value.unpack() if invocation.value is not None else None


_SIGNATURES = {
    "CreateSession": "(oosa{sv})",
    "SelectSources": "(oosa{sv})",
    "Start": "(oossa{sv})",
}

SESSION = "/org/freedesktop/portal/desktop/session/1_5/t"
REQUEST = "/org/freedesktop/portal/desktop/request/1_5/r"


class RestoreData(unittest.TestCase):
    def test_round_trip(self):
        choice = {"mode": "apps", "apps": ["org.example.Player"], "names": ["Player"]}
        data = audiocapture.restore_data(choice).unpack()
        self.assertEqual(audiocapture.choice_from_restore(data, 3), choice)

    def test_a_choice_no_longer_allowed_asks_again(self):
        apps = audiocapture.restore_data({"mode": "apps", "apps": ["a"], "names": []}).unpack()
        everything = audiocapture.restore_data({"mode": "system"}).unpack()
        self.assertIsNone(audiocapture.choice_from_restore(apps, audiocapture.SOURCE_SYSTEM))
        self.assertIsNone(audiocapture.choice_from_restore(everything, audiocapture.SOURCE_APPLICATIONS))

    def test_foreign_or_broken_data_asks_again(self):
        self.assertIsNone(audiocapture.choice_from_restore(("other", 1, {}), 3))
        self.assertIsNone(audiocapture.choice_from_restore(("luma", 99, {}), 3))
        self.assertIsNone(audiocapture.choice_from_restore(("luma", 1, {"mode": "apps"}), 3))
        self.assertIsNone(audiocapture.choice_from_restore("junk", 3))


class Flow(unittest.TestCase):
    def setUp(self):
        self.pickers = []
        self.connection = Connection()
        self.portal = audiocapture.AudioCapturePortal(self.connection, "/org/freedesktop/portal/desktop",
                                                      self.show_picker)

    def show_picker(self, app_id, name, types, multiple, parent, on_done):
        picker = type("Picker", (), {"closed": False, "close": lambda s: setattr(s, "closed", True)})()
        self.pickers.append((app_id, types, multiple, on_done, picker))
        return picker

    def start_session(self, options=None):
        self.assertEqual(call(self.portal, "CreateSession", REQUEST, SESSION, "org.example.Rec", {})[0], 0)
        self.assertEqual(call(self.portal, "SelectSources", REQUEST, SESSION, "org.example.Rec", options or {})[0], 0)

    def test_nothing_is_captured_before_the_choice(self):
        self.start_session({"types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", True)})
        invocation = Invocation("Start")
        self.portal._call(None, ":1.5", "/", "", "Start",
                          GLib.Variant("(oossa{sv})", (REQUEST, SESSION, "org.example.Rec", "", {})), invocation)
        self.assertIsNone(invocation.value)
        self.assertFalse([c for c in self.connection.calls if c[0] == "CreateCapture"])
        app_id, types, multiple, on_done, _picker = self.pickers[0]
        self.assertEqual((app_id, types, multiple), ("org.example.Rec", 1, True))
        on_done({"mode": "apps", "apps": ["org.example.Player"], "names": ["Player"]})
        response, results = invocation.value.unpack()
        self.assertEqual(response, 0)
        ((node, props),) = results["streams"]
        self.assertEqual((node, props["pipewire-serial"], props["applications"]), (77, 1077, ["Player"]))
        self.assertNotIn("restore_data", results)
        created = [c for c in self.connection.calls if c[0] == "CreateCapture"][0][1]
        self.assertEqual(created, ("org.example.Rec", "apps", ["org.example.Player"], False, "Player"))

    def test_no_picker_while_locked(self):
        class Locked:
            locked = True
            waiting = []

            def when_unlocked(self, callback):
                self.waiting.append(callback)
        lock = Locked()
        self.portal._lock = lock
        self.start_session()
        invocation = Invocation("Start")
        self.portal._call(None, ":1.5", "/", "", "Start",
                          GLib.Variant("(oossa{sv})", (REQUEST, SESSION, "org.example.Rec", "", {})), invocation)
        self.assertEqual(self.pickers, [], "nothing is asked while locked")
        self.assertIsNone(invocation.value)
        lock.locked = False
        for callback in lock.waiting:
            callback()
        self.assertEqual(len(self.pickers), 1, "asked once the session is open")

    def test_cancel_answers_and_captures_nothing(self):
        self.start_session()
        invocation = Invocation("Start")
        self.portal._call(None, ":1.5", "/", "", "Start",
                          GLib.Variant("(oossa{sv})", (REQUEST, SESSION, "org.example.Rec", "", {})), invocation)
        self.pickers[0][3](None)
        self.assertEqual(invocation.value.unpack()[0], audiocapture.PORTAL_CANCELLED)
        self.assertFalse([c for c in self.connection.calls if c[0] == "CreateCapture"])

    def test_restore_skips_the_picker_and_persists(self):
        data = audiocapture.restore_data({"mode": "system"})
        self.start_session({"restore_data": data, "persist_mode": GLib.Variant("u", 2),
                            "audio_mode": GLib.Variant("u", 4)})
        response, results = call(self.portal, "Start", REQUEST, SESSION, "org.example.Rec", "", {})
        self.assertEqual(response, 0)
        self.assertFalse(self.pickers)
        self.assertEqual(results["persist_mode"], 2)
        self.assertEqual(results["restore_data"][0], "luma")
        created = [c for c in self.connection.calls if c[0] == "CreateCapture"][0][1]
        self.assertEqual(created[1:4], ("system", [], True))

    def test_closing_the_session_closes_the_capture(self):
        self.start_session({"restore_data": audiocapture.restore_data({"mode": "system"})})
        call(self.portal, "Start", REQUEST, SESSION, "org.example.Rec", "", {})
        self.connection.registered[SESSION](None, None, None, None, None, None, Invocation("Close"))
        self.assertIn(("CloseCapture", ("cap1",)), self.connection.calls)

    def test_a_failed_capture_still_answers(self):
        self.connection.capture_error = GLib.Error("no")
        self.start_session({"restore_data": audiocapture.restore_data({"mode": "system"})})
        response, _ = call(self.portal, "Start", REQUEST, SESSION, "org.example.Rec", "", {})
        self.assertEqual(response, audiocapture.PORTAL_CANCELLED)


if __name__ == "__main__":
    unittest.main()
