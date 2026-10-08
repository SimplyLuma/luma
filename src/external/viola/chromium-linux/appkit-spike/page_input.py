# SPDX-License-Identifier: GPL-3.0-only
"""GTK input and IME boundary for the isolated native page integration.

Physical key names reuse Chromium's table. Target and tab identities must
match the displayed frame and the existing active-tab state before dispatch.
Clipboard, drag/drop and page accessibility are separate open gates.
"""
import json
import time
from concurrent.futures import Future
from pathlib import Path
from input_coalescing import PendingInput
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk

PHYSICAL_KEYS = json.loads((Path(__file__).parent / 'chromium-keycodes.json').read_text())['xkb_to_dom_code']
KEY_NAMES = {'Return': ('Enter', 13), 'KP_Enter': ('Enter', 13), 'Escape': ('Escape', 27),
             'BackSpace': ('Backspace', 8), 'Delete': ('Delete', 46), 'Tab': ('Tab', 9),
             'ISO_Left_Tab': ('Tab', 9), 'Left': ('ArrowLeft', 37), 'Up': ('ArrowUp', 38),
             'Right': ('ArrowRight', 39), 'Down': ('ArrowDown', 40), 'Home': ('Home', 36),
             'End': ('End', 35), 'Page_Up': ('PageUp', 33), 'Page_Down': ('PageDown', 34),
             'Shift_L': ('Shift', 16), 'Shift_R': ('Shift', 16),
             'Control_L': ('Control', 17), 'Control_R': ('Control', 17),
             'Alt_L': ('Alt', 18), 'Alt_R': ('Alt', 18),
             'Super_L': ('Meta', 91), 'Super_R': ('Meta', 92)}


def modifiers(state):
    return ((1 if state & Gdk.ModifierType.ALT_MASK else 0)
            | (2 if state & Gdk.ModifierType.CONTROL_MASK else 0)
            | (4 if state & Gdk.ModifierType.SUPER_MASK else 0)
            | (8 if state & Gdk.ModifierType.SHIFT_MASK else 0))


def native_shortcut(keyval, state):
    from native_zoom_shortcuts import zoom_command
    if zoom_command(keyval, state) is not None:
        return True
    name = (Gdk.keyval_name(keyval) or '').lower()
    mask = modifiers(state)
    return ((mask == 2 and name in {'w', 'n', 't', 'l', 's', 'r'}) or
            (mask == 10 and name in {'w', 't', 'n'}) or
            (mask == 1 and name in {'left', 'right'}))


def utf16_length(text):
    return len(text.encode('utf-16-le')) // 2


class PageInput:
    def __init__(self, page, engine, services, submit, on_error, input_submit=None):
        self.page = page
        self.engine = engine
        self.services = services
        self.submit = submit
        self.input_submit = input_submit or submit
        self.on_error = on_error
        from page_requests import PageRequests
        self.page_requests = PageRequests(engine, self.input_submit, on_error)
        self.target = None
        self.tab_id = None
        self.session = None
        self.viewport = None
        self.viewport_revision = 0
        self.viewport_refresh_session = None
        self.pending = 0
        self.last_pending_input = None
        self.deferred_motion = None
        self.motion_retry = None
        self.wheel_queue = []
        self.wheel_timer = None
        self.wheel_inflight = 0
        self.buttons = 0
        self.pointer = (0, 0)
        self.composing = False
        self.desired_size = None
        self.resize_timer = None
        self.resize_inflight = False
        self.metrics = {'sent': 0, 'identity_rejections': 0, 'coalesced': 0, 'pointer_backpressure': 0}
        page.on_frame = self.frame_changed
        page.on_resize = self.resize
        self.motion = Gtk.EventControllerMotion()
        self.motion.connect('motion', self._motion)
        page.add_controller(self.motion)
        self.click = Gtk.GestureClick(button=0)
        self.click.connect('pressed', lambda gesture, count, x, y: self.click_event(gesture,
            True, gesture.get_current_button(), count, x, y, gesture.get_current_event_state()))
        self.click.connect('released', lambda gesture, count, x, y: self.click_event(gesture,
            False, gesture.get_current_button(), count, x, y, gesture.get_current_event_state()))
        page.add_controller(self.click)
        self.scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        self.scroll.connect('scroll', self._scroll)
        page.add_controller(self.scroll)
        self.ime = Gtk.IMMulticontext()
        self.ime.set_client_widget(page)
        self.ime.connect('commit', self.commit_text)
        self.ime.connect('preedit-changed', self._preedit)
        # Typed GTK signals avoid the unavailable boxed GdkEvent mapping in
        # this installed PyGObject build. Capture DOM key events before GTK's
        # native IM filter, then let the IM controller produce commits.
        self.keys = Gtk.EventControllerKey()
        self.keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.keys.connect('key-pressed', lambda controller, key, code, state:
                          self._key_event(True, key, code, state))
        self.keys.connect('key-released', lambda controller, key, code, state:
                          self._key_event(False, key, code, state))
        page.add_controller(self.keys)
        self.text_keys = Gtk.EventControllerKey()
        self.text_keys.set_im_context(self.ime)
        self.text_keys.connect('key-pressed', self._unfiltered_key)
        page.add_controller(self.text_keys)
        focus = Gtk.EventControllerFocus()
        focus.connect('enter', lambda *_: self._focus(True))
        focus.connect('leave', lambda *_: self._focus(False))
        page.add_controller(focus)
        from page_touch import PageTouch
        self.touch = PageTouch(self)

    def frame_changed(self, metadata):
        if metadata['target_id'] != self.target and hasattr(self, 'touch'):
            self.touch.cancel()
        self.tab_id = metadata.get('tab_id')
        target = metadata['target_id']
        if target == self.target:
            return
        self.target = target
        self.session = None
        getattr(self, 'on_session_changed', lambda: None)()
        self.viewport = None
        self.viewport_revision += 1
        self.viewport_refresh_session = None
        self.page_requests.clear()
        self.resize_inflight = False
        def attached(result):
            if result is None:
                self.target = None
                return
            self.session = result['sessionId']
            self.page_requests.call('Page.enable', {}, self.session, lambda _: None)
            self.flush_resize()
        self.page_requests.call('Target.attachToTarget', {
            'targetId': target, 'flatten': True}, None, attached)

    def page_event(self, event):
        if not self.session or event.get('sessionId') != self.session:
            return GLib.SOURCE_REMOVE
        if event.get('method') == 'Page.frameNavigated' and event.get('params', {}).get('frame', {}).get('parentId'):
            return GLib.SOURCE_REMOVE
        self.viewport_revision += 1
        self.refresh_viewport()
        return GLib.SOURCE_REMOVE

    def refresh_viewport(self):
        # Browser zoom and per-site zoom restoration change CSS input units
        # without resizing the GTK widget. Read Chromium's authoritative zoom
        # on its viewport event; do not reset device emulation or poll the page.
        if (not self.session or self.resize_inflight or
                self.viewport_refresh_session == self.session):
            return
        session, revision, size = self.session, self.viewport_revision, self.desired_size
        self.viewport_refresh_session = session
        def ready(result):
            if self.viewport_refresh_session != session or self.session != session:
                return
            self.viewport_refresh_session = None
            if result is None:
                return
            if revision != self.viewport_revision or size != self.desired_size:
                self.refresh_viewport()
                return
            self.viewport = self.input_viewport(result)
        self.page_requests.call('Page.getLayoutMetrics', {}, session, ready)

    @staticmethod
    def input_viewport(result):
        visual = result['cssVisualViewport']
        return dict(result['cssLayoutViewport'],
                    input_zoom=visual.get('zoom', 1) * visual.get('scale', 1))

    def resize(self, width, height, scale):
        self.page.expected_buffer_size = (round(width * scale), round(height * scale))
        size = (width, height, scale)
        if size == self.desired_size:
            return
        self.desired_size = size
        if self.resize_timer is None:
            self.resize_timer = GLib.timeout_add(40, self.flush_resize)

    def flush_resize(self):
        self.resize_timer = None
        if not self.session or not self.desired_size or self.resize_inflight:
            return GLib.SOURCE_REMOVE
        size, target, session = self.desired_size, self.target, self.session
        width, height, scale = size
        self.resize_inflight = True
        layout_revision = [self.viewport_revision]
        def failed():
            self.resize_inflight = False
            self.target = self.session = self.viewport = None
            self.metrics['geometry_rejections'] = self.metrics.get('geometry_rejections', 0) + 1
        def layout_ready(result):
            self.resize_inflight = False
            if result is None:
                failed()
                return
            self.viewport = self.input_viewport(result)
            if size != self.desired_size:
                self.flush_resize()
            elif layout_revision[0] != self.viewport_revision:
                self.refresh_viewport()
        def visible_ready(result):
            if result is None:
                failed()
                return
            layout_revision[0] = self.viewport_revision
            self.page_requests.call('Page.getLayoutMetrics', {}, session, layout_ready)
        def metrics_ready(result):
            if result is None:
                failed()
                return
            self.page_requests.call('Emulation.setVisibleSize', {
                'width': round(width * scale), 'height': round(height * scale)}, session, visible_ready)
        self.page_requests.call('Emulation.setDeviceMetricsOverride', {
            'width': width, 'height': height, 'deviceScaleFactor': scale,
            'scale': scale, 'mobile': False, 'screenWidth': width, 'screenHeight': height,
            # Preserve Blink's root scroll transform; size the physical view
            # separately while applying the native surface's fractional scale.
            'dontSetVisibleSize': True}, session, metrics_ready)
        return GLib.SOURCE_REMOVE

    def close(self):
        if hasattr(self, 'touch'):
            self.touch.close()
        self.page_requests.clear()
        self.target = self.session = self.viewport = None
        self.viewport_refresh_session = None
        self.desired_size = None
        self.deferred_motion = None
        self.wheel_queue = []
        for name in ('resize_timer', 'motion_retry', 'wheel_timer'):
            source = getattr(self, name, None)
            if source:
                GLib.source_remove(source)
                setattr(self, name, None)
        self.page.on_frame = lambda *_: None
        self.page.on_resize = lambda *_: None

    def resize_failed(self):
        self.resize_inflight = False
        return GLib.SOURCE_REMOVE

    def valid_identity(self):
        return bool(self.session and self.tab_id and self.services.state
                    and self.tab_id == self.services.state.get('activeTabId')
                    and self.page.frame and self.page.frame['target_id'] == self.target)

    def dispatch(self, method, params, *, flush_motion=False, on_settled=None):
        if not self.valid_identity():
            self.metrics['identity_rejections'] += 1
            return False
        session, target, tab_id = self.session, self.target, self.tab_id
        identity = (session, target, tab_id)
        is_motion = method == 'Input.dispatchMouseEvent' and params.get('type') == 'mouseMoved'
        if is_motion and self.pending >= 8 and not flush_motion:
            self.deferred_motion = (identity, params)
            self.metrics['pointer_backpressure'] += 1
            if not getattr(self, 'motion_retry', None):
                self.motion_retry = GLib.timeout_add(16, self.retry_motion)
            return True
        if not is_motion:
            deferred = getattr(self, 'deferred_motion', None)
            self.deferred_motion = None
            if deferred and deferred[0] == identity:
                # A release must follow the last held position even when ACKs
                # are slow. Dropping it can turn a short drag into a click.
                self.dispatch('Input.dispatchMouseEvent', deferred[1], flush_motion=True)
        if method != 'Input.dispatchMouseEvent' or params.get('type') not in ('mouseMoved', 'mouseWheel'):
            self.flush_wheels(force=True)
        if on_settled is None and self.last_pending_input and self.last_pending_input.merge(identity, method, params):
            self.metrics['coalesced'] += 1
            return True
        if self.pending >= 64:
            if method == 'Input.dispatchMouseEvent' and params.get('type') == 'mouseMoved':
                self.metrics['pointer_backpressure'] += 1
                return True
            self.on_error('Native discrete input backlog exceeded its qualification bound')
            return False
        queued_at = time.monotonic()
        entry = PendingInput(identity, method, params)
        self.last_pending_input = entry
        self.pending += 1
        self.metrics['inflight_max'] = max(self.metrics.get('inflight_max', 0), self.pending)
        def send():
            queued_params = entry.take()
            wait_ms = (time.monotonic() - queued_at) * 1000
            self.metrics['queue_wait_ms_max'] = max(self.metrics.get('queue_wait_ms_max', 0), wait_ms)
            # Recheck after waiting behind other service work. A tab switch
            # must not deliver queued text into the previous hidden page.
            if (target != self.target or session != self.session
                    or tab_id != (self.services.state or {}).get('activeTabId')):
                return False
            began = time.monotonic()
            if hasattr(self.engine, 'request'):
                response = self.engine.request(method, queued_params, session)
                response.input_started = began
                return response
            self.engine.call(method, queued_params, session)
            return True
        future = getattr(self, 'input_submit', self.submit)(send)
        settled = [False]
        def complete_input():
            if settled[0]:
                return False
            settled[0] = True
            self.pending -= 1
            if on_settled:
                GLib.idle_add(on_settled)
            return True
        def expire():
            if complete_input():
                self.metrics['ack_timeouts'] = self.metrics.get('ack_timeouts', 0) + 1
            return GLib.SOURCE_REMOVE
        deadline = GLib.timeout_add_seconds(5, expire)
        def acknowledge(response):
            if not complete_input():
                return GLib.SOURCE_REMOVE
            GLib.source_remove(deadline)
            if response.exception():
                # Destruction and renderState travel on different queues. A
                # tab can already be gone while all cached identities still
                # match. Its late input acknowledgement is not an app failure.
                if (target != self.target or session != self.session
                        or tab_id != (self.services.state or {}).get('activeTabId')
                        or any(message in str(response.exception()) for message in (
                            'Session with given id not found', 'No target with given id', 'Target closed'))):
                    self.metrics['identity_rejections'] += 1
                else:
                    self.on_error(str(response.exception()))
            else:
                self.metrics['sent'] += 1
                elapsed = (time.monotonic() - response.input_started) * 1000
                self.metrics['ack_ms_max'] = max(self.metrics.get('ack_ms_max', 0), elapsed)
            return GLib.SOURCE_REMOVE
        def finished(result):
            if result.exception() or not isinstance(result.result(), Future):
                if not complete_input():
                    return
                GLib.source_remove(deadline)
            if result.exception():
                GLib.idle_add(self.on_error, str(result.exception()))
            elif isinstance(result.result(), Future):
                result.result().add_done_callback(lambda response: GLib.idle_add(acknowledge, response))
            else:
                if result.result():
                    self.metrics['sent'] += 1
        future.add_done_callback(finished)
        return True

    def retry_motion(self):
        deferred = self.deferred_motion
        if deferred is None:
            self.motion_retry = None
            return GLib.SOURCE_REMOVE
        if self.pending >= 8:
            return GLib.SOURCE_CONTINUE
        self.deferred_motion = None
        self.motion_retry = None
        identity, params = deferred
        if identity == (self.session, self.target, self.tab_id):
            self.dispatch('Input.dispatchMouseEvent', params)
        return GLib.SOURCE_REMOVE

    def point(self, x, y):
        if not self.viewport:
            return None
        width, height = self.page.get_width(), self.page.get_height()
        if width < 1 or height < 1:
            return None
        scale = self.desired_size[2] if self.desired_size else 1
        # Layout client dimensions exclude scrollbar gutters. Using them as
        # the input extent shifts edge hits into the page and changes pointer
        # speed whenever a scrollbar appears. The full native allocation owns
        # the surface; CDP applies page zoom again when injecting the event.
        zoom = self.viewport.get('input_zoom', 1)
        return {'x': max(0, min(x, width)) * scale / zoom,
                'y': max(0, min(y, height)) * scale / zoom}

    def _motion(self, controller, x, y):
        from page_touch import touchscreen
        if touchscreen(controller):
            return
        self.pointer = (x, y)
        getattr(self, 'on_motion', lambda *_: None)(x, y)
        point = self.point(x, y)
        if point:
            # Chromium's drag handler checks the button identity as well as
            # the held-button mask. Omitting it makes pressed motion a hover.
            button = next((name for bit, name in (
                (1, 'left'), (2, 'right'), (4, 'middle'), (8, 'back'), (16, 'forward'))
                if self.buttons & bit), 'none')
            self.dispatch('Input.dispatchMouseEvent', {'type': 'mouseMoved', **point,
                'button': button, 'buttons': self.buttons,
                'modifiers': modifiers(controller.get_current_event_state())})

    def click_event(self, gesture, pressed, button, count, x, y, state=0):
        from page_touch import touchscreen
        if not touchscreen(gesture):
            self.pointer_button(pressed, button, count, x, y, state)

    def pointer_button(self, pressed, button, count, x, y, state=0):
        mapping = {1: ('left', 1), 2: ('middle', 4), 3: ('right', 2),
                   8: ('back', 8), 9: ('forward', 16)}
        if button not in mapping:
            return
        name, bit = mapping[button]
        self.pointer = (x, y)
        self.buttons = self.buttons | bit if pressed else self.buttons & ~bit
        point = self.point(x, y)
        if not point:
            return
        if pressed:
            self.page.grab_focus()
        self.dispatch('Input.dispatchMouseEvent', {
            'type': 'mousePressed' if pressed else 'mouseReleased', **point,
            'button': name, 'buttons': self.buttons, 'clickCount': count,
            'modifiers': modifiers(state)})

    def release_pointer_buttons(self):
        # A native popup takes the GTK grab before the physical release reaches
        # this page. End the forwarded press before handing input to the popup;
        # otherwise Blink sees later clicks as additional held mouse buttons and
        # omits pointerdown (breaking custom seek bars and dropdowns).
        for button, bit in ((1, 1), (2, 4), (3, 2), (8, 8), (9, 16)):
            if self.buttons & bit:
                self.pointer_button(False, button, 1, *self.pointer)

    def queue_wheel(self, params):
        if not self.valid_identity():
            self.metrics['identity_rejections'] += 1
            return False
        identity = (self.session, self.target, self.tab_id)
        queue = self.wheel_queue
        if queue and queue[-1].merge(identity, 'Input.dispatchMouseEvent', params):
            self.metrics['wheel_coalesced'] = self.metrics.get('wheel_coalesced', 0) + 1
        else:
            if len(queue) >= 16:
                self.flush_wheels(force=True)
            queue.append(PendingInput(identity, 'Input.dispatchMouseEvent', params))
        if self.wheel_timer is None:
            self.wheel_timer = GLib.timeout_add(16, self.flush_wheels)
        return True

    def flush_wheels(self, *, force=False):
        queue = getattr(self, 'wheel_queue', [])
        if force and getattr(self, 'wheel_timer', None):
            GLib.source_remove(self.wheel_timer)
        self.wheel_timer = None
        while queue and (force or self.wheel_inflight < 2):
            entry = queue.pop(0)
            if entry.identity != (self.session, self.target, self.tab_id):
                self.metrics['identity_rejections'] += 1
                continue
            self.wheel_inflight += 1
            if not self.dispatch(entry.method, entry.take(), on_settled=self.wheel_settled):
                self.wheel_inflight -= 1
        if queue and self.wheel_inflight < 2:
            self.wheel_timer = GLib.timeout_add(16, self.flush_wheels)
        return GLib.SOURCE_REMOVE

    def wheel_settled(self):
        self.wheel_inflight = max(0, self.wheel_inflight - 1)
        if self.wheel_queue and self.wheel_timer is None:
            self.wheel_timer = GLib.timeout_add(16, self.flush_wheels)
        return GLib.SOURCE_REMOVE

    def _scroll(self, controller, dx, dy):
        self.metrics['scroll_events'] = self.metrics.get('scroll_events', 0) + 1
        self.metrics['last_scroll'] = [dx, dy, str(controller.get_unit()), list(self.pointer)]
        point = self.point(*self.pointer)
        if not point:
            return False
        # Match Chromium's Linux WaylandPointer::OnAxis conversion:
        # GDK wheel units are notches; surface units retain raw Wayland
        # values (10 per conventional notch), not Chromium scroll pixels.
        factor = 120 if controller.get_unit() == Gdk.ScrollUnit.WHEEL else 12
        getattr(self, 'on_scroll', lambda *_: None)(dx * factor, dy * factor)
        return self.queue_wheel({'type': 'mouseWheel', **point,
            'deltaX': dx * factor, 'deltaY': dy * factor,
            'modifiers': modifiers(controller.get_current_event_state())})

    def _focus(self, focused):
        if focused:
            self.ime.focus_in()
        else:
            if hasattr(self, 'touch'):
                self.touch.cancel()
            self.ime.focus_out()
            self.ime.reset()
        self.dispatch('Emulation.setFocusEmulationEnabled', {'enabled': focused})

    def _key_event(self, pressed, keyval, keycode, state):
        # These commands belong to AppKit. Forwarding them as page keys too
        # allows a second Chromium accelerator to close/create another tab.
        if native_shortcut(keyval, state):
            return False
        character = Gdk.keyval_to_unicode(keyval)
        text = chr(character) if character >= 32 and character != 127 else ''
        name = Gdk.keyval_name(keyval) or 'Unidentified'
        key, virtual = KEY_NAMES.get(name, (text or name, ord(text.upper())
            if len(text) == 1 and text.isascii() and text.isalnum() else 0))
        self.dispatch('Input.dispatchKeyEvent', {
            'type': 'rawKeyDown' if pressed else 'keyUp', 'key': key,
            'code': PHYSICAL_KEYS.get(str(keycode), ''), 'windowsVirtualKeyCode': virtual,
            'nativeVirtualKeyCode': keyval, 'modifiers': modifiers(state)})
        return False

    def _unfiltered_key(self, _controller, keyval, _keycode, state):
        if native_shortcut(keyval, state):
            return False
        # Called only when the installed GTK IM context did not consume it.
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not modifiers(state) & 7:
            # Native key-down and character delivery are separate. Chromium
            # needs the unconsumed Enter character for implicit form submission
            # and textarea newlines; IME-consumed Enter never reaches this path.
            self.dispatch('Input.dispatchKeyEvent', {
                'type': 'char', 'key': 'Enter',
                'code': 'NumpadEnter' if keyval == Gdk.KEY_KP_Enter else 'Enter',
                'text': '\r', 'unmodifiedText': '\r',
                'windowsVirtualKeyCode': 13, 'modifiers': modifiers(state)})
            return True
        character = Gdk.keyval_to_unicode(keyval)
        if character >= 32 and character != 127 and not modifiers(state) & 7:
            self.commit_text(self.ime, chr(character))
        # Browser keys (including Tab/arrows) remain inside the browser page.
        return True

    def commit_text(self, _context, text):
        self.composing = False
        self.dispatch('Input.insertText', {'text': text})

    def _preedit(self, context):
        text, _attributes, cursor = context.get_preedit_string()
        self.composing = bool(text)
        position = utf16_length(text[:cursor])
        self.dispatch('Input.imeSetComposition', {
            'text': text, 'selectionStart': position, 'selectionEnd': position})
