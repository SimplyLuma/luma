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


SC = portals.SCREENCAST
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
    p.on_ended = None
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


class FakeGranted:
    """luma_appkit.audio_capture.AudioCapture, as much as Streamy uses."""

    def __init__(self, fd):
        self.fd, self.target, self.session_handle = fd, "4100", "/session/a"
        self.restore_token, self.description, self.on_closed, self.closed = "tok-2", "Spotify, Firefox", None, False

    def close(self):
        self.closed = True


class AppAudioTests(unittest.TestCase):
    """App audio goes through luma_appkit.audio_capture: Streamy keeps one capture per widget."""

    def setUp(self):
        import os
        import sys
        import types

        # A stand-in luma_appkit.audio_capture: the unit tests run without the platform installed.
        module = types.ModuleType("luma_appkit.audio_capture")
        module.APPLICATIONS, module.ALL_SOUND = 1, 2

        class AudioCaptureError(Exception):
            def __init__(self, message, cancelled=False):
                super().__init__(message)
                self.cancelled = cancelled

        module.AudioCaptureError = AudioCaptureError
        module.is_available = lambda _bus=None: self.available
        module.request_audio_capture = self.request
        package = types.ModuleType("luma_appkit")
        package.audio_capture = module
        self.saved = {name: sys.modules.get(name) for name in ("luma_appkit", "luma_appkit.audio_capture")}
        sys.modules["luma_appkit"], sys.modules["luma_appkit.audio_capture"] = package, module
        self.module = module
        self.available, self.asked, self.answers = True, [], []
        self.read, self.write = os.pipe()

    def tearDown(self):
        import os
        import sys

        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for fd in (self.read, self.write):
            try:
                os.close(fd)
            except OSError:
                pass

    def request(self, parent, callback, **options):
        self.asked.append(options)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            callback(None, answer)
        else:
            callback(answer, None)

    def share(self, token=None):
        p, out = make({}), {}
        p.app_audio("a", restore_token=token, done=lambda c: out.setdefault("done", c),
                    failed=lambda r: out.setdefault("failed", r))
        return p, out

    def test_without_the_portal_it_is_unavailable(self):
        self.available = False
        self.assertEqual(self.share()[1], {"failed": "unavailable"})
        self.assertEqual(self.asked, [])

    def test_a_capture_carries_what_is_heard(self):
        granted = FakeGranted(self.read)
        self.answers = [granted]
        p, out = self.share()
        capture = out["done"]
        self.assertEqual((capture.node, capture.description, capture.restore_token), ("4100", "Spotify, Firefox", "tok-2"))
        self.assertEqual(len(capture.pool), 1)
        self.assertNotEqual(capture.pool[0], self.read)  # each source owns a duplicate
        options = self.asked[0]
        self.assertEqual(options["types"], self.module.APPLICATIONS | self.module.ALL_SOUND)
        self.assertFalse(options["include_self"])
        self.assertTrue(options["multiple"] and options["persist"])
        p.close("a")
        self.assertTrue(granted.closed)
        self.assertNotIn("a", p.captures)

    def test_a_refused_token_falls_back_to_the_picker_once(self):
        self.answers = [self.module.AudioCaptureError("bad token"), FakeGranted(self.read)]
        _p, out = self.share(token="old")
        self.assertIn("done", out)
        self.assertEqual([o["restore_token"] for o in self.asked], ["old", None])

    def test_cancel_is_not_retried(self):
        self.answers = [self.module.AudioCaptureError("no", cancelled=True)]
        self.assertEqual(self.share(token="old")[1], {"failed": "cancelled"})
        self.assertEqual(len(self.asked), 1)

    def test_the_portal_ending_it_is_heard(self):
        granted = FakeGranted(self.read)
        self.answers = [granted]
        p, _out = self.share()
        ended = []
        p.on_ended = ended.append
        granted.on_closed(granted)
        self.assertEqual(ended, ["a"])
        self.assertNotIn("a", p.captures)



class CameraListTest(unittest.TestCase):
    def test_rates_labels_and_one_row_per_camera(self):
        from luma_streamy.portals import Camera, _rates, camera_label, unique_cameras

        self.assertEqual(_rates("video/x-raw, width=(int)1920, framerate=(fraction){ 30/1, 25/1 }"), [30, 25])
        self.assertEqual(_rates("image/jpeg, framerate=(fraction)[ 1/1, 60/1 ]"), [1, 60])
        self.assertEqual(_rates("video/x-raw, framerate=(fraction)30000/1001"), [30])
        self.assertEqual(camera_label("DJIPocket3 (V4L2)"), "DJIPocket3")
        rows = unique_cameras([Camera("1", "v", "Cam", (), "v4l2"), Camera("2", "l", "Cam", (), "libcamera"),
                               Camera("3", "d", "DJIPocket3", (), "v4l2")])
        self.assertEqual([c.target for c in rows], ["2", "3"])


class CameraModesTest(unittest.TestCase):
    def test_default_resolutions_rates_and_scaled_sizes(self):
        from luma_streamy.portals import default_mode, rates, resolutions, source_for_scaled

        modes = ((3840, 2160, 30), (3840, 2160, 25), (1920, 1080, 30), (1920, 1080, 60), (1280, 720, 30))
        self.assertEqual(default_mode(modes), (1920, 1080, 30))
        keys = [k for k, _l in resolutions(modes)]
        self.assertEqual(keys[:3], ["3840x2160", "1920x1080", "1280x720"])
        self.assertIn("854x480:scaled", keys)
        self.assertEqual(rates(modes, (1920, 1080)), [60, 30])
        self.assertEqual(source_for_scaled(modes, (854, 480)), (1280, 720))
        self.assertEqual(source_for_scaled(((3840, 2160, 30),), (1280, 720)), (3840, 2160))
        self.assertEqual(dict(resolutions(((3840, 2160, 30),)))["1280x720:scaled"], "720p, scaled")

if __name__ == "__main__":
    unittest.main()
