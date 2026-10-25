# SPDX-License-Identifier: GPL-3.0-only
import unittest
from unittest.mock import Mock, patch
import gi
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk
from native_visibility import NativeVisibility


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.surface = Mock()
        self.surface.get_state.return_value = 0
        self.window = Mock()
        self.window.get_surface.return_value = self.surface
        self.window.get_mapped.return_value = True
        self.receiver = Mock()
        self.receiver.set_visible.return_value = True
        self.disconnect = Mock()
        def observe(callback):
            self.session = callback
            callback(False)
            return self.disconnect
        self.observer = patch('native_visibility.observe_session_activity', observe)
        self.observer.start()
        self.addCleanup(self.observer.stop)

    def visibility(self):
        instance = NativeVisibility(self.window, 7, self.receiver)
        self.addCleanup(instance.close)
        return instance

    def test_lock_pauses_capture_unlock_resumes_without_redundant_commands(self):
        instance = self.visibility()
        self.session(True); self.session(True); instance.changed()
        self.session(False)
        self.assertEqual(self.receiver.set_visible.call_args_list,
                         [((7, True),), ((7, False),), ((7, True),)])

    def test_unlock_does_not_resume_minimized_or_unmapped_window(self):
        instance = self.visibility()
        self.session(True)
        self.surface.get_state.return_value = Gdk.ToplevelState.MINIMIZED
        self.session(False)
        self.assertEqual(instance.sent, False)
        self.surface.get_state.return_value = 0
        self.window.get_mapped.return_value = False
        instance.changed()
        self.assertEqual(instance.sent, False)
        self.window.get_mapped.return_value = True
        instance.changed()
        self.assertEqual(instance.sent, True)

    def test_initial_page_materializes_before_visibility_control_and_close_cancels_retry(self):
        self.window.get_mapped.return_value = False
        instance = self.visibility()
        self.session(True)
        self.receiver.set_visible.assert_not_called()
        self.window.get_mapped.return_value = True
        self.receiver.set_visible.return_value = False
        with patch('native_visibility.GLib.timeout_add', return_value=91):
            instance.changed()
        self.assertEqual(instance.retry, 91)
        with patch('native_visibility.GLib.source_remove') as remove:
            instance.close()
            remove.assert_called_once_with(91)
        self.assertTrue(instance.closed)
        self.assertEqual(instance.signals, [])


if __name__ == '__main__':
    unittest.main()
