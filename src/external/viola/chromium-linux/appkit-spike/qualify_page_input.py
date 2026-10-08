# SPDX-License-Identifier: GPL-3.0-only
"""Fixture checks through the GTK adapter, not physical-device acceptance."""
import time
import json
from gi.repository import GLib


class InputQualification:
    def __init__(self, window, adapter, engine, services, submit, complete, page_menu=False):
        self.window, self.adapter = window, adapter
        self.engine, self.services, self.submit = engine, services, submit
        self.complete = complete
        self.started = time.monotonic()
        self.running = False
        self.page_menu = page_menu
        self.document_guard = False
        self.webui_guard = False
        self.resize_cases = [(640, 480, 1.0), (640, 480, 1.25), (320, 240, 1.0)]
        self.resize_index = 0
        self.report = {'classification': 'adapter fixture, not physical GTK event acceptance'}
        self.report['resize_cases'] = []
        GLib.timeout_add(100, self.ready)

    def ready(self):
        if time.monotonic() - self.started > 20:
            self.finish('Input target did not become ready')
            return False
        if not self.adapter.valid_identity() or not self.services.state.get('activeUrl', '').endswith('/native-window'):
            return True
        if self.running:
            return False
        self.running = True
        # Allocation is deterministic for this unpresented fixture only.
        self.window.page.allocate(640, 480, -1, None)
        self.adapter.resize(*self.resize_cases[0])
        GLib.timeout_add(100, self.resized)
        return False

    def resized(self):
        if time.monotonic() - self.started > 20:
            self.report['observed_viewport'] = self.adapter.viewport
            self.report['observed_frame'] = self.window.page.frame
            self.finish('Native viewport did not resize')
            return False
        viewport = self.adapter.viewport
        frame = self.window.page.frame
        width, height, scale = self.resize_cases[self.resize_index]
        expected_frame = [round(width * scale), round(height * scale)]
        if (not viewport or viewport['clientWidth'] != width or viewport['clientHeight'] != height
                or [frame['width'], frame['height']] != expected_frame):
            return True
        self.report['css_viewport_resize'] = True
        self.report['resize_cases'].append({'css': [width, height], 'scale': scale,
                                           'frame': expected_frame})
        self.resize_index += 1
        if self.resize_index < len(self.resize_cases):
            width, height, scale = self.resize_cases[self.resize_index]
            self.window.page.allocate(width, height, -1, None)
            self.adapter.resize(width, height, scale)
            return True
        self.evaluate("(() => { const e = document.querySelector('input'); if (!e) return null; const r = e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()", self.click)
        return False

    def evaluate(self, expression, done):
        session = self.adapter.session
        def work():
            result = self.engine.call('Runtime.evaluate', {
                'expression': expression, 'returnByValue': True}, session=session)
            if 'exceptionDetails' in result:
                raise RuntimeError(str(result['exceptionDetails']))
            return result['result'].get('value')
        self.submit(work, done)

    def click(self, point):
        if not point:
            self.finish('Fixture input missing')
            return
        viewport = self.adapter.viewport
        x = point['x'] * self.window.page.get_width() / viewport['clientWidth']
        y = point['y'] * self.window.page.get_height() / viewport['clientHeight']
        self.adapter.pointer_button(True, 1, 1, x, y)
        self.adapter.pointer_button(False, 1, 1, x, y)
        self.evaluate("document.activeElement === document.querySelector('input')", self.focused)

    def focused(self, focused):
        self.report['pointer_focus'] = focused
        if not focused:
            self.finish('Adapter pointer did not focus input')
            return
        self.adapter.ime.emit('commit', 'Viola café 日本語 🦋')
        self.evaluate("document.querySelector('input').value", self.committed)

    def committed(self, value):
        self.report['gtk_ime_commit'] = value == 'Viola café 日本語 🦋'
        if not self.report['gtk_ime_commit']:
            self.finish('GTK commit text mismatch')
            return
        if self.page_menu:
            self.menu_before = self.services.menu
            x, y = self.adapter.pointer
            self.adapter.pointer_button(True, 3, 1, x, y)
            self.adapter.pointer_button(False, 3, 1, x, y)
            GLib.timeout_add(100, self.menu_ready)
        else:
            self.switch_tab()

    def menu_ready(self):
        if time.monotonic() - self.started > 20:
            self.finish('Original page context menu was not exported')
            return False
        description = self.services.menu
        if description is self.menu_before or not description or description.get('surface') != 'page':
            return True
        if description.get('target_id') != self.adapter.target:
            self.finish('Page context menu target differs from the displayed frame')
            return False
        def find(items, path=()):
            for item in items:
                current = path + (item['index'],)
                if item['label'].replace('&', '').lower() == 'select all' and item['enabled']:
                    return current
                child = find(item.get('children', []), current)
                if child:
                    return child
        path = find(description['items'])
        if not path:
            self.finish('Original editable-page Select all action is missing')
            return False
        from native_menu import NativeMenu
        self.page_menu_description, self.page_menu_path = description, path
        self.page_popup = NativeMenu(description, lambda nonce, selection: self.submit(
            lambda: self.services.activate_menu(nonce, selection), self.selection_requested))
        if self.webui_guard:
            self.qualify_webui_lifetime()
        elif self.document_guard:
            self.qualify_document_lifetime()
        else:
            self.page_popup.actions.lookup_action(self.page_popup.paths[path]).activate(None)
        return False

    def selection_requested(self, _):
        self.evaluate("(() => { const e=document.querySelector('input'); return e.selectionStart===0 && e.selectionEnd===e.value.length; })()",
                      self.selected_all)

    def selected_all(self, selected):
        if not selected and time.monotonic() - self.started < 20:
            GLib.timeout_add(100, lambda: self.selection_requested(None) or False)
            return
        self.report['page_context_original_select_all'] = bool(selected)
        if not selected:
            self.finish('Native GTK action did not invoke original page selection')
            return
        self.evaluate("(() => { const e=document.querySelector('input'); e.setSelectionRange(e.value.length,e.value.length); return true; })()",
                      self.replay_page_action)

    def replay_page_action(self, _):
        self.submit(lambda: self.services.activate_menu(self.page_menu_description['nonce'],
                                                       list(self.page_menu_path)),
                    lambda _: GLib.timeout_add(200, self.check_replay))

    def check_replay(self):
        self.evaluate("(() => {const e=document.querySelector('input'); return e.selectionStart===e.selectionEnd;})()",
                      self.replayed)
        return False

    def replayed(self, ignored):
        self.report['page_context_replayed_action_ignored'] = bool(ignored)
        if not ignored:
            self.finish('A consumed page-menu token executed again')
            return
        self.document_guard = True
        self.menu_before = self.services.menu
        x, y = self.adapter.pointer
        self.adapter.pointer_button(True, 3, 1, x, y)
        self.adapter.pointer_button(False, 3, 1, x, y)
        GLib.timeout_add(100, self.menu_ready)

    def qualify_document_lifetime(self):
        session = self.adapter.session
        url = self.services.state['activeUrl'] + '/document-guard'
        description, path = self.page_menu_description, self.page_menu_path
        def navigate_and_try_old_action():
            self.engine.call('Page.navigate', {'url': url}, session=session)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    ready = self.engine.call('Runtime.evaluate', {'expression':
                        'location.href === ' + json.dumps(url) + ' && document.readyState === "complete" && !!document.querySelector("input")',
                        'returnByValue': True}, session=session)['result'].get('value')
                    if ready:
                        break
                except RuntimeError:
                    pass  # The old execution context can disappear mid-navigation.
                time.sleep(.05)
            else:
                raise RuntimeError('Replacement document did not become ready')
            self.engine.call('Runtime.evaluate', {'expression':
                "(() => { const e=document.querySelector('input'); e.value='Viola café 日本語 🦋';e.focus();e.setSelectionRange(3,3); })()"}, session=session)
            baseline = self.engine.call('Runtime.evaluate', {'expression':
                "(() => {const e=document.querySelector('input');return [e.selectionStart,e.selectionEnd,e.value,location.href];})()",
                'returnByValue': True}, session=session)['result'].get('value')
            self.report['document_selection_before_action'] = baseline
            self.services.activate_menu(description['nonce'], list(path))
            time.sleep(.15)
            self.report['document_selection_after_action'] = self.engine.call('Runtime.evaluate', {'expression':
                "(() => {const e=document.querySelector('input');return [e.selectionStart,e.selectionEnd,e.value,location.href];})()",
                'returnByValue': True}, session=session)['result'].get('value')
            return self.engine.call('Runtime.evaluate', {'expression':
                "(() => {const e=document.querySelector('input');return e.selectionStart===3&&e.selectionEnd===3;})()",
                'returnByValue': True}, session=session)['result'].get('value')
        self.submit(navigate_and_try_old_action, self.document_rejected)

    def document_rejected(self, rejected):
        self.report['page_context_document_change_rejected'] = bool(rejected)
        if rejected:
            self.webui_guard = True
            self.menu_before = self.services.menu
            x, y = self.adapter.pointer
            self.adapter.pointer_button(True, 3, 1, x, y)
            self.adapter.pointer_button(False, 3, 1, x, y)
            GLib.timeout_add(100, self.menu_ready)
        else:
            self.finish('An old page-menu action affected a replacement document')

    def qualify_webui_lifetime(self):
        description, path = self.page_menu_description, self.page_menu_path
        page_session = self.adapter.session
        shell_session = self.services.sessions['sidebar']
        tab_id = self.adapter.tab_id
        def reload_and_try_old_action():
            self.services.evaluate('sidebar', 'window.__nativeReloadGuard = true')
            self.engine.call('Page.reload', {}, session=shell_session)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    ready = self.services.evaluate('sidebar',
                        'document.readyState === "complete" && !window.__nativeReloadGuard && typeof vela !== "undefined"')
                    if ready:
                        break
                except RuntimeError:
                    pass
                time.sleep(.05)
            else:
                raise RuntimeError('Trusted WebUI did not finish reloading')
            with self.services.updated:
                self.services.state = None
            self.services.connect()
            self.services.wait_state(lambda state: state.get('activeTabId') == tab_id)
            self.engine.call('Runtime.evaluate', {'expression':
                "document.querySelector('input').setSelectionRange(3,3)"}, session=page_session)
            self.services.activate_menu(description['nonce'], list(path))
            time.sleep(.15)
            return self.engine.call('Runtime.evaluate', {'expression':
                "(() => {const e=document.querySelector('input');return e.selectionStart===3&&e.selectionEnd===3;})()",
                'returnByValue': True}, session=page_session)['result'].get('value')
        self.submit(reload_and_try_old_action, self.webui_rejected)

    def webui_rejected(self, rejected):
        self.report['page_context_webui_reload_rejected'] = bool(rejected)
        if rejected:
            self.switch_tab()
        else:
            self.finish('A page-menu action survived its trusted WebUI lifetime')

    def switch_tab(self):
        self.original_tab = self.adapter.tab_id
        self.original_session = self.adapter.session
        url = self.services.state['activeUrl'].replace('/native-window', '/other-input-tab')
        def switch():
            self.services.navigate(url, new_tab=True)
            self.services.wait_state(lambda state: state.get('activeTabId') != self.original_tab)
        self.submit(switch, self.switched)

    def switched(self, _):
        # Simulate the delayed input from the prior displayed frame. Its
        # identity is real; only timing is controlled by this fixture.
        current = self.adapter.tab_id
        self.adapter.tab_id = self.original_tab
        accepted = self.adapter.dispatch('Input.insertText', {'text': 'WRONG TAB'})
        self.adapter.tab_id = current
        self.report['stale_frame_rejected'] = not accepted
        def read_original():
            return self.engine.call('Runtime.evaluate', {
                'expression': "document.querySelector('input').value", 'returnByValue': True},
                session=self.original_session)['result'].get('value')
        self.submit(read_original, self.original_unchanged)

    def original_unchanged(self, value):
        self.report['previous_tab_unchanged'] = value == 'Viola café 日本語 🦋'
        self.finish()

    def finish(self, error=None):
        if error:
            self.report['error'] = error
        self.complete(self.report)
