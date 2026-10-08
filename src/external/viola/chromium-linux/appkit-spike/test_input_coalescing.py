# SPDX-License-Identifier: GPL-3.0-only
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from input_coalescing import PendingInput


class CoalescingTest(unittest.TestCase):
    def test_motion_keeps_latest_until_dispatch(self):
        event = PendingInput(('s', 'target', 'tab'), 'Input.dispatchMouseEvent',
                             {'type': 'mouseMoved', 'x': 0, 'y': 0, 'buttons': 0})
        for x in range(10000):
            self.assertTrue(event.merge(('s', 'target', 'tab'), 'Input.dispatchMouseEvent',
                                        {'type': 'mouseMoved', 'x': x, 'y': 0, 'buttons': 0}))
        self.assertEqual(event.take()['x'], 9999)
        self.assertFalse(event.merge(('s', 'target', 'tab'), 'Input.dispatchMouseEvent',
                                    {'type': 'mouseMoved', 'x': 10000, 'y': 0, 'buttons': 0}))

    def test_boundaries_and_wheel_distance(self):
        wheel = {'type': 'mouseWheel', 'x': 2, 'y': 3, 'deltaX': 1, 'deltaY': 2}
        event = PendingInput(('s', 'target', 'tab'), 'Input.dispatchMouseEvent', wheel)
        for _ in range(999):
            self.assertTrue(event.merge(event.identity, event.method, wheel))
        self.assertFalse(event.merge(('other', 'target', 'tab'), event.method, wheel))
        self.assertFalse(event.merge(event.identity, event.method, {**wheel, 'x': 4}))
        self.assertFalse(event.merge(event.identity, event.method, {'type': 'mousePressed'}))
        self.assertFalse(event.merge(event.identity, 'Input.dispatchKeyEvent', {'type': 'keyDown'}))
        self.assertEqual(event.take()['deltaY'], 2000)

    def test_drag_motion_carries_button_identity_and_release_restores_hover(self):
        from page_input import PageInput
        adapter = PageInput.__new__(PageInput)
        adapter.point = lambda x, y: dict(x=x, y=y)
        sent = []
        adapter.dispatch = lambda method, params: sent.append(params)
        controller = SimpleNamespace(get_current_event_state=lambda: 0)
        for mask, name in ((1, 'left'), (2, 'right'), (4, 'middle'), (0, 'none')):
            adapter.buttons = mask
            adapter._motion(controller, 20, 30)
            self.assertEqual(sent[-1]['button'], name)
            self.assertEqual(sent[-1]['buttons'], mask)
        event = PendingInput(('s', 'target', 'tab'), 'Input.dispatchMouseEvent',
                             dict(type='mouseMoved', button='left', buttons=1))
        self.assertFalse(event.merge(event.identity, event.method,
                                    dict(type='mouseMoved', button='none', buttons=1)))

    def test_gutters_do_not_compress_pointer_coordinates(self):
        from page_input import PageInput
        adapter = PageInput.__new__(PageInput)
        adapter.page = SimpleNamespace(get_width=lambda: 900, get_height=lambda: 700)
        adapter.viewport = dict(clientWidth=885, clientHeight=685, input_zoom=1)
        adapter.desired_size = (900, 700, 1)
        self.assertEqual(adapter.point(893, 693), dict(x=893, y=693))
        adapter.desired_size = (900, 700, 1.25)
        self.assertEqual(adapter.point(800, 600), dict(x=1000, y=750))
        adapter.viewport['input_zoom'] = 2
        self.assertEqual(adapter.point(800, 600), dict(x=500, y=375))

    def test_slow_acks_do_not_drop_final_drag_position(self):
        from page_input import PageInput, GLib
        adapter = PageInput.__new__(PageInput)
        adapter.session, adapter.target, adapter.tab_id = 's', 'target', 'tab'
        adapter.services = SimpleNamespace(state={'activeTabId': 'tab'})
        adapter.valid_identity = lambda: True
        adapter.pending, adapter.last_pending_input = 8, None
        adapter.deferred_motion, adapter.motion_retry = None, None
        adapter.metrics = dict(sent=0, identity_rejections=0, coalesced=0, pointer_backpressure=0)
        adapter.on_error = self.fail
        queued, sent = [], []
        def submit(work):
            future = Future(); queued.append((work, future)); return future
        adapter.submit = submit
        adapter.engine = SimpleNamespace(call=lambda method, params, session: sent.append(params))
        for x in (10, 20, 80):
            adapter.dispatch('Input.dispatchMouseEvent', dict(type='mouseMoved', x=x, y=20, button='left', buttons=1))
        adapter.dispatch('Input.dispatchMouseEvent', dict(type='mouseReleased', x=80, y=20, button='left', buttons=0))
        for work, future in queued:
            future.set_result(work())
        self.assertEqual([(e['type'], e['x']) for e in sent], [('mouseMoved', 80), ('mouseReleased', 80)])
        self.assertIsNone(adapter.deferred_motion)
        GLib.source_remove(adapter.motion_retry)

    def test_unconsumed_enter_sends_character_without_ctrl_submission(self):
        from page_input import PageInput, Gdk
        adapter = PageInput.__new__(PageInput)
        sent = []
        adapter.dispatch = lambda method, params: sent.append(params)
        adapter.commit_text = lambda *_: self.fail('Enter used text insertion')
        adapter._unfiltered_key(None, Gdk.KEY_Return, 0, 0)
        self.assertEqual(sent[-1]['type'], 'char')
        self.assertEqual(sent[-1]['text'], '\r')
        adapter._unfiltered_key(None, Gdk.KEY_KP_Enter, 0, Gdk.ModifierType.SHIFT_MASK)
        self.assertEqual(sent[-1]['code'], 'NumpadEnter')
        self.assertEqual(sent[-1]['modifiers'], 8)
        adapter._unfiltered_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(len(sent), 2)

    def test_wayland_scroll_matches_chromium_native_units(self):
        try:
            from page_input import PageInput, Gdk
        except ModuleNotFoundError as error:
            if error.name == 'gi':
                self.skipTest('GTK binding is tested on the Linux host')
            raise
        adapter = PageInput.__new__(PageInput)
        adapter.metrics, adapter.pointer = {}, (10, 20)
        adapter.point = lambda *_: {'x': 10, 'y': 20}
        sent = []
        adapter.dispatch = lambda method, params: sent.append(params) or True
        for unit, delta in ((Gdk.ScrollUnit.WHEEL, 1), (Gdk.ScrollUnit.SURFACE, 10)):
            controller = SimpleNamespace(get_unit=lambda: unit, get_current_event_state=lambda: 0)
            adapter._scroll(controller, -delta, delta)
        self.assertEqual([(e['deltaX'], e['deltaY']) for e in sent], [(-120, 120), (-120, 120)])

    def test_input_writes_do_not_wait_for_service_or_prior_ack(self):
        try:
            from page_input import PageInput, GLib
        except ModuleNotFoundError as error:
            if error.name == 'gi':
                self.skipTest('GTK binding is tested on Linux')
            raise
        adapter = PageInput.__new__(PageInput)
        adapter.session, adapter.target, adapter.tab_id = 's', 'target', 'tab'
        adapter.services = SimpleNamespace(state={'activeTabId': 'tab'})
        adapter.valid_identity = lambda: True
        adapter.pending, adapter.last_pending_input = 0, None
        adapter.metrics = dict(sent=0, identity_rejections=0, coalesced=0, pointer_backpressure=0)
        errors, sent, responses = [], [], []
        adapter.on_error = errors.append
        adapter.submit = lambda *_: self.fail('Input entered the blocking service executor')
        def write(work):
            future = Future()
            future.set_result(work())
            return future
        adapter.input_submit = write
        def request(method, params, session):
            sent.append(params['type'])
            future = Future()
            responses.append(future)
            return future
        adapter.engine = SimpleNamespace(request=request)
        for kind in ('mouseWheel', 'mousePressed', 'mouseReleased'):
            adapter.dispatch('Input.dispatchMouseEvent', {'type': kind})
        self.assertEqual(sent, ['mouseWheel', 'mousePressed', 'mouseReleased'])
        self.assertEqual(adapter.pending, 3)
        for response in responses:
            response.set_result({})
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        self.assertEqual(adapter.pending, 0)
        self.assertFalse(errors)
        from unittest.mock import patch
        deadlines = []
        with patch('page_input.GLib.timeout_add_seconds', side_effect=lambda _seconds, callback: deadlines.append(callback) or 0):
            adapter.dispatch('Input.dispatchMouseEvent', {'type': 'mouseMoved'})
        self.assertEqual(adapter.pending, 1)
        deadlines[0]()
        self.assertEqual(adapter.pending, 0)
        responses[-1].set_result({})
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        self.assertEqual(adapter.pending, 0)
        self.assertEqual(adapter.metrics['ack_timeouts'], 1)

    def test_closed_tab_ack_before_sidebar_update_is_not_fatal(self):
        from page_input import PageInput, GLib
        for message in ('Session with given id not found.', 'No target with given id found',
                        'Target closed', 'Unexpected protocol failure'):
            with self.subTest(message=message):
                adapter = PageInput.__new__(PageInput)
                adapter.session, adapter.target, adapter.tab_id = 's', 'target', 'tab'
                adapter.services = SimpleNamespace(state={'activeTabId': 'tab'})
                adapter.valid_identity = lambda: True
                adapter.pending, adapter.last_pending_input = 0, None
                adapter.metrics = dict(sent=0, identity_rejections=0, coalesced=0, pointer_backpressure=0)
                errors, response = [], Future()
                adapter.on_error = errors.append
                adapter.engine = SimpleNamespace(request=lambda *_: response)
                def write(work):
                    future = Future()
                    future.set_result(work())
                    return future
                adapter.input_submit = adapter.submit = write
                adapter.dispatch('Input.dispatchKeyEvent', {'type': 'rawKeyDown'})
                # The backend has destroyed this session, but renderState has
                # not arrived: every cached identity still matches the tab.
                response.set_exception(RuntimeError(message))
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                self.assertEqual(adapter.pending, 0)
                if message == 'Unexpected protocol failure':
                    self.assertEqual(errors, [message])
                else:
                    self.assertFalse(errors)
                    self.assertEqual(adapter.metrics['identity_rejections'], 1)

    def test_adapter_burst_preserves_click_order(self):
        try:
            from page_input import PageInput
        except ModuleNotFoundError as error:
            if error.name == 'gi':
                self.skipTest('GTK binding is tested on the Linux host')
            raise
        adapter = PageInput.__new__(PageInput)
        adapter.session, adapter.target, adapter.tab_id = 's', 'target', 'tab'
        adapter.services = SimpleNamespace(state={'activeTabId': 'tab'})
        adapter.valid_identity = lambda: True
        adapter.pending, adapter.last_pending_input = 0, None
        adapter.metrics = dict(sent=0, identity_rejections=0, coalesced=0, pointer_backpressure=0)
        queued, sent, errors = [], [], []
        def submit(work):
            future = Future()
            queued.append((work, future))
            return future
        adapter.submit = submit
        adapter.on_error = errors.append
        adapter.engine = SimpleNamespace(call=lambda method, params, session: sent.append(params))
        for x in range(10000):
            adapter.dispatch('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': 0})
        adapter.dispatch('Input.dispatchMouseEvent', {'type': 'mousePressed', 'button': 'left'})
        for x in range(10000, 20000):
            adapter.dispatch('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': 0, 'buttons': 1})
        adapter.dispatch('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'button': 'left'})
        self.assertEqual(adapter.pending, 4)
        for work, future in queued:
            future.set_result(work())
        self.assertEqual([event['type'] for event in sent], ['mouseMoved', 'mousePressed', 'mouseMoved', 'mouseReleased'])
        self.assertEqual([sent[0]['x'], sent[2]['x']], [9999, 19999])
        self.assertFalse(errors)
        self.assertEqual(adapter.pending, 0)


if __name__ == '__main__':
    unittest.main()
