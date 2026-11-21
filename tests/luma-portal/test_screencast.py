# SPDX-License-Identifier: Apache-2.0
"""The screen sharing backend's own logic, without a bus.

What is worth testing here is what the backend decides on its own: which
cursor mode Mutter is asked for, what an application is told about a stream,
and what restoring a share does: xdg-desktop-portal owns the tokens and gives
the backend back only its own restore data, which must restore the same choice
or fall back to the picker, never fail the share.
"""

import unittest

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from luma_portal import mutter, screencast  # noqa: E402


class FakeConnection:
    """Enough of Gio.DBusConnection for a Stream to be constructed."""

    def __init__(self):
        self.unsubscribed = []

    def signal_subscribe(self, *_args):
        return 7

    def signal_unsubscribe(self, subscription):
        self.unsubscribed.append(subscription)


class CursorModeTests(unittest.TestCase):
    def test_portal_modes_map_to_mutter(self):
        self.assertEqual(
            mutter.cursor_mode_for_portal(mutter.PORTAL_CURSOR_EMBEDDED),
            mutter.MUTTER_CURSOR_EMBEDDED)
        self.assertEqual(
            mutter.cursor_mode_for_portal(mutter.PORTAL_CURSOR_METADATA),
            mutter.MUTTER_CURSOR_METADATA)
        self.assertEqual(
            mutter.cursor_mode_for_portal(mutter.PORTAL_CURSOR_HIDDEN),
            mutter.MUTTER_CURSOR_HIDDEN)

    def test_an_unknown_mode_hides_the_cursor(self):
        # Never leak a pointer into a stream because of a mode we do not know.
        self.assertEqual(mutter.cursor_mode_for_portal(0),
                         mutter.MUTTER_CURSOR_HIDDEN)
        self.assertEqual(mutter.cursor_mode_for_portal(99),
                         mutter.MUTTER_CURSOR_HIDDEN)


class StreamTests(unittest.TestCase):
    def test_a_stream_reports_its_source_type(self):
        stream = mutter.Stream(FakeConnection(), "/s/1",
                               mutter.SOURCE_WINDOW, None)
        properties = stream.portal_properties()
        self.assertEqual(properties["source_type"].unpack(),
                         mutter.SOURCE_WINDOW)
        self.assertNotIn("position", properties)

    def test_a_monitor_stream_reports_where_it_is(self):
        stream = mutter.Stream(FakeConnection(), "/s/2",
                               mutter.SOURCE_MONITOR, (0, 0, 2880, 1800))
        properties = stream.portal_properties()
        self.assertEqual(properties["size"].unpack(), (2880, 1800))
        self.assertEqual(properties["position"].unpack(), (0, 0))

    def test_readiness_waits_for_every_node(self):
        connection = FakeConnection()
        session = mutter.ScreenCastSession.__new__(mutter.ScreenCastSession)
        session.streams = [
            mutter.Stream(connection, "/s/1", mutter.SOURCE_WINDOW, None),
            mutter.Stream(connection, "/s/2", mutter.SOURCE_WINDOW, None),
        ]
        self.assertFalse(session.ready)
        session.streams[0].announced = True
        self.assertFalse(session.ready)
        session.streams[1].announced = True
        self.assertTrue(session.ready)

    def settle(self):
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def test_the_serial_from_v2_is_what_the_application_targets(self):
        stream = mutter.Stream(FakeConnection(), "/s/3", mutter.SOURCE_MONITOR, None)
        heard = []
        stream.on_node = heard.append
        stream._on_node(None, None, None, None, "PipeWireStreamAdded", GLib.Variant("(u)", (83,)), None)
        stream._on_node_v2(None, None, None, None, "PipeWireStreamAddedV2", GLib.Variant(
            "(a{sv})", ({"node-id": GLib.Variant("u", 83), "node-serial": GLib.Variant("t", 4127)},)), None)
        self.assertEqual(heard, [])  # announced once both have been read
        self.settle()
        self.assertEqual(heard, [stream])
        self.assertTrue(stream.announced)
        self.assertEqual(stream.portal_properties()["pipewire-serial"].unpack(), 4127)

    def test_a_mutter_without_v2_reads_the_serial_from_pipewire(self):
        stream = mutter.Stream(FakeConnection(), "/s/4", mutter.SOURCE_MONITOR, None)
        heard, asked = [], []

        def read():
            asked.append(stream.node_id)
            stream.node_serial = 5150
            stream._done()

        stream._read_serial = read
        stream.on_node = heard.append
        stream._on_node(None, None, None, None, "PipeWireStreamAdded", GLib.Variant("(u)", (90,)), None)
        self.settle()
        self.assertEqual((asked, heard), ([90], [stream]))
        self.assertEqual(stream.portal_properties()["pipewire-serial"].unpack(), 5150)

    def test_the_serial_in_a_pw_dump(self):
        dump = '[{"id": 89, "info": {"props": {"object.serial": 300}}}, {"id": 90, "info": {"props": {"object.serial": 4127}}}]'
        self.assertEqual(mutter.serial_of(dump, 90), 4127)
        self.assertIsNone(mutter.serial_of(dump, 91))
        self.assertIsNone(mutter.serial_of("not json", 90))

    def test_a_session_with_no_stream_is_not_ready(self):
        session = mutter.ScreenCastSession.__new__(mutter.ScreenCastSession)
        session.streams = []
        self.assertFalse(session.ready)


class UnpackTests(unittest.TestCase):
    def test_variants_become_plain_values(self):
        options = {
            "types": GLib.Variant("u", 3),
            "multiple": GLib.Variant("b", True),
        }
        self.assertEqual(screencast.unpack(options),
                         {"types": 3, "multiple": True})

    def test_nothing_is_an_empty_dict(self):
        self.assertEqual(screencast.unpack(None), {})


class RestoreDataTests(unittest.TestCase):
    """xdg-desktop-portal keeps our restore data and hands it back; tokens are its business."""

    CHOICE = {"monitors": ["eDP-1"], "windows": [], "cursor-mode": 2,
              "hide-notifications": True, "allow-control": False}

    def through_the_bus(self, value):
        # As the frontend stores and returns it: serialised, then parsed again.
        return GLib.Variant.new_from_bytes(value.get_type(), value.get_data_as_bytes(), False)

    def test_a_choice_comes_back(self):
        data = self.through_the_bus(screencast.restore_data(self.CHOICE))
        self.assertEqual(data.get_type_string(), "(suv)")
        self.assertEqual(screencast.restored_choice(data), self.CHOICE)
        self.assertEqual(screencast.restored_choice(data.unpack()), self.CHOICE)

    def test_another_backends_data_is_ignored(self):
        other = GLib.Variant("(suv)", ("gnome", 1, GLib.Variant("a{sv}", {})))
        self.assertIsNone(screencast.restored_choice(other))
        newer = GLib.Variant("(suv)", ("luma", 99, GLib.Variant("a{sv}", {
            "monitors": GLib.Variant("as", ["eDP-1"])})))
        self.assertIsNone(screencast.restored_choice(newer))

    def test_an_empty_choice_is_nothing(self):
        self.assertIsNone(screencast.restored_choice(
            screencast.restore_data({"monitors": [], "windows": []})))
        self.assertIsNone(screencast.restored_choice(None))
        self.assertIsNone(screencast.restored_choice("not restore data"))


class FakeInvocation:
    def __init__(self):
        self.answers = []

    def return_value(self, value):
        self.answers.append(value.unpack() if value is not None else None)


class FakeBus:
    """The backend's connection: objects it registers and the calls it makes."""

    def __init__(self):
        self.calls = []
        self._next = 1

    def register_object(self, *_args):
        self._next += 1
        return self._next

    def unregister_object(self, _registration):
        pass

    def signal_subscribe(self, *_args):
        return 1

    def emit_signal(self, *_args):
        pass

    def call(self, name, path, iface, method, params, reply_type, flags, timeout, cancellable, callback, *data):
        self.calls.append({"method": method, "timeout": timeout, "callback": callback})


class FakeCast:
    """Mutter's session: a monitor that is gone cannot be recorded."""

    gone = {"HDMI-1"}

    def __init__(self, _connection):
        self.streams = []
        self.on_closed = None
        self.ready = False

    def record_monitor(self, connector, _cursor_mode):
        if connector in self.gone:
            raise GLib.Error.new_literal(GLib.quark_from_string("test"), "Unknown monitor", 0)

    def record_window(self, _window_id, _cursor_mode):
        raise GLib.Error.new_literal(GLib.quark_from_string("test"), "Unknown window", 0)

    def start(self):
        pass

    def stop(self):
        pass


class BackendTests(unittest.TestCase):
    def setUp(self):
        self._real = mutter.ScreenCastSession
        mutter.ScreenCastSession = FakeCast
        self.fresh()

    def fresh(self):
        self.bus = FakeBus()
        self.portal = screencast.ScreenCastPortal(self.bus, "/org/freedesktop/portal/desktop")
        self.session = "/org/freedesktop/portal/desktop/session/1_1/s"
        self.portal._create_session(GLib.Variant("(oosa{sv})", ("/r/1", self.session, "", {})), FakeInvocation())

    def tearDown(self):
        mutter.ScreenCastSession = self._real

    def select(self, options):
        self.portal._select_sources(GLib.Variant("(oosa{sv})", ("/r/2", self.session, "", options)),
                                    FakeInvocation())

    def start(self):
        invocation = FakeInvocation()
        self.portal._start(GLib.Variant("(oossa{sv})", ("/r/3", self.session, "", "x11:1", {})), invocation)
        return invocation

    def shell_calls(self, method):
        return [c for c in self.bus.calls if c["method"] == method]

    def test_the_picker_never_times_out(self):
        self.select({"types": GLib.Variant("u", 1)})
        self.start()
        (choose,) = self.shell_calls("Choose")
        self.assertEqual(choose["timeout"], GLib.MAXINT)

    def test_restore_data_skips_the_picker(self):
        data = screencast.restore_data({"monitors": ["eDP-1"]})
        self.select({"restore_data": data, "persist_mode": GLib.Variant("u", 2)})
        self.start()
        self.assertEqual(self.shell_calls("Choose"), [])

    def test_a_choice_that_no_longer_applies_asks_again(self):
        for choice in ({"monitors": ["HDMI-1"]}, {"windows": [42]}):
            with self.subTest(choice=choice):
                self.fresh()
                self.select({"restore_data": screencast.restore_data(choice),
                             "persist_mode": GLib.Variant("u", 2)})
                invocation = self.start()
                self.assertEqual(len(self.shell_calls("Choose")), 1)
                self.assertEqual(invocation.answers, [], "the application waits for the picker")

    def test_a_restore_token_of_its_own_is_not_trusted(self):
        # xdg-desktop-portal never forwards a token; one that arrives anyway is not a choice.
        self.select({"restore_token": GLib.Variant("s", "0123456789abcdef")})
        self.start()
        self.assertEqual(len(self.shell_calls("Choose")), 1)


class FakeLock:
    """The session lock (lock.py), set by the test."""

    def __init__(self, locked=False):
        self.locked = locked
        self.waiting = []
        self.refreshed = 0

    def when_unlocked(self, callback):
        if not self.locked:
            callback()
        elif callback not in self.waiting:
            self.waiting.append(callback)

    def refresh(self):
        self.refreshed += 1

    def unlock(self):
        self.locked = False
        waiting, self.waiting = self.waiting, []
        for callback in waiting:
            callback()


class InhibitedCast(FakeCast):
    """Mutter while the session is locked: no new share starts."""

    def record_monitor(self, _connector, _cursor_mode):
        raise GLib.Error.new_literal(GLib.quark_from_string("g-io-error-quark"),
                                     "Session creation inhibited", 0)


class LockTests(unittest.TestCase):
    """Nothing is asked or started while locked; pickers come one at a time after unlock."""

    def setUp(self):
        self._real = mutter.ScreenCastSession
        mutter.ScreenCastSession = FakeCast
        self.bus = FakeBus()
        self.lock = FakeLock(locked=True)
        self.portal = screencast.ScreenCastPortal(self.bus, "/org/freedesktop/portal/desktop", self.lock)

    def tearDown(self):
        mutter.ScreenCastSession = self._real

    def session(self, name, options):
        handle = f"/org/freedesktop/portal/desktop/session/1_1/{name}"
        self.portal._create_session(GLib.Variant("(oosa{sv})", (f"/r/{name}c", handle, "", {})),
                                    FakeInvocation())
        self.portal._select_sources(GLib.Variant("(oosa{sv})", (f"/r/{name}s", handle, "", options)),
                                    FakeInvocation())
        invocation = FakeInvocation()
        self.portal._start(GLib.Variant("(oossa{sv})", (f"/r/{name}", handle, "", "", {})),
                           invocation)
        return handle, invocation

    def chooses(self):
        return [c for c in self.bus.calls if c["method"] == "Choose"]

    def settle(self):
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def test_no_picker_while_locked_and_one_after_unlock(self):
        _h, first = self.session("a", {"types": GLib.Variant("u", 2)})
        self.session("b", {"types": GLib.Variant("u", 2)})
        self.assertEqual(self.chooses(), [], "nothing is asked while locked")
        self.assertEqual(first.answers, [], "the application waits, unanswered")
        self.lock.unlock()
        self.assertEqual(len(self.chooses()), 1, "one picker at a time")

    def test_the_next_picker_comes_when_the_first_is_answered(self):
        self.session("a", {"types": GLib.Variant("u", 2)})
        _h, second = self.session("b", {"types": GLib.Variant("u", 2)})
        self.lock.unlock()
        (choose,) = self.chooses()

        class Denied:
            def call_finish(self, _result):
                return GLib.Variant("(ua{sv})", (screencast.PORTAL_CANCELLED, {}))
        choose["callback"](Denied(), None, None)
        self.settle()
        self.assertEqual(len(self.chooses()), 2)
        self.assertEqual(second.answers, [])

    def test_a_restored_share_waits_and_needs_no_picker(self):
        data = screencast.restore_data({"monitors": ["eDP-1"]})
        _h, invocation = self.session("r", {"restore_data": data, "persist_mode": GLib.Variant("u", 2)})
        self.assertEqual(invocation.answers, [])
        self.lock.unlock()
        self.assertEqual(self.chooses(), [])

    def test_a_request_closed_while_waiting_is_answered(self):
        handle, invocation = self.session("a", {"types": GLib.Variant("u", 2)})
        self.portal._sessions[handle].close()
        self.assertEqual(invocation.answers, [(screencast.PORTAL_CANCELLED, {})])
        self.lock.unlock()
        self.assertEqual(self.chooses(), [])

    def test_mutter_refusing_a_restore_as_locked_waits_instead_of_asking(self):
        mutter.ScreenCastSession = InhibitedCast
        self.lock.locked = False
        data = screencast.restore_data({"monitors": ["eDP-1"]})
        _h, invocation = self.session("r", {"restore_data": data, "persist_mode": GLib.Variant("u", 2)})
        self.assertEqual(self.chooses(), [], "a share refused as locked is not a reason to ask")
        self.assertEqual(invocation.answers, [])


class EndingTests(unittest.TestCase):
    """Every way a share ends tells the Shell, so no ring or pill outlives it."""

    def setUp(self):
        self._real = mutter.ScreenCastSession
        mutter.ScreenCastSession = FakeCast
        self.bus = FakeBus()
        self.bus.flush_sync = lambda _c: None
        self.portal = screencast.ScreenCastPortal(self.bus, "/org/freedesktop/portal/desktop", FakeLock())

    def tearDown(self):
        mutter.ScreenCastSession = self._real

    def open_session(self, name):
        handle = f"/org/freedesktop/portal/desktop/session/1_1/{name}"
        self.portal._create_session(GLib.Variant("(oosa{sv})", (f"/r/{name}c", handle, "", {})),
                                    FakeInvocation())
        return handle

    def closed(self):
        return [c for c in self.bus.calls if c["method"] == "SessionClosed"]

    def test_xdg_desktop_portal_closing_the_session_tells_the_shell(self):
        handle = self.open_session("a")

        class Close:
            def get_method_name(self):
                return "Close"

            def return_value(self, _v):
                pass
        self.portal._session_call(self.portal._sessions[handle], Close())
        self.assertEqual(len(self.closed()), 1)

    def test_stopping_the_backend_ends_every_share(self):
        self.open_session("a")
        self.open_session("b")
        self.portal.shutdown()
        self.assertEqual(len(self.closed()), 2)
        self.assertEqual(self.portal._sessions, {})

    def test_the_shell_learns_the_mutter_session(self):
        handle = self.open_session("a")
        session = self.portal._sessions[handle]
        session.cast = type("Cast", (), {"path": "/org/gnome/Mutter/ScreenCast/Session/u7"})()
        sent = []
        self.portal._call_shell = lambda method, params: sent.append((method, params))
        self.portal._tell_shell_started(session, {"windows": [3]})
        (method, params), = sent
        _handle, info = params.unpack()
        self.assertEqual(info["mutter-session"], "/org/gnome/Mutter/ScreenCast/Session/u7")


class OfferedCapabilityTests(unittest.TestCase):
    def test_luma_offers_windows_and_screens(self):
        self.assertEqual(screencast.AVAILABLE_SOURCE_TYPES,
                         mutter.SOURCE_MONITOR | mutter.SOURCE_WINDOW)

    def test_luma_does_not_offer_virtual_sources(self):
        # A virtual source is a monitor that does not exist; the picker has
        # nothing to preview for it, so it is not offered.
        self.assertFalse(screencast.AVAILABLE_SOURCE_TYPES &
                         mutter.SOURCE_VIRTUAL)

    def test_every_cursor_mode_is_offered(self):
        self.assertEqual(screencast.AVAILABLE_CURSOR_MODES, 1 | 2 | 4)


if __name__ == "__main__":
    unittest.main()
