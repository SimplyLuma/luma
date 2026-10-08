# SPDX-License-Identifier: GPL-3.0-only
"""Multi-window lifetime checks on the owned private compositor."""
import time
import os
from urllib.parse import urlsplit
from gi.repository import GLib, Gtk
from private_input import PrivateInput


class WindowQualification:
    def __init__(self, manager, report, complete):
        self.manager, self.report, self.complete = manager, report, complete
        self.device = PrivateInput()
        self.device.start()
        self.report['checks'] = []
        self.pid = manager.engine.process.pid
        self.primary = manager.primary['window']
        command = manager.command
        def record_command(window, label):
            self.report.setdefault('commands', []).append(label)
            self.report.setdefault('command_owners', []).append({
                'id': (window.native_context.get('description') or {}).get('window_id'),
                'closed': window.native_context['closed'],
                'active': window is manager.application.get_active_window()})
            future = command(window, label)
            future.add_done_callback(lambda result: self.report.setdefault('command_results', []).append(
                str(result.exception()) if result.exception() else result.result()))
            return future
        manager.command = record_command
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', lambda _c, key, code, state:
            self.report.setdefault('keys', []).append([key, int(state)]) or False)
        self.primary.add_controller(keys)
        manager.application.lookup_action('browser-new-window').connect('activate',
            lambda *_: self.report.setdefault('new_window_commands', []).append(time.monotonic()))
        if os.environ.get("VIOLA_QA_POPUP_ONLY") == "1":
            self.second = self.primary
            self.wait(lambda: self.primary.page_input.valid_identity(), self.scripted_popup)
        else:
            self.wait(lambda: self.primary.page_input.valid_identity(), self.open_second)

    def wait(self, predicate, then):
        self.report['waiting_for'] = then.__name__
        deadline = time.monotonic() + 8
        def poll():
            try:
                if predicate():
                    then()
                    return False
                if time.monotonic() > deadline:
                    raise TimeoutError('Native multi-window condition timed out')
                return True
            except Exception as error:
                self.finish(str(error))
                return False
        GLib.timeout_add(100, poll)

    def check(self, name, value):
        self.report['checks'].append({name: bool(value)})
        if not value:
            raise AssertionError(name)

    def open_second(self):
        self.device.chord(0xffe3, ord('n'))
        GLib.timeout_add(1500, lambda: self.manager.submit(
            lambda: self.manager.engine.call('Target.getTargets'),
            lambda result: self.report.update(targets=result)) and False)
        self.wait(lambda: len(self.manager.contexts) == 2 and all(
            c['window'].get_mapped() and c['window'].page_input.valid_identity()
            for c in self.manager.contexts.values()), self.second_ready)

    def second_ready(self):
        self.second = next(c['window'] for c in self.manager.contexts.values()
                           if c['window'] is not self.primary)
        self.check('ctrl_n_creates_two_native_windows', len(self.manager.contexts) == 2)
        self.check('one_application_identity', all(
            c['window'].get_application() is self.manager.application
            for c in self.manager.contexts.values()))
        self.report['application_id'] = self.manager.application.get_application_id()
        self.check('distinct_service_targets', self.primary.services.sessions != self.second.services.sessions)
        self.space = self.primary.services.state['activeSpaceId']
        self.primary.send('space:update', {'spaceId': self.space, 'patch': {'name': 'Window sync fixture'}})
        self.wait(lambda: any(space['id'] == self.space and space['name'] == 'Window sync fixture'
                  for space in self.second.services.state['spaces']), self.workspace_synced)

    def workspace_synced(self):
        self.check('workspace_edit_repaints_other_window', True)
        self.favorite = self.primary.services.state['activeTabId']
        self.primary.send('tab:reorder', {'tabId': self.favorite, 'spaceId': self.space,
                                        'section': 'favorite', 'index': 0})
        self.wait(lambda: all(any(row['id'] == self.favorite for row in window.services.state['favorites'])
                  for window in (self.primary, self.second)), self.favorite_synced)

    def favorite_synced(self):
        self.check('favorites_shared_between_windows', True)
        self.favorite_target = self.primary.page_input.target
        parts = urlsplit(self.primary.services.state['activeUrl'])
        self.restore_url = parts.scheme + '://' + parts.netloc + '/layout?title=Restore-key-fixture' 
        self.wait(lambda: self.second.sidebar.favorites.get(self.favorite) is not None and
                  self.second.sidebar.favorites[self.favorite].elsewhere.get_visible(), self.transfer_tab)

    def transfer_tab(self):
        self.check('active_remote_tab_is_marked', True)
        self.second.send('tab:activate', {'tabId': self.favorite})
        self.wait(lambda: self.second.services.state['activeTabId'] == self.favorite and
                  self.second.page_input.valid_identity(), self.tab_transferred)

    def tab_transferred(self):
        self.check('tab_transfer_preserves_live_page', self.second.page_input.target == self.favorite_target
                   and len(self.manager.contexts) == 2)
        self.wait(lambda: self.primary.services.state['activeTabId'] != self.favorite and
                  self.primary.page_input.valid_identity(), self.source_survived)

    def source_survived(self):
        if os.environ.get('VIOLA_QA_FAVORITE_TEXT') == '1':
            from pathlib import Path
            from capture_native_widget import capture
            selected = self.second.sidebar.favorites[self.favorite]
            assert selected.has_css_class('active'), 'Current favorite lacks selected state'
            capture(self.second, Path('/tmp/viola-selected-favorite.png'))
            self.report['selected_favorite_snapshot'] = '/tmp/viola-selected-favorite.png'
        self.check('taking_only_tab_preserves_source_window', len(self.manager.contexts) == 2)
        self.second.present()
        self.second.page.grab_focus()
        self.close_count = 0
        GLib.timeout_add(300, lambda: self.close_tab_key() or False)

    def close_tab_key(self):
        self.closing_tab = self.second.services.state['activeTabId']
        self.device.chord(0xffe3, ord('w'))
        self.wait(lambda: self.second.services.state['activeTabId'] != self.closing_tab and
                  self.second.page_input.valid_identity(), self.tab_key_closed)

    def tab_key_closed(self):
        self.close_count += 1
        self.check('ctrl_w_keeps_both_windows_' + str(self.close_count),
                   len(self.manager.contexts) == 2 and self.manager.engine.process.poll() is None)
        if os.environ.get('VIOLA_QA_REPEAT_CLOSE') == '1' and self.close_count < 11:
            focus = (self.second.page, self.second.address, self.second.toggle)[self.close_count % 3]
            focus.grab_focus()
            GLib.timeout_add(300, lambda: self.close_tab_key() or False)
            return
        if self.close_count < (12 if os.environ.get('VIOLA_QA_REPEAT_CLOSE') == '1' else 2):
            self.second.submit(lambda: self.second.services.navigate(self.restore_url))
            self.wait(lambda: self.second.services.state['activeUrl'] == self.restore_url,
                      lambda: GLib.timeout_add(250, lambda: self.close_tab_key() or False))
            return
        GLib.timeout_add(250, self.restore_key)

    def restore_key(self):
        self.device.chord((0xffe3, 0xffe1), ord('t'))
        self.wait(lambda: self.second.services.state['activeUrl'] == self.restore_url and
                  self.second.page_input.valid_identity(), self.restored)
        return False

    def restored(self):
        self.check('ctrl_shift_t_restores_last_closed_tab', len(self.manager.contexts) == 2)
        self.menu_window = self.second
        self.open_page_menu()

    def open_page_menu(self):
        window = self.menu_window
        window.present()
        def right_click():
            valid, bounds = window.page.compute_bounds(window)
            if not valid:
                self.finish('Page menu has no native bounds')
                return False
            self.device.move((1600-window.get_width())/2+bounds.get_x()+35,
                             (1000-window.get_height())/2+bounds.get_y()+35)
            GLib.timeout_add(80, lambda: self.device.click(273) or False)
            self.wait(lambda: window.open_popover is not None and window.open_popover.get_mapped(),
                      self.page_menu_opened)
            return False
        GLib.timeout_add(250, right_click)

    def page_menu_opened(self):
        other = self.primary if self.menu_window is self.second else self.second
        self.check('right_click_routes_to_' + ('second' if self.menu_window is self.second else 'first'),
                   other.open_popover is None)
        self.device.key(0xff1b)
        self.wait(lambda: self.menu_window.open_popover is None, self.page_menu_closed)

    def page_menu_closed(self):
        if self.menu_window is self.second:
            self.menu_window = self.primary
            self.open_page_menu()
        else:
            self.primary.close()
            self.wait(lambda: len(self.manager.contexts) == 1 and self.second.get_mapped(), self.first_closed)

    def first_closed(self):
        self.check('first_close_preserves_engine', self.manager.engine.process.pid == self.pid
                   and self.manager.engine.process.poll() is None)
        self.check('survivor_has_live_page', self.second.page_input.valid_identity())
        self.second.present()
        self.second.page.grab_focus()
        GLib.timeout_add(300, lambda: self.open_again() or False)

    def open_again(self):
        self.device.chord(0xffe3, ord('n'))
        self.wait(lambda: len(self.manager.contexts) == 2 and all(
            c['window'].get_mapped() and c['window'].page_input.valid_identity()
            for c in self.manager.contexts.values()), self.third_ready)

    def third_ready(self):
        self.check('survivor_can_open_another_window', True)
        third = next(c['window'] for c in self.manager.contexts.values()
                     if c['window'] is not self.second)
        third.close()
        self.wait(lambda: len(self.manager.contexts) == 1, self.application_menu_after_close)

    def application_menu_after_close(self):
        self.second.present()
        def activate():
            menu = self.second.identity_menu
            row = next(row for row in menu.description['items'] if row['label'] == 'New window')
            self.manager.application.activate_action(menu.menu.paths[(row['index'],)], None)
            self.wait(lambda: len(self.manager.contexts) == 2 and all(
                c['window'].get_mapped() for c in self.manager.contexts.values()), self.menu_survived)
            return False
        GLib.timeout_add(300, activate)

    def menu_survived(self):
        self.check('application_menu_survives_its_previous_window', True)
        fourth = next(c['window'] for c in self.manager.contexts.values() if c['window'] is not self.second)
        fourth.close()
        self.wait(lambda: len(self.manager.contexts) == 1, self.scripted_popup if os.environ.get("VIOLA_QA_POPUP") == "1" else lambda: self.finish())

    def scripted_popup(self):
        self.opener_tab = self.second.services.state['activeTabId']
        def create():
            return self.manager.engine.call('Runtime.evaluate', {
                'expression': "window._qaPopup=window.open('/layout?title=Popup-fixture','_blank','width=440,height=320'); !!window._qaPopup",
                'userGesture': True, 'returnByValue': True}, session=self.second.page_input.session)
        self.second.submit(create, lambda _: self.wait(lambda: len(self.manager.contexts) == 2 and all(
            c['window'].get_mapped() and c['window'].page_input.valid_identity()
            for c in self.manager.contexts.values()), self.popup_ready))

    def popup_ready(self):
        popup = next(c['window'] for c in self.manager.contexts.values() if c['window'] is not self.second)
        self.check('scripted_popup_preserves_opener_tab', self.second.services.state['activeTabId'] == self.opener_tab)
        def inspect():
            return self.manager.engine.call('Runtime.evaluate', {
                'expression': '!!window.opener && !window.opener.closed',
                'returnByValue': True}, session=popup.page_input.session)
        def inspected(result):
            self.check('scripted_popup_has_own_visible_page_and_opener',
                'Popup-fixture' in popup.services.state.get('activeUrl', '') and
                result.get('result', {}).get('value') is True)
            popup.close()
            self.wait(lambda: len(self.manager.contexts) == 1, lambda: self.finish())
        popup.submit(inspect, inspected)

    def finish(self, error=None):
        self.report['completed'] = True
        if error:
            self.report['error'] = error
            self.report['windows'] = [dict(id=key, mapped=c['window'].get_mapped(),
                valid_input=c['window'].page_input.valid_identity(),
                active=c['window'].services.state.get('activeTabId'),
                tab_id=c['window'].page_input.tab_id,
                target_id=c['window'].page_input.target)
                for key, c in self.manager.contexts.items()]
        self.device.close()
        self.complete()
