# SPDX-License-Identifier: GPL-3.0-only
"""Idle browser state costs nothing: dedupe, coalescing and idempotent stop."""
import json
import shutil
import subprocess
import unittest
from types import SimpleNamespace

from browser_services import BrowserServices
from native_media import NativeMedia
from state_renders import StateRenders


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Scheduler:
    def __init__(self):
        self.idle, self.timeouts = [], []

    def idle_add(self, callback):
        self.idle.append(callback)

    def timeout_add(self, delay, callback):
        self.timeouts.append((delay, callback))

    def run(self):
        callbacks = self.idle + [callback for _delay, callback in self.timeouts]
        self.idle, self.timeouts = [], []
        for callback in callbacks:
            callback()


def renders():
    clock, scheduler, rendered = Clock(), Scheduler(), []
    queue = StateRenders(rendered.append, scheduler.idle_add, scheduler.timeout_add, clock=clock)
    return queue, scheduler, clock, rendered


class StateRendersTest(unittest.TestCase):
    def test_identical_state_is_dropped_before_render(self):
        queue, scheduler, clock, rendered = renders()
        queue.push({'activeUrl': 'https://a.test/'})
        scheduler.run()
        for _ in range(50):
            clock.now += 1
            queue.push({'activeUrl': 'https://a.test/'})
            scheduler.run()
        self.assertEqual(rendered, [{'activeUrl': 'https://a.test/'}])
        self.assertEqual(queue.metrics['unchanged'], 50)
        clock.now += 1
        queue.push({'activeUrl': 'https://b.test/'})
        scheduler.run()
        self.assertEqual(rendered[-1], {'activeUrl': 'https://b.test/'})

    def test_burst_schedules_one_callback_and_renders_latest(self):
        queue, scheduler, _clock, rendered = renders()
        for index in range(20):
            queue.push({'index': index})
        self.assertEqual(len(scheduler.idle) + len(scheduler.timeouts), 1)
        scheduler.run()
        self.assertEqual(rendered, [{'index': 19}])
        self.assertEqual(queue.metrics['coalesced'], 19)

    def test_renders_are_spaced_one_frame_apart(self):
        queue, scheduler, clock, rendered = renders()
        queue.push({'index': 0})
        scheduler.run()
        clock.now += 0.004
        queue.push({'index': 1})
        self.assertEqual(scheduler.idle, [])
        self.assertEqual([delay for delay, _ in scheduler.timeouts], [13])
        queue.push({'index': 2})
        self.assertEqual(len(scheduler.timeouts), 1)
        clock.now += 0.013
        scheduler.run()
        self.assertEqual(rendered, [{'index': 0}, {'index': 2}])
        clock.now += 1
        queue.push({'index': 3})
        self.assertEqual(len(scheduler.idle), 1)


def binding(name, payload, session='sidebar-session'):
    return {'method': 'Runtime.bindingCalled', 'sessionId': session,
            'params': {'name': name, 'payload': payload}}


class ServicesStateTest(unittest.TestCase):
    def services(self):
        delivered = []
        engine = SimpleNamespace(on_event=lambda _event: None)
        services = BrowserServices(engine, delivered.append, native_media=True)
        services.sessions['sidebar'] = 'sidebar-session'
        return services, delivered

    def test_identical_payload_is_not_decoded_again(self):
        services, delivered = self.services()
        payload = json.dumps({'activeUrl': 'https://a.test/'})
        for _ in range(10):
            services._event(binding('violaNativeStateProbe', payload))
        self.assertEqual(delivered, [{'activeUrl': 'https://a.test/'}])
        self.assertEqual(services.state_metrics, {'published': 1, 'unchanged': 9})
        services._event(binding('violaNativeStateProbe', json.dumps({'activeUrl': 'https://b.test/'})))
        self.assertEqual(services.state, {'activeUrl': 'https://b.test/'})
        self.assertEqual(len(delivered), 2)

    @unittest.skipUnless(shutil.which('node'), 'node is unavailable')
    def test_page_publishes_and_stops_only_on_change(self):
        expressions = []
        def call(method, params=None, session=None):
            if method == 'Target.getTargets':
                return {'targetInfos': []}
            if method == 'Runtime.evaluate':
                expressions.append(params['expression'])
                return {'result': {'value': True}}
            return {}
        services = BrowserServices(SimpleNamespace(on_event=None, call=call), lambda _state: None,
                                   native_media=True)
        services.sessions = {'sidebar': 's', 'overlay': 'o'}
        services.wait_state = lambda *_args: None
        services.connect(timeout=1)
        script = '''
          const sent = [], media = [], handlers = {};
          globalThis.location = {href: 'chrome://viola/'};
          globalThis.window = globalThis;
          globalThis.violaNativeStateProbe = payload => sent.push(payload);
          globalThis.violaNativeEventProbeJSON = () => {};
          globalThis.violaNativeMenuProbeJSON = () => {};
          globalThis.violaNativeMediaProbeJSON = payload => media.push(JSON.parse(payload).kind);
          globalThis.vela = {on: (name, handler) => { handlers[name] = handler; return () => {}; }};
          let engineStops = 0;
          window.__velaPreviewStop = () => { engineStops++; };
          globalThis.S = {activeUrl: 'https://a.test/'};
        ''' + ';\n'.join(expressions) + ''';
          for (let i = 0; i < 50; i++) handlers.render({activeUrl: 'https://a.test/'});
          handlers.render({activeUrl: 'https://b.test/'});
          for (let i = 0; i < 50; i++) window.__velaPreviewStop();
          window.__velaPreviewAnswer('{}', 1);
          window.__velaPreviewStop(); window.__velaPreviewStop();
          console.log(JSON.stringify({sent: sent.length, media, engineStops}));
        '''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'sent': 2, 'media': ['offer', 'stop'], 'engineStops': 52})


class NativeMediaStopTest(unittest.TestCase):
    def media(self):
        previews = []
        media = NativeMedia.__new__(NativeMedia)
        media.preview = lambda paintable, tab_id: previews.append((paintable, tab_id))
        media.paintable = media.pipeline = media.receiver = media.sink = None
        media.generation = media.tab_id = None
        return media, previews

    def test_stop_without_preview_is_a_no_op(self):
        media, previews = self.media()
        for _ in range(50):
            media.stop()
        self.assertEqual(previews, [])

    def test_stop_after_offer_clears_preview_once(self):
        media, previews = self.media()
        media.generation, media.tab_id = 4, 'tab-1'
        media.stop()
        media.stop()
        self.assertEqual(previews, [(None, 'tab-1')])
        self.assertIsNone(media.generation)


if __name__ == '__main__':
    unittest.main()
