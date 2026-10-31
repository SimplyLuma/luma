# SPDX-License-Identifier: GPL-3.0-only
"""Clipboard readiness must not block or abort the browser's first window."""
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from native_clipboard import EngineClipboard


class Clipboard:
    def __init__(self):
        self.callbacks = {}
        self.disconnected = []
        self.format_reads = 0

    def connect(self, name, callback):
        self.callbacks[17] = callback
        return 17

    def disconnect(self, ident):
        self.disconnected.append(ident)
        del self.callbacks[ident]

    def get_formats(self):
        self.format_reads += 1
        return SimpleNamespace(get_mime_types=lambda: ['text/plain', 'unknown/type'])


class Connection:
    def __init__(self):
        self.calls = []
        self.unsubscribed = []

    def signal_subscribe(self, *args):
        return 23

    def signal_unsubscribe(self, ident):
        self.unsubscribed.append(ident)

    def call_sync(self, *args):
        raise AssertionError('Clipboard startup blocked the UI thread')

    def call(self, *args):
        self.calls.append(args)

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result

    def is_closed(self):
        return False


class ClipboardStartupTest(unittest.TestCase):
    def setUp(self):
        self.clipboard = Clipboard()
        self.connection = Connection()
        repository = ModuleType('gi.repository')
        repository.Gdk = SimpleNamespace(Display=SimpleNamespace(get_default=lambda:
            SimpleNamespace(get_clipboard=lambda: self.clipboard)))
        repository.Gio = SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0),
                                        DBusCallFlags=SimpleNamespace(NONE=0))
        repository.GLib = SimpleNamespace(Variant=lambda signature, value: (signature, value))
        gi = ModuleType('gi')
        gi.repository = repository
        modules = patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository})
        modules.start()
        self.addCleanup(modules.stop)
        self.bridge = EngineClipboard(SimpleNamespace(pointer_connection=self.connection,
            pointer_session='/owned/private/session'))

    def finish_enable(self, result=None):
        self.connection.calls[0][-1](self.connection, result)

    def test_cold_start_returns_before_handshake_and_defers_selection(self):
        self.assertFalse(self.bridge.enabled)
        self.assertEqual(self.clipboard.format_reads, 0)
        call, = self.connection.calls
        self.assertEqual(call[1], '/owned/private/session')
        self.assertEqual(call[3], 'EnableClipboard')
        self.assertEqual(call[-3], 5000)
        self.clipboard.callbacks[17]()
        self.assertEqual(len(self.connection.calls), 1)

    def test_later_success_publishes_current_supported_formats(self):
        self.finish_enable()
        self.assertTrue(self.bridge.enabled)
        self.assertEqual(self.bridge.formats, ('text/plain',))
        self.assertEqual(self.connection.calls[1][3], 'SetSelection')
        self.assertEqual(self.clipboard.format_reads, 1)

    def test_failed_readiness_does_not_raise_or_publish_selection(self):
        with self.assertLogs('native_clipboard', level='WARNING') as logs:
            self.finish_enable(TimeoutError('owned compositor is still starting'))
        self.assertIn('Clipboard bridge unavailable', logs.output[0])
        self.assertFalse(self.bridge.enabled)
        self.assertEqual(len(self.connection.calls), 1)
        self.assertEqual(self.clipboard.format_reads, 0)

    def test_close_during_startup_cleans_handlers_and_ignores_late_success(self):
        self.bridge.close()
        self.finish_enable()
        self.assertTrue(self.bridge.closed)
        self.assertFalse(self.bridge.enabled)
        self.assertEqual(self.clipboard.disconnected, [17])
        self.assertEqual(self.connection.unsubscribed, [23])
        self.assertEqual(len(self.connection.calls), 1)
        self.bridge.close()
        self.assertEqual(self.connection.unsubscribed, [23])


if __name__ == '__main__':
    unittest.main()
