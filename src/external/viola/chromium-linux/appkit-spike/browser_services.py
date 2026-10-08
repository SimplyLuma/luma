# SPDX-License-Identifier: GPL-3.0-only
"""Probe adapter for existing Viola services; no replacement browser model.

Only the owned process's exact trusted WebUI targets receive bindings or
commands. Their generated host contract remains the authorization boundary.
"""
import json
import hashlib
import threading
import time


class BrowserServices:
    def __init__(self, engine, on_state, native_media=False):
        self.native_media = native_media
        self.on_native_media = lambda _payload: None
        self.engine = engine
        self.on_state = on_state
        self.sessions = {}
        self.state = None
        self.menu = None
        self.updated = threading.Condition()
        self.previous_event = engine.on_event
        self.on_native_event = lambda _name, _payload: None
        self.on_native_menu = lambda _description: None
        engine.on_event = self._event

    def connect(self, timeout=10, targets=None):
        deadline = time.monotonic() + timeout
        wanted = {'chrome://viola/': 'sidebar',
                  'chrome://viola/overlay.html': 'overlay'}
        while time.monotonic() < deadline:
            for target in self.engine.call('Target.getTargets')['targetInfos']:
                surface = wanted.get(target['url'])
                if not surface or surface in self.sessions:
                    continue
                if targets and target['targetId'] != targets.get(surface):
                    continue
                if target['type'] != 'browser_ui':
                    raise RuntimeError('Expected a trusted Chromium WebUI target')
                session = self.engine.call('Target.attachToTarget', {
                    'targetId': target['targetId'], 'flatten': True})['sessionId']
                self.sessions[surface] = session
            if len(self.sessions) == len(wanted):
                break
            time.sleep(.05)
        if len(self.sessions) != len(wanted):
            raise RuntimeError('Existing Viola service surfaces are unavailable')
        session = self.sessions['sidebar']
        self.engine.call('Runtime.enable', session=session)
        self.engine.call('Runtime.addBinding', {'name': 'violaNativeStateProbe'}, session)
        self.engine.call('Runtime.addBinding', {'name': 'violaNativeMenuProbeJSON'}, session)
        self.engine.call('Runtime.addBinding', {'name': 'violaNativeEventProbeJSON'}, session)
        self.evaluate('sidebar', '''(() => {
          if (location.href !== 'chrome://viola/') throw Error('Wrong surface');
          globalThis.__violaNativeProbeUnsubscribe?.();
          const publish = state => violaNativeStateProbe(JSON.stringify({...state,
            nativePrettyUrl: typeof prettyUrl === 'function' ? prettyUrl(state.activeUrl) : state.activeUrl}));
          globalThis.__violaNativeProbeUnsubscribe = vela.on('render', publish);
          globalThis.__violaNativeEventUnsubscribe?.forEach(stop => stop());
          globalThis.__violaNativeEventUnsubscribe = ['media:preview', 'download:update', 'permission:prompt']
            .map(name => vela.on(name, payload => violaNativeEventProbeJSON(JSON.stringify({name, payload}))));
          globalThis.__violaNativeMenuProbe = menu =>
            violaNativeMenuProbeJSON(JSON.stringify(menu));
          if (typeof S !== 'undefined' && S) publish(S);
          return true;
        })()''')
        if self.native_media:
            self.enable_native_media()
        self.wait_state(lambda state: bool(state), timeout)

    def enable_native_media(self):
        self.engine.call('Runtime.addBinding', {'name': 'violaNativeMediaProbeJSON'}, self.sessions['sidebar'])
        self.evaluate('sidebar', """(() => {
            const pending = new Map();
            const answer = window.__velaPreviewAnswer, stop = window.__velaPreviewStop;
            window.__violaNativeMediaRestore = () => {
                for (const resolve of pending.values()) resolve(null); pending.clear();
                window.__velaPreviewAnswer = answer; window.__velaPreviewStop = stop;
                delete window.__violaNativeMediaHealthy;
            };
            window.__violaNativeMediaReply = (generation, value) => {
                const resolve = pending.get(generation); pending.delete(generation);
                if (resolve) resolve(value);
            };
            window.__violaNativeMediaHealthy = generation => {
                const h = window.__violaNativeMediaHealth;
                return !!h && h.generation === generation && h.frames > 0 &&
                    (S?.media?.playing === false || (Date.now() - h.lastFrameAt < 3500 && h.blackMs < 4000));
            };
            window.__velaPreviewAnswer = (offer, generation) => new Promise(resolve => {
                pending.set(generation, resolve);
                violaNativeMediaProbeJSON(JSON.stringify({kind:'offer', offer, generation, tabId:S?.media?.tabId}));
            });
            window.__velaPreviewStop = () => {
                for (const resolve of pending.values()) resolve(null); pending.clear();
                window.__violaNativeMediaHealth = null;
                violaNativeMediaProbeJSON(JSON.stringify({kind:'stop'}));
                stop?.();
            };
            return true;
        })()""")

    def media_answer(self, generation, answer):
        return self.evaluate('sidebar', 'window.__violaNativeMediaReply?.(' + json.dumps(generation)
                             + ',' + json.dumps(answer) + '); true')

    def media_health(self, health):
        return self.evaluate('sidebar', 'window.__violaNativeMediaHealth=' + json.dumps(health) + '; true')

    def _event(self, event):
        if (self.native_media and event.get('method') == 'Runtime.bindingCalled'
                and event.get('sessionId') == self.sessions.get('sidebar')
                and event.get('params', {}).get('name') == 'violaNativeMediaProbeJSON'):
            payload = event['params']['payload']
            if len(payload) <= 300000:
                self.on_native_media(json.loads(payload))
            return
        if (event.get('method') == 'Runtime.bindingCalled'
                and event.get('sessionId') == self.sessions.get('sidebar')
                and event.get('params', {}).get('name') == 'violaNativeEventProbeJSON'):
            raw = event['params']['payload']
            if len(raw) > 8 * 1024 * 1024:
                raise RuntimeError('Unexpectedly large native service event')
            value = json.loads(raw)
            if value['name'] in ('media:preview', 'download:update', 'permission:prompt'):
                self.on_native_event(value['name'], value['payload'])
            return
        if (event.get('method') == 'Runtime.bindingCalled'
                and event.get('sessionId') == self.sessions.get('sidebar')
                and event.get('params', {}).get('name') == 'violaNativeMenuProbeJSON'):
            payload = event['params']['payload']
            if len(payload) > 1024 * 1024:
                raise RuntimeError('Unexpectedly large menu model')
            with self.updated:
                self.menu = json.loads(payload)
                self.updated.notify_all()
            self.on_native_menu(self.menu)
            return
        if (event.get('method') == 'Runtime.bindingCalled'
                and event.get('sessionId') == self.sessions.get('sidebar')
                and event.get('params', {}).get('name') == 'violaNativeStateProbe'):
            payload = event['params']['payload']
            if len(payload) > 8 * 1024 * 1024:
                raise RuntimeError('Unexpectedly large browser state')
            state = json.loads(payload)
            if not isinstance(state, dict):
                raise RuntimeError('Invalid browser state')
            with self.updated:
                self.state = state
                self.updated.notify_all()
            self.on_state(state)
        else:
            self.previous_event(event)

    def evaluate(self, surface, expression):
        result = self.engine.call('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True,
            'awaitPromise': True}, self.sessions[surface])
        if result.get('exceptionDetails'):
            raise RuntimeError(str(result['exceptionDetails']))
        return result['result'].get('value')

    def send(self, surface, channel, payload=None):
        # JSON is transported directly in the anonymous protocol, never a
        # shell command. vela.send checks the existing surface capability list.
        return self.evaluate(surface, 'vela.send(' + json.dumps(channel) + ','
                             + json.dumps(payload) + '); true')

    def create_new_tab(self):
        if not getattr(self.engine, 'native_new_tabs', False):
            raise RuntimeError('Immediate new tabs require the native Linux engine')
        previous = self.state.get('activeTabId') if self.state else None
        self.send('sidebar', 'tab:new', {})
        return self.wait_state(lambda state: state and state.get('activeTabId')
                               and state.get('activeTabId') != previous
                               and state.get('activeUrl') == 'about:blank')

    def navigate(self, text, *, new_tab=False):
        if new_tab and getattr(self.engine, 'native_new_tabs', False):
            self.create_new_tab()
            new_tab = False
        mode = 'new' if new_tab else 'edit'
        if new_tab:
            self.send('sidebar', 'tab:new', {})
        else:
            self.send('sidebar', 'palette:open', {'mode': mode})
        self.send('overlay', 'palette:commit', {
            'item': {'type': 'raw', 'url': text, 'title': text},
            'mode': mode, 'meta': False})

    def wait_state(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        with self.updated:
            while not predicate(self.state):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Expected existing browser state was not published')
                self.updated.wait(remaining)
            return self.state

    def open_menu(self, channel, payload):
        previous = self.menu
        self.send('sidebar', channel, payload)
        deadline = time.monotonic() + 10
        with self.updated:
            while self.menu is previous:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Native menu model was not published')
                self.updated.wait(remaining)
            return self.menu

    def activate_menu(self, nonce, path, *, wait=True):
        expression = 'chrome.send("violaNativeMenuProbeAction", ' + json.dumps([nonce, path]) + '); true'
        if wait:
            return self.evaluate('sidebar', expression)
        # A close command destroys its own evaluator. Its reply is not a
        # prerequisite for other windows to continue handling commands.
        return self.engine.request('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True}, self.sessions['sidebar'])

    def dismiss_menu(self, nonce):
        return self.evaluate('sidebar', 'chrome.send("violaNativeMenuProbeDismiss", '
                             + json.dumps([nonce]) + '); true')

    def favicon(self, page_url, revision=None):
        # Reuse Chromium's favicon database and browser-owned image decoder.
        # fetch() does not support this internal source; an image does. The
        # bounded canvas exports only this 32px icon, never a page frame.
        if not isinstance(page_url, str) or len(page_url) > 32768:
            raise ValueError('Invalid favicon page URL')
        revision_key = hashlib.sha256(json.dumps(revision).encode()).hexdigest()[:16]
        return self.evaluate('sidebar', '''new Promise(resolve => {
          const image = new Image();
          const timer = setTimeout(() => resolve(null), 3000);
          image.onload = () => {
            clearTimeout(timer);
            try {
              const canvas = document.createElement('canvas');
              canvas.width = canvas.height = 32;
              canvas.getContext('2d').drawImage(image, 0, 0, 32, 32);
              const data = canvas.toDataURL('image/png');
              resolve(data.length <= 131072 ? data : null);
            } catch (_) { resolve(null); }
          };
          image.onerror = () => { clearTimeout(timer); resolve(null); };
          image.src = 'chrome://favicon2/?size=32&scaleFactor=1x&allowGoogleServerFallback=0&pageUrl='
            + encodeURIComponent(''' + json.dumps(page_url) + ''')
            + '&nativeProbeRevision=' + ''' + json.dumps(revision_key) + ''';
        })''')

    def close(self, *, local_only=False):
        if not local_only and self.engine.process.poll() is None and 'sidebar' in self.sessions:
            try:
                if self.native_media:
                    self.evaluate('sidebar', 'window.__violaNativeMediaRestore?.(); true')
                    self.engine.call('Runtime.removeBinding', {'name': 'violaNativeMediaProbeJSON'}, self.sessions['sidebar'])
                self.evaluate('sidebar', 'globalThis.__violaNativeProbeUnsubscribe?.(); true')
                self.evaluate('sidebar', 'globalThis.__violaNativeEventUnsubscribe?.forEach(stop => stop()); true')
                self.engine.call('Runtime.removeBinding', {'name': 'violaNativeStateProbe'},
                                 self.sessions['sidebar'])
                self.engine.call('Runtime.removeBinding', {'name': 'violaNativeMenuProbeJSON'},
                                 self.sessions['sidebar'])
                self.engine.call('Runtime.removeBinding', {'name': 'violaNativeEventProbeJSON'},
                                 self.sessions['sidebar'])
            except Exception:
                pass
        self.engine.on_event = self.previous_event
