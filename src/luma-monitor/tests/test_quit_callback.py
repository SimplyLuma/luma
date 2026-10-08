# SPDX-License-Identifier: Apache-2.0
"""A completed quit request must show one result and retire its idle source."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from luma_monitor.application import MonitorWindow


class ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class QuitCallbackTests(unittest.TestCase):
    def run_request(self, result, closed=False):
        window = SimpleNamespace(closed=closed, host=object(),
                                 live_rows={'app': {'id': 'app'}})
        app = {'id': 'app', 'n': 'Editor'}
        callbacks = []
        request = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
        with patch('luma_monitor.application.threading.Thread', ImmediateThread), \
             patch('luma_monitor.application.GLib.idle_add', side_effect=lambda callback: callbacks.append(callback)), \
             patch('luma_monitor.integration.quit_row', request), \
             patch('luma_monitor.application.Toast.show', return_value=object()) as toast:
            MonitorWindow._request_quit(window, app)
            self.assertEqual(len(callbacks), 1)
            self.assertIs(callbacks[0](), False)
            if closed:
                toast.assert_not_called()
            else:
                toast.assert_called_once_with(window.host, self.message(result))

    @staticmethod
    def message(result):
        if isinstance(result, Exception):
            return str(result)
        return 'Editor was asked to quit' if result else 'Editor doesn’t accept quit requests'

    def test_success_retires_idle_source(self):
        self.run_request(True)

    def test_refusal_retires_idle_source(self):
        self.run_request(False)

    def test_error_retires_idle_source(self):
        self.run_request(RuntimeError('Request failed'))

    def test_closed_window_does_not_show_toast(self):
        self.run_request(True, closed=True)


if __name__ == '__main__':
    unittest.main()
