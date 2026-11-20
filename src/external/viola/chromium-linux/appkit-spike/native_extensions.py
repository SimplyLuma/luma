# SPDX-License-Identifier: GPL-3.0-only
"""Extensions in the bar: run one, its menu, and the page it opens.

The engine lists the profile's enabled extensions in its state (`extensions`:
id, name, icon, pinned; engine patch 0008, pinned first). Pinned extensions
sit in the bar as their own icon; the Extensions key lists them all. Running
one sends `ext:openControl` `execute`, the toolbar click Chromium performs.

Chromium draws an extension's popup or side panel on the undisplayed browser
window. Its page is its own DevTools target: after a run, the first new
`chrome-extension://<id>/` page that is not a tab is the surface. The bar
grows into it, drawn from the target's screencast, with pointer, wheel and key
input sent to that target. Folding the bar closes the page; a page that closes
itself folds the bar. The context menu (pin, site access, options, remove) is
Chromium's own model on the native menu channel.
"""
import base64
import binascii
import time

from gi.repository import Gdk, GLib, Gtk

from luma_appkit.bar_panel import PanelRow, panel_list
from page_input import KEY_NAMES, PHYSICAL_KEYS, modifiers

EXTENSION_ID_LENGTH = 32
SURFACE_WAIT_SECONDS = 3.0
SURFACE_POLL_MS = 120
MAX_SURFACE_WIDTH = 800
MAX_SURFACE_HEIGHT = 900
MAX_FRAME_BYTES = 16 * 1024 * 1024
ICON_SIZE = 18


def valid_id(value):
    return (isinstance(value, str) and len(value) == EXTENSION_ID_LENGTH
            and all('a' <= character <= 'p' for character in value))


def extension_rows(state):
    """The engine's extension rows, minus anything malformed."""
    rows = state.get('extensions') if isinstance(state, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows[:24] if isinstance(row, dict) and valid_id(row.get('id'))
            and isinstance(row.get('name'), str)]


def rows_signature(rows):
    return tuple((row['id'], row['name'], bool(row.get('pinned'))) for row in rows)


def tab_urls(state):
    urls = set()
    for key in ('favorites', 'pinned', 'today'):
        for tab in (state or {}).get(key, []) or []:
            if isinstance(tab, dict) and isinstance(tab.get('url'), str):
                urls.add(tab['url'])
    return urls


def surface_target(targets, extension_id, known, tabs):
    """The page a run opened: a new page target of that extension that is not a tab."""
    prefix = 'chrome-extension://' + extension_id + '/'
    for target in targets:
        if (target.get('type') == 'page' and str(target.get('url', '')).startswith(prefix)
                and target.get('targetId') not in known and target.get('url') not in tabs):
            return target
    return None


def frame_size(metadata):
    width = int(round(float(metadata.get('deviceWidth') or 0)))
    height = int(round(float(metadata.get('deviceHeight') or 0)))
    if not (0 < width <= 4096 and 0 < height <= 4096):
        return None
    return width, height


def display_size(size):
    """The size the bar shows a frame at: its own, inside the surface bounds."""
    width, height = size
    scale = min(1.0, MAX_SURFACE_WIDTH / width, MAX_SURFACE_HEIGHT / height)
    return max(1, int(width * scale)), max(1, int(height * scale))


def page_point(x, y, allocated, size):
    """A point on the drawn frame in the page's own CSS pixels, or None off the frame."""
    width, height = allocated
    if width <= 0 or height <= 0 or not size:
        return None
    # The frame keeps its aspect inside the widget (Gtk.ContentFit.CONTAIN).
    scale = min(width / size[0], height / size[1])
    left = (width - size[0] * scale) / 2
    top = (height - size[1] * scale) / 2
    px, py = (x - left) / scale, (y - top) / scale
    if not (0 <= px < size[0] and 0 <= py < size[1]):
        return None
    return {'x': px, 'y': py}


BUTTONS = {1: 'left', 2: 'middle', 3: 'right'}
BUTTON_MASKS = {'left': 1, 'right': 2, 'middle': 4}


class ExtensionSurface(Gtk.Box):
    """One extension page drawn from its screencast, with its input."""

    def __init__(self, owner, extension_id, name, target_id, session):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, focusable=True,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        self.add_css_class('viola-extension-surface')
        self.update_property([Gtk.AccessibleProperty.LABEL], [name])
        self.owner = owner
        self.extension_id = extension_id
        self.target_id = target_id
        self.session = session
        self.size = None
        self.closed = False
        self.buttons = 0
        self.pointer = (0.0, 0.0)
        self.last_motion = 0.0
        # The page keeps its own size (a popup chooses it), centred in the bar.
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.picture.set_size_request(320, 120)
        self.append(self.picture)
        click = Gtk.GestureClick(button=0)
        click.connect('pressed', self._pressed)
        click.connect('released', self._released)
        self.picture.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._motion)
        self.picture.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect('scroll', self._scroll)
        self.picture.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', lambda _c, keyval, code, state: self._key(True, keyval, code, state))
        keys.connect('key-released', lambda _c, keyval, code, state: self._key(False, keyval, code, state))
        self.add_controller(keys)

    # Frames arrive on the engine's reader thread; textures are made here.
    def frame(self, data, metadata):
        if self.closed:
            return GLib.SOURCE_REMOVE
        size = frame_size(metadata)
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return GLib.SOURCE_REMOVE
        if size and size != self.size:
            self.size = size
            self.picture.set_size_request(*display_size(size))
        self.picture.set_paintable(texture)
        return GLib.SOURCE_REMOVE

    def dispatch(self, method, params):
        if not self.closed:
            self.owner.request(method, params, self.session)

    def _point(self, controller, x, y):
        return page_point(x, y, (self.picture.get_width(), self.picture.get_height()), self.size)

    def _pressed(self, controller, count, x, y):
        self.grab_focus()
        point = self._point(controller, x, y)
        button = BUTTONS.get(controller.get_current_button())
        if not point or not button:
            return
        self.buttons |= BUTTON_MASKS[button]
        self.dispatch('Input.dispatchMouseEvent', {'type': 'mousePressed', **point, 'button': button,
                      'buttons': self.buttons, 'clickCount': max(1, min(3, count)),
                      'modifiers': modifiers(controller.get_current_event_state())})

    def _released(self, controller, count, x, y):
        point = self._point(controller, x, y)
        button = BUTTONS.get(controller.get_current_button())
        if not button:
            return
        self.buttons &= ~BUTTON_MASKS[button]
        if not point:
            return
        self.dispatch('Input.dispatchMouseEvent', {'type': 'mouseReleased', **point, 'button': button,
                      'buttons': self.buttons, 'clickCount': max(1, min(3, count)),
                      'modifiers': modifiers(controller.get_current_event_state())})

    def _motion(self, controller, x, y):
        self.pointer = (x, y)
        now = time.monotonic()
        # Hover is coalesced to about 60 per second; a drag sends every move.
        if not self.buttons and now - self.last_motion < 0.016:
            return
        self.last_motion = now
        point = self._point(controller, x, y)
        if point:
            self.dispatch('Input.dispatchMouseEvent', {'type': 'mouseMoved', **point,
                          'buttons': self.buttons,
                          'modifiers': modifiers(controller.get_current_event_state())})

    def _scroll(self, controller, dx, dy):
        point = self._point(controller, *self.pointer)
        if not point:
            return False
        factor = 120 if controller.get_unit() == Gdk.ScrollUnit.WHEEL else 12
        self.dispatch('Input.dispatchMouseEvent', {'type': 'mouseWheel', **point,
                      'deltaX': dx * factor, 'deltaY': dy * factor,
                      'modifiers': modifiers(controller.get_current_event_state())})
        return True

    def _key(self, pressed, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            # Esc folds the bar (its capture phase), which closes the page.
            return False
        character = Gdk.keyval_to_unicode(keyval)
        text = chr(character) if character >= 32 and character != 127 and not modifiers(state) & 7 else ''
        name = Gdk.keyval_name(keyval) or 'Unidentified'
        key, virtual = KEY_NAMES.get(name, (text or name, ord(text.upper())
                                            if len(text) == 1 and text.isascii() and text.isalnum() else 0))
        params = {'type': ('keyDown' if text else 'rawKeyDown') if pressed else 'keyUp', 'key': key,
                  'code': PHYSICAL_KEYS.get(str(keycode), ''), 'windowsVirtualKeyCode': virtual,
                  'nativeVirtualKeyCode': keyval, 'modifiers': modifiers(state)}
        if pressed and text:
            params['text'] = params['unmodifiedText'] = text
        elif pressed and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            params['type'] = 'keyDown'
            params['text'] = params['unmodifiedText'] = '\r'
        self.dispatch('Input.dispatchKeyEvent', params)
        return True


class NativeExtensions:
    """The bar's extension controls and the surface a run opens, for one window."""

    def __init__(self, host):
        self.host = host
        self.surface = None
        self.pending = None
        self.icons = {}

    # ---- engine access -------------------------------------------------
    def services(self):
        return getattr(self.host, 'services', None)

    def request(self, method, params=None, session=None):
        services = self.services()
        if services is None:
            return None
        try:
            return services.engine.request(method, params, session)
        except Exception:
            return None

    # ---- the rows ------------------------------------------------------
    def rows(self):
        return extension_rows(self.host.state)

    def icon_widget(self, row, size=ICON_SIZE):
        image = Gtk.Image(icon_name='lumaui-blocks-symbolic', pixel_size=size,
                          accessible_role=Gtk.AccessibleRole.PRESENTATION)
        image.add_css_class('viola-extension-icon')
        url = row.get('icon')
        favicons = getattr(self.host, 'favicons', None)
        if isinstance(url, str) and url.startswith('chrome://extension-icon/') and favicons:
            def received(texture, target=image):
                if texture is not None:
                    target.set_from_paintable(texture)
            favicons.request(url, received, revision=('extension-icon', row['id']))
        return image

    def panel(self):
        rows = []
        for row in self.rows():
            item = PanelRow(row['name'], lead=self.icon_widget(row, 24),
                            note='Pinned' if row.get('pinned') else None,
                            on_activate=lambda key=row['id']: self.run(key))
            self.secondary(item, row['id'])
            rows.append(item)
        rows.append(None)
        rows.append(PanelRow('Manage extensions', icon='settings',
                             on_activate=lambda: self.host.send('ext:openControl', {'extId': None})))
        return panel_list(rows, label='Extensions')

    def secondary(self, widget, extension_id):
        """Right-click or press-and-hold opens the extension's own menu."""
        click = Gtk.GestureClick(button=3)
        click.connect('pressed', lambda *_: self.menu(extension_id, widget))
        widget.add_controller(click)
        hold = Gtk.GestureLongPress()
        hold.connect('pressed', lambda *_: self.menu(extension_id, widget))
        widget.add_controller(hold)

    def bar_button(self, row):
        button = Gtk.Button(child=self.icon_widget(row), valign=Gtk.Align.CENTER)
        button.add_css_class('lumaui-bar-action')
        button.add_css_class('viola-extension-action')
        button.set_tooltip_text(row['name'])
        button.update_property([Gtk.AccessibleProperty.LABEL], [row['name']])
        button.connect('clicked', lambda *_: self.run(row['id'], anchor=button))
        self.secondary(button, row['id'])
        return button

    def menu(self, extension_id, anchor):
        services = self.services()
        if services is None or not valid_id(extension_id):
            return
        self.host.submit(lambda: self.open_menu(services, extension_id),
                         lambda model: model and self.host.show_menu(
                             anchor, model, position=Gtk.PositionType.TOP))

    @staticmethod
    def open_menu(services, extension_id):
        try:
            return services.open_menu('ext:openControl', {'extId': extension_id, 'action': 'menu'})
        except Exception:
            return None

    # ---- running and the surface ---------------------------------------
    def run(self, extension_id, anchor=None):
        services = self.services()
        if services is None or not valid_id(extension_id):
            return
        self.close_surface()
        name = next((row['name'] for row in self.rows() if row['id'] == extension_id), 'Extension')
        tabs = tab_urls(self.host.state)
        pending = self.pending = {'id': extension_id, 'name': name, 'deadline': None,
                                  'known': None, 'tabs': tabs, 'anchor': anchor}

        def start():
            known = self.extension_targets(services, extension_id)
            if known is None:
                return False
            services.send('sidebar', 'ext:openControl', {'extId': extension_id, 'action': 'execute'})
            return known

        def started(known):
            if self.pending is not pending or known is False:
                return
            pending['known'] = known
            pending['deadline'] = time.monotonic() + SURFACE_WAIT_SECONDS
            GLib.timeout_add(SURFACE_POLL_MS, self.poll, pending)
        self.host.submit(start, started)

    @staticmethod
    def extension_targets(services, extension_id):
        try:
            targets = services.engine.call('Target.getTargets')['targetInfos']
        except Exception:
            return None
        prefix = 'chrome-extension://' + extension_id + '/'
        return {target['targetId'] for target in targets if str(target.get('url', '')).startswith(prefix)}

    def poll(self, pending):
        if self.pending is not pending:
            return GLib.SOURCE_REMOVE
        services = self.services()
        if services is None:
            return GLib.SOURCE_REMOVE

        def look():
            try:
                targets = services.engine.call('Target.getTargets')['targetInfos']
            except Exception:
                return None
            target = surface_target(targets, pending['id'], pending['known'], pending['tabs'])
            if target is None:
                return None
            try:
                session = services.engine.call('Target.attachToTarget', {
                    'targetId': target['targetId'], 'flatten': True})['sessionId']
                services.engine.call('Page.enable', None, session)
                services.engine.call('Emulation.setFocusEmulationEnabled', {'enabled': True}, session)
                services.engine.call('Page.startScreencast', {'format': 'png', 'everyNthFrame': 1}, session)
            except Exception:
                return None
            return target['targetId'], session

        def found(result):
            if self.pending is not pending:
                if result:
                    self.close_target(services, result[1])
                return
            if result:
                self.pending = None
                self.show_surface(pending, *result)
            elif time.monotonic() < pending['deadline']:
                GLib.timeout_add(SURFACE_POLL_MS, self.poll, pending)
            else:
                # An extension without a popup acted on the click itself.
                self.pending = None
        self.host.submit(look, found)
        return GLib.SOURCE_REMOVE

    def show_surface(self, pending, target_id, session):
        surface = self.surface = ExtensionSurface(self, pending['id'], pending['name'], target_id, session)
        center = self.host.responsive.center
        center.grow('extension', surface, on_fold=lambda: self.surface_folded(surface),
                    panel_padding=False, wide=True)
        self.host.responsive.panel = 'extension'
        GLib.idle_add(lambda: (surface.grab_focus(), False)[1])

    def surface_folded(self, surface):
        if self.surface is surface:
            self.surface = None
        if self.host.responsive.panel == 'extension':
            self.host.responsive.panel = None
        self.end(surface)

    def end(self, surface):
        if surface.closed:
            return
        surface.closed = True
        services = self.services()
        if services is not None:
            self.close_target(services, surface.session)

    def close_target(self, services, session):
        # The page closes as a popup closes when it loses focus; detaching
        # alone would leave it open on the undisplayed window.
        try:
            services.engine.request('Runtime.evaluate', {'expression': 'window.close()'}, session)
        except Exception:
            pass

    def close_surface(self):
        self.pending = None
        surface = self.surface
        if surface is None:
            return
        self.surface = None
        center = self.host.responsive.center
        if center.grown == 'extension':
            center.fold()
        self.end(surface)

    # ---- engine events (reader thread) ---------------------------------
    def event(self, event):
        surface = self.surface
        if surface is None or event.get('sessionId', (event.get('params') or {}).get('sessionId')) is None:
            return
        method = event.get('method')
        params = event.get('params') or {}
        if method == 'Page.screencastFrame' and event.get('sessionId') == surface.session:
            self.request('Page.screencastFrameAck', {'sessionId': params.get('sessionId')}, surface.session)
            raw = params.get('data')
            if not isinstance(raw, str) or len(raw) > MAX_FRAME_BYTES * 4 // 3 + 4:
                return
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                return
            GLib.idle_add(surface.frame, data, params.get('metadata') or {})
        elif method == 'Target.detachedFromTarget' and params.get('sessionId') == surface.session:
            GLib.idle_add(self.target_gone, surface)

    def target_gone(self, surface):
        if self.surface is surface:
            surface.closed = True
            self.close_surface()
        return GLib.SOURCE_REMOVE
