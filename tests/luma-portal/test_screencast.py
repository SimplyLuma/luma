# SPDX-License-Identifier: Apache-2.0
"""The screen sharing backend's own logic, without a bus.

What is worth testing here is what the backend decides on its own: which
cursor mode Mutter is asked for, what an application is told about a stream,
and above all that a restore token is never honoured for an application it was
not issued to.
"""

import os
import tempfile
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
        session.streams[0].node_id = 41
        self.assertFalse(session.ready)
        session.streams[1].node_id = 42
        self.assertTrue(session.ready)

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


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self._home.name
        self.store = screencast.TokenStore()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._saved
        self._home.cleanup()

    def test_no_token_is_issued_without_persistence(self):
        self.assertIsNone(self.store.issue(
            "org.example.App", {"windows": [1]}, screencast.PERSIST_NONE))

    def test_a_transient_token_comes_back_for_the_same_app(self):
        token = self.store.issue("org.example.App", {"windows": [1]},
                                 screencast.PERSIST_TRANSIENT)
        self.assertEqual(self.store.take("org.example.App", token),
                         {"windows": [1]})

    def test_a_token_is_never_honoured_for_another_app(self):
        token = self.store.issue("org.example.App", {"windows": [1]},
                                 screencast.PERSIST_PERSISTENT)
        self.assertIsNone(self.store.take("org.example.Other", token))
        # And the real owner is unaffected by someone else's attempt.
        self.assertEqual(self.store.take("org.example.App", token),
                         {"windows": [1]})

    def test_an_unknown_token_is_nothing(self):
        self.assertIsNone(self.store.take("org.example.App", "not-a-token"))

    def test_a_persistent_token_survives_a_restart(self):
        token = self.store.issue("org.example.App", {"monitors": ["eDP-1"]},
                                 screencast.PERSIST_PERSISTENT)
        self.assertEqual(
            screencast.TokenStore().take("org.example.App", token),
            {"monitors": ["eDP-1"]})

    def test_a_transient_token_does_not(self):
        token = self.store.issue("org.example.App", {"monitors": ["eDP-1"]},
                                 screencast.PERSIST_TRANSIENT)
        self.assertIsNone(screencast.TokenStore().take("org.example.App", token))

    def test_a_dropped_token_is_gone(self):
        token = self.store.issue("org.example.App", {"windows": [1]},
                                 screencast.PERSIST_PERSISTENT)
        self.store.drop(token)
        self.assertIsNone(self.store.take("org.example.App", token))
        self.assertIsNone(
            screencast.TokenStore().take("org.example.App", token))

    def test_tokens_are_not_guessable(self):
        first = self.store.issue("org.example.App", {},
                                 screencast.PERSIST_TRANSIENT)
        second = self.store.issue("org.example.App", {},
                                  screencast.PERSIST_TRANSIENT)
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 32)


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
