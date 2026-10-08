# SPDX-License-Identifier: GPL-3.0-only
"""Deliver real compositor events to GTK on the owned private monitor."""
import time
import os
from pathlib import Path
from gi.repository import Gdk, GLib, Gtk
from private_input import PrivateInput
from native_zoom_row import NativeZoomRow


class ControlQualification:
    def __init__(self, window, complete):
        self.window, self.complete = window, complete
        self.device = None
        self.started = time.monotonic()
        self.report = {'classification': 'private compositor-delivered GTK events', 'checks': []}
        self.positions = [(0.5, 0.5), (0.15, 0.15), (0.85, 0.15), (0.15, 0.85), (0.85, 0.85)]
        self.index = 0
        self.expect_native_close = False
        window.on_native_close = self.closed_by_control
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', lambda _c, key, code, state: self.report.setdefault('keys', []).append(key) or False)
        window.add_controller(keys)
        GLib.timeout_add(100, self.ready)

    def capture_stage(self, stage, then):
        from luma_appkit.menus import _descendants
        self.report.setdefault('frame_states', {})[stage] = {
            'active': self.window.is_active(),
            'toplevel': str(self.window.get_surface().get_state()),
            'title_labels': [{'text': widget.get_text(),
                              'color': widget.get_style_context().get_color().to_string(),
                              'states': str(widget.get_state_flags())}
                             for widget in _descendants(self.window.title_bar)
                             if isinstance(widget, Gtk.Label) and widget.get_text() and widget.get_mapped()]}
        if os.environ.get('VIOLA_QA_CAPTURE_STAGE') == stage:
            Path(os.environ['VIOLA_QA_CAPTURE_MARKER']).write_text(stage + '\n')
            def resume_once():
                then()
                return False
            GLib.timeout_add(3500, resume_once)
        else:
            then()

    def ready(self):
        if time.monotonic() - self.started > 15:
            self.finish('Native controls did not become ready')
            return False
        if not self.window.get_mapped() or not self.window.page_input.valid_identity():
            return True
        try:
            self.device = PrivateInput()
            self.device.start()
            self.initial_size = [self.window.get_width(), self.window.get_height()]
            if os.environ.get('VIOLA_QA_SETTINGS_SELECTS') == '1':
                from qualify_settings_selects import SettingsSelectQualification
                GLib.timeout_add(500, lambda: self.capture_stage('browser',
                    lambda: SettingsSelectQualification(self)) or False)
            elif os.environ.get('VIOLA_QA_MOUSE_NAV') == '1':
                from qualify_mouse_navigation import MouseNavigationQualification
                GLib.timeout_add(500, lambda: self.capture_stage('browser',
                    lambda: MouseNavigationQualification(self)) or False)
            elif os.environ.get('VIOLA_QA_SEEK') == '1':
                from qualify_seek_controls import SeekQualification
                GLib.timeout_add(500, lambda: self.capture_stage('browser', lambda: SeekQualification(self)) or False)
            elif os.environ.get('VIOLA_QA_HOVER') == '1':
                from qualify_hover_controls import HoverQualification
                GLib.timeout_add(500, lambda: self.capture_stage('browser',
                    lambda: HoverQualification(self)) or False)
            elif os.environ.get('VIOLA_QA_SCROLL_REGRESSION') == '1':
                from qualify_scroll_regression import ScrollRegression
                GLib.timeout_add(500, lambda: self.capture_stage('browser',
                    lambda: ScrollRegression(self)) or False)
            elif os.environ.get('VIOLA_QA_SCROLL_ONLY') == '1':
                from qualify_scrolling import ScrollQualification
                GLib.timeout_add(500, lambda: self.capture_stage('browser',
                    lambda: ScrollQualification(self)) or False)
            elif os.environ.get('VIOLA_QA_DRAG') == '1':
                from qualify_native_drag import DragQualification
                GLib.timeout_add(500, lambda: self.capture_stage('browser',
                    lambda: DragQualification(self)) or False)
            elif os.environ.get('VIOLA_QA_PAGE_CONTROLS_ONLY') == '1':
                from qualify_page_controls import PageControlQualification
                self.report['scope'] = 'page controls only'
                GLib.timeout_add(500, lambda: self.capture_stage('browser',
                    lambda: PageControlQualification(self, self.finish)) or False)
            else:
                GLib.timeout_add(500, lambda: self.start_window_controls() or False)
        except Exception as error:
            self.finish(str(error))
        return False

    def native_control(self, name):
        from luma_appkit.menus import _descendants
        return next((widget for widget in _descendants(self.window.title_bar)
                     if isinstance(widget, Gtk.Button) and widget.get_mapped()
                     and widget.has_css_class(name)), None)

    def start_window_controls(self):
        self.initial_size = [self.window.get_width(), self.window.get_height()]
        button = self.native_control('maximize')
        if button is None:
            self.finish('Toolkit maximize control is missing')
            return
        self.point(button, (.5, .5))
        GLib.timeout_add(600, self.maximized)

    def page_fills(self, stage):
        page = self.window.page
        expected = (page.get_width(), page.get_height())
        size = page.displayed_size
        passed = bool(size and all(abs(a-b) <= 1 for a,b in zip(size, expected)))
        self.report['checks'].append({'page_fills_' + stage: passed,
                                      'page_size': expected, 'painted_size': size,
                                      'capture': page.frame_size_diagnostic})
        if not passed:
            self.finish('Page leaves an uncovered strip after ' + stage)
        return passed

    def maximized(self):
        passed = bool(self.window.get_surface().get_state() & Gdk.ToplevelState.MAXIMIZED)
        self.report['checks'].append({'native_maximize': passed,
                                     'size': [self.window.get_width(), self.window.get_height()]})
        if not passed:
            self.finish('Native maximize button did not maximize the window')
            return False
        if not self.page_fills('maximize'):
            return False
        self.point(self.native_control('maximize'), (.5, .5))
        GLib.timeout_add(600, self.unmaximized)
        return False

    def unmaximized(self):
        size = [self.window.get_width(), self.window.get_height()]
        passed = not (self.window.get_surface().get_state() & Gdk.ToplevelState.MAXIMIZED) and size == self.initial_size
        self.report['checks'].append({'native_restore': bool(passed), 'size': size})
        if not passed:
            self.finish('Native restore did not restore the original window geometry')
            return False
        if not self.page_fills('restore'):
            return False
        self.point(self.native_control('minimize'), (.5, .5))
        GLib.timeout_add(600, self.minimized)
        return False

    def minimized(self):
        # xdg_toplevel has no minimized state and this compositor does not
        # send suspended either. Verify the actual private monitor instead.
        baseline = Path(os.environ['VIOLA_QA_EMPTY_MONITOR'])
        capture = baseline.with_name('minimized-monitor.png')
        self.minimized_state = str(self.window.get_surface().get_state())
        def inspect_monitor():
            import subprocess, sys, gi
            gi.require_version('GdkPixbuf', '2.0')
            from gi.repository import GdkPixbuf
            subprocess.run([sys.executable, str(Path(__file__).with_name('capture_private_display.py')),
                            '--output', str(capture)], check=True, timeout=20,
                           stdout=subprocess.DEVNULL)
            before = GdkPixbuf.Pixbuf.new_from_file(str(baseline))
            after = GdkPixbuf.Pixbuf.new_from_file(str(capture))
            if (before.get_width(), before.get_height(), before.get_rowstride(), before.get_n_channels()) != (
                    after.get_width(), after.get_height(), after.get_rowstride(), after.get_n_channels()):
                raise RuntimeError('Private monitor format changed during minimize')
            original, current = before.get_pixels(), after.get_pixels()
            changed = sum(abs(a-b) > 2 for a, b in zip(original, current))
            return changed, len(original)
        self.window.submit(inspect_monitor, self.minimized_monitor)
        return False

    def minimized_monitor(self, result):
        changed, total = result
        passed = changed / total < .001 and not self.window.is_active()
        self.report['checks'].append({'native_minimize': passed,
            'state': self.minimized_state, 'monitor_changed_components': changed,
            'monitor_total_components': total})
        if not passed:
            self.finish('Minimize did not restore the empty private monitor')
            return
        # A user-driven switch supplies compositor activation authority;
        # programmatic present alone cannot unminimize on this private bus.
        self.device.chord(0xffe9, 0xff09)
        GLib.timeout_add(600, self.presented_again)

    def presented_again(self):
        state = self.window.get_surface().get_state()
        passed = self.window.is_active() and not (state & (Gdk.ToplevelState.MINIMIZED | Gdk.ToplevelState.SUSPENDED))
        self.report['checks'].append({'native_present_after_minimize': bool(passed), 'state': str(state)})
        if passed:
            self.next_click()
        else:
            self.finish('Owned window could not be restored after minimize')
        return False

    def point(self, widget, fraction, click=True):
        native = widget.get_native()
        valid, bounds = widget.compute_bounds(native)
        surface = native.get_surface()
        # Popup positions include the top-level CSD shadow surface inset;
        # the fixture's centered window position describes widget geometry.
        offset_x, offset_y = native.get_surface_transform()
        window_x, window_y = self.window.get_surface_transform()
        offset_x -= window_x
        offset_y -= window_y
        while isinstance(surface, Gdk.Popup):
            offset_x += surface.get_position_x()
            offset_y += surface.get_position_y()
            surface = surface.get_parent()
        if not valid:
            raise RuntimeError('Native widget has no window bounds')
        # The single private monitor centers the fresh test window. This is
        # fixture placement, never a production input coordinate assumption.
        x = (1600 - self.window.get_width()) / 2 + offset_x + bounds.get_x() + bounds.get_width() * fraction[0]
        y = (1000 - self.window.get_height()) / 2 + offset_y + bounds.get_y() + bounds.get_height() * fraction[1]
        self.report.setdefault('points', []).append([x, y, bounds.get_width(), bounds.get_height()])
        self.device.move(x, y)
        if click:
            GLib.timeout_add(60, lambda: self.device.click() or False)

    def next_click(self):
        if self.index == len(self.positions):
            from luma_appkit.menus import _descendants
            identity = next((widget for widget in _descendants(self.window.title_bar)
                if isinstance(widget, Gtk.MenuButton) and widget.get_mapped()
                and widget.has_css_class('luma-identity-button')), None)
            if identity is None:
                self.finish('Native toolkit identity button is missing')
                return
            self.identity = identity
            self.point(identity, (.5, .5))
            GLib.timeout_add(300, self.identity_opened)
            return
        self.window.new_tab_pending = False
        self.point(self.window.new_tab, self.positions[self.index])
        GLib.timeout_add(150, self.check_click)

    def check_click(self):
        pointer = self.window.get_display().get_default_seat().get_pointer()
        self.report['pointer_position'] = str(self.window.get_surface().get_device_position(pointer))
        passed = self.window.new_tab_pending and self.window.address_focused()
        self.report['checks'].append({'new_tab_hit_fraction': self.positions[self.index], 'passed': passed, 'pending': self.window.new_tab_pending, 'focus': str(self.window.get_focus())})
        if not passed:
            self.finish('New-tab button missed compositor click')
        else:
            self.index += 1
            self.next_click()
        return False

    def identity_opened(self):
        from luma_appkit.menus import _descendants
        popup = self.identity.get_popover()
        labels = [widget.get_text() for widget in _descendants(popup)
                  if isinstance(widget, Gtk.Label) and widget.get_mapped()]
        passed = popup.get_mapped() and 'About Viola' in labels and 'Quit Viola' in labels
        self.report['checks'].append({'native_identity_original_menu': passed, 'labels': labels})
        if not passed:
            self.finish('Native identity still shows a bootstrap or unrelated menu')
        else:
            self.device.key(0xff1b)
            GLib.timeout_add(200, self.open_browser_menu)
        return False

    def open_browser_menu(self):
        self.point(self.window.menu_button, (.5, .5))
        GLib.timeout_add(500, self.menu_opened)
        return False

    def menu_opened(self):
        menu = self.window.open_popover
        passed = bool(menu and menu.get_mapped())
        self.report['checks'].append({'browser_menu_mapped': passed})
        if menu:
            self.report['menu_classes'] = menu.get_css_classes()
            self.report['native_command_rows'] = [
                {'action': widget.get_action_name(), 'enabled': widget.is_sensitive(),
                 'mapped': widget.get_mapped(), 'width': widget.get_width()}
                for _, widget in menu._placed if isinstance(widget, Gtk.Button)]
        if not passed:
            self.finish('Browser menu did not open from compositor click')
        else:
            self.zoom_menu = menu
            self.zoom_steps = [(2, 110), (2, 125), (0, 110), (1, 100)]
            self.zoom_step = 0
            self.capture_stage('browser', self.zoom_click)
        return False

    def zoom_click(self):
        menu = self.window.open_popover
        rows = [widget for _, widget in menu._placed if isinstance(widget, NativeZoomRow)]
        if len(rows) != 1:
            self.finish('Browser menu has no inline native zoom row')
            return
        if self.zoom_step == 0:
            sizes = [[button.get_width(), button.get_height()] for button in rows[0].buttons]
            passed = rows[0].get_height() == 34 and sizes == [[28, 26], [44, 26], [28, 26], [28, 26]]
            self.report['checks'].append({'native_zoom_geometry': passed,
                                         'height': rows[0].get_height(), 'buttons': sizes})
            if not passed:
                self.finish('Inline zoom dimensions differ from the reference contract')
                return
        self.zoom_nonce = menu.nonce
        self.point(rows[0].buttons[self.zoom_steps[self.zoom_step][0]], (.5, .5))
        self.zoom_deadline = time.monotonic() + 5
        GLib.timeout_add(100, self.zoom_changed)

    def zoom_changed(self):
        menu = self.window.open_popover
        if menu is self.zoom_menu and menu.nonce == self.zoom_nonce and time.monotonic() < self.zoom_deadline:
            return True
        value = next((row['value'] for row, _ in menu.zoom_rows.values()), None) if menu else None
        expected = self.zoom_steps[self.zoom_step][1]
        passed = menu is self.zoom_menu and menu.get_mapped() and value == expected
        self.report['checks'].append({'native_zoom_kept_open': passed, 'value': value, 'expected': expected, 'repeat_calls': getattr(self.window, 'zoom_repeat_calls', 0)})
        if not passed:
            self.finish('Native zoom failed to update in the same mapped menu')
        else:
            self.zoom_step += 1
            if self.zoom_step < len(self.zoom_steps):
                GLib.timeout_add(100, lambda: self.zoom_click() or False)
            else:
                self.device.key(0xff1b)
                GLib.timeout_add(200, self.menu_closed)
        return False

    def menu_closed(self):
        passed = self.window.open_popover is None
        self.report['checks'].append({'escape_dismissed_menu': passed, 'focus': str(self.window.get_focus()), 'mapped': bool(self.window.open_popover and self.window.open_popover.get_mapped())})
        if not passed:
            self.finish('Escape did not dismiss native menu')
        else:
            self.evaluate("document.title='A deliberately very long tab title that must never define the width of its context menu'",
                          lambda _: GLib.timeout_add(300, self.context_title_ready))
        return False

    def context_title_ready(self):
        row = self.window.rows[self.window.state['activeTabId']]
        self.point(row.activate, (.5, .5))
        GLib.timeout_add(200, self.keyboard_context)
        return False

    def keyboard_context(self):
        self.device.chord(0xffe1, 0xffc7)
        self.context_deadline = time.monotonic() + 5
        GLib.timeout_add(100, self.context_opened)
        return False

    def context_opened(self):
        menu = self.window.open_popover
        if not menu and time.monotonic() < self.context_deadline:
            return True
        action = next((command.id for command, label, _theme in menu.custom_rows.values()
                       if label == 'Mute tab'), None) if menu else None
        button = next((widget for _, widget in menu._placed if isinstance(widget, Gtk.Button)
                       and widget.get_action_name() == 'menu.' + action), None) if action else None
        passed = bool(menu and menu.get_mapped() and button and button.is_sensitive())
        self.report['checks'].append({'shift_f10_original_tab_menu': passed})
        if passed:
            # Inspect root section headings, not tooltip labels GTK may attach
            # beneath a popup after hovering the source tab's long title.
            model = menu.get_menu_model()
            title_omitted = menu.hide_root_title and all(
                model.get_item_attribute_value(index, 'label', None) is None
                for index in range(model.get_n_items()))
            compact = title_omitted and 276 <= menu.get_width() <= 450
            self.report['checks'].append({'tab_context_omits_title_uses_toolkit_width': compact,
                                         'width_with_shadow': menu.get_width(), 'title_omitted': title_omitted})
            passed = passed and compact
        if not passed:
            self.finish('Shift+F10 did not expose the original tab context menu')
        else:
            def activate_context():
                self.context_deadline = time.monotonic() + 5
                self.point(button, (.5, .5))
                GLib.timeout_add(100, self.context_muted)
            self.capture_stage('context', activate_context)
        return False

    def context_muted(self):
        tab = next((tab for tab in self.window.state.get('today', [])
                    if tab['id'] == self.window.state.get('activeTabId')), {})
        passed = bool(tab.get('muted') and self.window.open_popover is None)
        if not passed and time.monotonic() < self.context_deadline:
            return True
        self.report['checks'].append({'compositor_context_original_mute': passed})
        if not passed:
            self.finish('Native context-menu click did not invoke original mute action')
        else:
            self.point(self.window.toggle, (.5, .5))
            GLib.timeout_add(200, self.sidebar_hidden)
        return False

    def sidebar_hidden(self):
        passed = not self.window.sidebar.get_visible() and self.window.page.get_mapped()
        self.report['checks'].append({'sidebar_hides_page_stays_mapped': passed})
        if not passed:
            self.finish('Sidebar collapse lost the page')
        else:
            self.point(self.window.toggle, (.5, .5))
            GLib.timeout_add(200, self.sidebar_restored)
        return False

    def sidebar_restored(self):
        passed = self.window.sidebar.get_visible() and self.window.page.get_mapped()
        self.report['checks'].append({'sidebar_restored_page_stays_mapped': passed})
        if passed:
            from qualify_workspace_controls import WorkspaceQualification
            self.workspace_qualification = WorkspaceQualification(self)
        else:
            self.finish('Sidebar did not restore')
        return False

    def evaluate(self, expression, done):
        adapter = self.window.page_input
        self.window.submit(lambda: adapter.engine.call('Runtime.evaluate',
            {'expression': expression, 'returnByValue': True}, session=adapter.session)['result'].get('value'), done)

    def page_input(self):
        self.window.new_tab_pending = False
        self.evaluate("(() => {const r=document.querySelector('input').getBoundingClientRect(); return [(r.x+r.width/2)/innerWidth,(r.y+r.height/2)/innerHeight]})()", self.click_page_input)
        return False

    def click_page_input(self, fraction):
        self.point(self.window.page, fraction)
        self.typed_index = 0
        GLib.timeout_add(250, self.type_character)

    def type_character(self):
        if self.typed_index == len('viola'):
            self.evaluate("document.querySelector('input').value", self.page_typed)
            return False
        self.device.key(ord('viola'[self.typed_index]))
        self.typed_index += 1
        return True

    def page_typed(self, value):
        passed = value == 'viola'
        self.report['checks'].append({'compositor_gtk_page_text': passed, 'value': value})
        if passed:
            self.address_deadline = time.monotonic() + 5
            self.device.chord(0xffe3, ord('l'))
            GLib.timeout_add(300, self.address_shortcut)
        else:
            self.finish('Compositor keyboard text did not reach the page')

    def address_shortcut(self):
        popup = self.window.address_suggestions.popup
        if (not popup or not popup.get_mapped()) and time.monotonic() < self.address_deadline:
            return True
        suggestions = bool(popup and popup.get_mapped())
        self.report['checks'].append({'native_address_suggestions': suggestions})
        if not suggestions:
            self.finish('Existing palette suggestions did not appear in a native menu')
            return False
        # Compare painted border boxes, not content widths or shadow surfaces.
        width = popup.get_first_child().compute_bounds(popup)[1].get_width()
        address_width = self.window.address_field.compute_bounds(self.window)[1].get_width()
        self.report['checks'].append({'address_popup_width': width,
                                      'address_field_width': address_width,
                                      'address_width_matches': abs(width - address_width) <= 2})
        if abs(width - address_width) > 2:
            self.finish('Address suggestions do not match the address field width')
            return False
        passed = self.window.address_focused() and not self.window.new_tab_pending
        self.report['checks'].append({'native_ctrl_l': passed})
        if not passed:
            self.finish('Ctrl+L did not focus the native address')
        else:
            self.capture_stage('address', self.enter_address_results)
        return False

    def enter_address_results(self):
        self.device.key(0xff54)
        GLib.timeout_add(200, self.address_results_focused)

    def address_results_focused(self):
        passed = self.window.address_suggestions.owns_focus()
        self.report['checks'].append({'native_address_down_focus': passed})
        if not passed:
            self.finish('Down did not focus the native address suggestions')
            return False
        self.suggestion_url = self.window.state['activeUrl']
        self.device.key(0xff0d)
        GLib.timeout_add(350, self.address_result_committed)
        return False

    def address_result_committed(self):
        passed = (self.window.address_suggestions.popup is None
                  and self.window.get_focus() is self.window.page
                  and self.window.state['activeUrl'] == self.suggestion_url)
        self.report['checks'].append({'native_address_result_enter': passed})
        if not passed:
            self.finish('Enter did not commit the current-page suggestion and restore focus')
            return False
        self.device.chord(0xffe3, ord('l'))
        GLib.timeout_add(350, self.cancel_address)
        return False

    def cancel_address(self):
        self.device.key(0xff1b)
        GLib.timeout_add(200, self.address_cancelled)
        return False

    def address_cancelled(self):
        passed = self.window.get_focus() is self.window.page and not self.window.new_tab_pending
        self.report['checks'].append({'address_escape_restores_page': passed})
        if not passed:
            self.finish('Address Escape did not restore native page focus')
        else:
            self.device.chord(0xffe3, ord('t'))
            GLib.timeout_add(250, self.new_tab_shortcut)
        return False

    def new_tab_shortcut(self):
        passed = self.window.address_focused() and self.window.new_tab_pending
        self.report['checks'].append({'native_ctrl_t': passed})
        if not passed:
            self.finish('Ctrl+T did not start native new-tab editing')
        else:
            self.device.key(0xff1b)
            GLib.timeout_add(250, self.start_address_navigation)
        return False

    def start_address_navigation(self):
        self.address_original = self.window.state['activeUrl']
        self.device.chord(0xffe3, ord('l'))
        GLib.timeout_add(250, self.address_end)
        return False

    def address_end(self):
        self.device.key(0xff57)
        self.address_character = 0
        GLib.timeout_add(150, self.type_address_suffix)
        return False

    def type_address_suffix(self):
        if self.address_character < len('-typed'):
            self.device.key(ord('-typed'[self.address_character]))
            self.address_character += 1
            return True
        self.report['address_before_enter'] = self.window.address.get_text()
        self.device.key(0xff0d)
        self.navigation_deadline = time.monotonic() + 5
        GLib.timeout_add(100, self.address_navigated)
        return False

    def address_navigated(self):
        expected = self.address_original + '-typed'
        if self.window.state.get('activeUrl') != expected and time.monotonic() < self.navigation_deadline:
            return True
        passed = (self.window.state.get('activeUrl') == expected
                  and self.window.address_suggestions.popup is None)
        self.report['checks'].append({'native_address_enter_navigation': passed,
                                     'url': self.window.state.get('activeUrl')})
        if passed:
            self.favicon_deadline = time.monotonic() + 6
            GLib.timeout_add(150, self.start_late_favicon)
        else:
            self.finish('Native address Enter did not commit exact compositor-typed text')
        return False

    def start_late_favicon(self):
        if not self.window.page_input.valid_identity():
            if time.monotonic() < self.favicon_deadline:
                return True
            self.finish('Selected page did not become ready after address navigation')
            return False
        self.favicon_before = self.window.rows[self.window.state['activeTabId']].icon.get_paintable()
        self.evaluate("(() => { const link=document.createElement('link');link.rel='icon';link.href='/late-icon.svg';document.head.append(link);return true; })()",
                      lambda _: GLib.timeout_add(100, self.late_favicon))
        return False

    def late_favicon(self):
        row = self.window.rows[self.window.state['activeTabId']]
        texture = row.icon.get_paintable()
        passed = (texture is not None and texture is not self.favicon_before
                  and row.icon_key[1].endswith('/late-icon.svg'))
        if not passed and time.monotonic() < self.favicon_deadline:
            return True
        self.report['checks'].append({'late_favicon_refresh': passed})
        if passed:
            from qualify_page_controls import PageControlQualification
            self.page_control_qualification = PageControlQualification(self, self.start_scroll_qualification)
        else:
            self.finish('A late browser favicon did not refresh the native tab')
        return False

    def start_scroll_qualification(self):
        from qualify_scrolling import ScrollQualification
        self.scroll_qualification = ScrollQualification(self)

    def tile_shortcut(self):
        self.device.chord(0xffeb, 0xff51)
        GLib.timeout_add(800, self.tiled)
        return False

    def tiled(self):
        width, height = self.window.get_width(), self.window.get_height()
        passed = 640 <= width <= 800 and height <= 1000 and height > 820
        self.report['checks'].append({'compositor_tile_fits': passed,
            'size': [width, height], 'surface_state': str(self.window.get_surface().get_state())})
        if passed:
            if not self.page_fills('tile'):
                return False
            self.capture_stage('tiled', self.untile)
        else:
            self.finish('Window did not fit the compositor half-screen tile')
        return False

    def untile(self):
        self.device.chord(0xffeb, 0xff51)
        GLib.timeout_add(600, self.close_after_untile)

    def close_after_untile(self):
        if [self.window.get_width(), self.window.get_height()] != self.initial_size:
            self.finish('Private untile did not restore fixture geometry before close')
            return False
        button = self.native_control('close')
        if button is None:
            self.finish('Native toolkit close button is missing')
            return False
        self.expect_native_close = True
        self.point(button, (.5, .5))
        GLib.timeout_add(1500, self.close_timeout)
        return False

    def closed_by_control(self, *_):
        if self.expect_native_close:
            self.expect_native_close = False
            self.report['checks'].append({'native_close_button': True})
            self.finish()
        # The host owns release/drain ordering and destroys the GTK window
        # after this signal returns, once the main loop exits.
        return True

    def close_timeout(self):
        if self.expect_native_close:
            self.expect_native_close = False
            self.finish('Native close button did not request window closure')
        return False

    def finish(self, error=None):
        self.report['completed'] = True
        if error:
            self.report['error'] = error
        if self.device:
            self.device.close()
            self.device = None
        self.complete(self.report)
