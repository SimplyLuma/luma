# SPDX-License-Identifier: GPL-3.0-only
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch
from page_requests import PageRequests
from gi.repository import GLib


class PageRequestsTest(unittest.TestCase):
    def drain(self):
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

    def test_stalled_old_geometry_does_not_block_new_page(self):
        responses, calls, results, errors = [], [], [], []
        def request(method, params, session):
            calls.append((method, session))
            future = Future()
            responses.append(future)
            return future
        client = PageRequests(SimpleNamespace(request=request), lambda work: work(), errors.append)
        client.call('Emulation.setDeviceMetricsOverride', {}, 'old', results.append)
        self.assertEqual(calls, [('Emulation.setDeviceMetricsOverride', 'old')])
        client.clear()
        client.call('Page.getLayoutMetrics', {}, 'new', results.append)
        responses[1].set_result({'new': True})
        self.drain()
        responses[0].set_exception(RuntimeError('Session with given id not found'))
        self.drain()
        self.assertEqual(results, [{'new': True}])
        self.assertFalse(errors)
        self.assertFalse(client.pending)

    def test_timeout_releases_request_and_ignores_late_result(self):
        response, timers, results, errors = Future(), [], [], []
        client = PageRequests(SimpleNamespace(request=lambda *_: response), lambda work: work(), errors.append)
        with patch('page_requests.GLib.timeout_add', side_effect=lambda _, callback: timers.append(callback) or 1):
            client.call('Emulation.setDeviceMetricsOverride', {}, 's', results.append)
        timers[0]()
        response.set_result({})
        self.drain()
        self.assertEqual(results, [None])
        self.assertFalse(errors)
        self.assertFalse(client.pending)

    def test_unrelated_protocol_error_is_reported(self):
        response, errors = Future(), []
        client = PageRequests(SimpleNamespace(request=lambda *_: response), lambda work: work(), errors.append)
        client.call('Page.getLayoutMetrics', {}, 's', lambda _: self.fail('Unexpected success'))
        response.set_exception(RuntimeError('Invalid parameters'))
        self.drain()
        self.assertEqual(errors, ['Invalid parameters'])

    def test_page_switch_sizes_new_target_while_old_resize_never_answers(self):
        from page_input import PageInput
        requests, errors = [], []
        def request(method, params, session):
            response = Future()
            requests.append((method, session, response))
            return response
        adapter = PageInput.__new__(PageInput)
        adapter.target = adapter.session = adapter.viewport = None
        adapter.desired_size = (900, 700, 1.25)
        adapter.resize_inflight, adapter.resize_timer = False, None
        adapter.metrics = {}
        adapter.page_requests = PageRequests(SimpleNamespace(request=request), lambda work: work(), errors.append)
        adapter.frame_changed({'tab_id': 'old-tab', 'target_id': 'old'})
        requests[-1][2].set_result({'sessionId': 'old-session'})
        self.drain()
        self.assertEqual(requests[-1][:2], ('Emulation.setDeviceMetricsOverride', 'old-session'))
        stalled = requests[-1][2]
        adapter.frame_changed({'tab_id': 'new-tab', 'target_id': 'new'})
        requests[-1][2].set_result({'sessionId': 'new-session'})
        self.drain()
        for method in ('Emulation.setDeviceMetricsOverride', 'Emulation.setVisibleSize'):
            self.assertEqual(requests[-1][:2], (method, 'new-session'))
            requests[-1][2].set_result({})
            self.drain()
        viewport = {'clientWidth': 900, 'clientHeight': 700}
        requests[-1][2].set_result({'cssLayoutViewport': viewport, 'cssVisualViewport': {'zoom': 1, 'scale': 1}})
        self.drain()
        stalled.set_exception(TimeoutError('Old renderer never answered'))
        self.drain()
        self.assertEqual(adapter.target, 'new')
        self.assertEqual(adapter.session, 'new-session')
        self.assertEqual(adapter.viewport, dict(viewport, input_zoom=1))
        self.assertFalse(adapter.resize_inflight)
        self.assertFalse(errors)
