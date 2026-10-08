# SPDX-License-Identifier: GPL-3.0-only
"""Window-scoped native shells over one profile-owned Chromium process."""
import threading
import time
import uuid
from gi.repository import GLib
from browser_services import BrowserServices
from browser_favicons import BrowserFavicons
from native_media import NativeMedia
from native_identity_menu import NativeIdentityMenu
from page_input import PageInput


def application_menu(services):
    # The authoritative menu channel requires a bounded anchor even when its
    # retained commands are invoked by a shortcut instead of a visible popup.
    return services.open_menu('app:menu', {'anchorRect': {'x': 0, 'y': 0, 'width': 28, 'height': 28}})


class EngineClient:
    """Keep each service adapter's event subscriptions local to its window."""
    def __init__(self, owner):
        self.owner = owner
        self.on_event = lambda _event: None

    native_new_tabs = True

    @property
    def process(self):
        return self.owner.engine.process

    def call(self, *args, **kwargs):
        return self.owner.engine.call(*args, **kwargs)

    def request(self, *args, **kwargs):
        return self.owner.engine.request(*args, **kwargs)


class NativeWindows:
    def __init__(self, application, loop, submit, input_submit, error, factory,
                 primary, *, present=False, manual=False, preview_ready=None):
        self.application, self.loop = application, loop
        self.submit, self.input_submit, self.error = submit, input_submit, error
        self.factory, self.present, self.manual = factory, present, manual
        self.preview_ready = preview_ready
        self.engine = None
        self.contexts = {}
        self.clients = []
        self.detached_tabs = {}
        self.ready = threading.Condition()
        self.primary_description = None
        self.shutting_down = False
        self.primary = self.prepare(primary)

    def prepare(self, window):
        client = EngineClient(self)
        self.clients.append(client)
        context = {'window': window, 'client': client, 'closed': False, 'description': None}
        window.native_context = context
        window.window_manager = self
        def scoped_submit(work, done=None):
            def guarded_work():
                if context['closed']:
                    return None
                try:
                    return work()
                except Exception:
                    if not context['closed']:
                        raise
            def guarded_done(value):
                if not context['closed'] and done:
                    done(value)
            return self.submit(guarded_work, guarded_done)
        window.submit = scoped_submit
        def render(state):
            if not context['closed']:
                window.render_state(state)
            return False
        services = BrowserServices(client, lambda state: GLib.idle_add(render, state), native_media=True)
        context['services'] = window.services = services
        window.manual_identity = self.manual
        def preview(paintable, tab_id):
            if not context['closed']:
                window.sidebar.footer.live_preview(paintable, tab_id)
                if window.pip_window:
                    window.pip_window.update(paintable, tab_id)
                if paintable is not None and self.preview_ready:
                    self.preview_ready(window)
        media = context['media'] = NativeMedia(services, scoped_submit, preview, self.error)
        services.on_native_media = lambda payload: GLib.idle_add(
            lambda: media.event(payload) if not context['closed'] else False)
        services.on_native_event = lambda name, payload: GLib.idle_add(
            lambda: window.native_event(name, payload) if not context['closed'] else False)
        services.on_native_menu = lambda model: GLib.idle_add(
            lambda: window.native_menu(model) if not context['closed'] else False)
        window.favicons = BrowserFavicons(services)
        window.page_input = PageInput(window.page, client, services, scoped_submit,
                                      self.error, input_submit=self.input_submit)
        window.page_input.on_scroll = window.responsive.page_scrolled
        window.page_input.on_motion = window.responsive.pointer_moved
        from native_dialogs import NativeDialogs
        context['dialogs'] = NativeDialogs(window)
        window.page_input.on_session_changed = context['dialogs'].sync
        return context

    def event(self, event):
        for client in tuple(self.clients):
            client.on_event(event)
        if event.get('method') in ('Page.frameResized', 'Page.frameNavigated'):
            for context in tuple(self.contexts.values()):
                if not context['closed']:
                    GLib.idle_add(lambda owner=context, packet=event:
                                  owner['window'].page_input.page_event(packet)
                                  if not owner['closed'] else False)
        if event.get('method') in ('Page.javascriptDialogOpening','Page.javascriptDialogClosed'):
            for context in tuple(self.contexts.values()):
                if not context['closed']:
                    GLib.idle_add(lambda owner=context: owner['dialogs'].event(event)
                                  if not owner['closed'] else False)

    def announced(self, description):
        if self.shutting_down:
            return False
        identity = description['window_id']
        if identity in self.contexts:
            self.error('Duplicate native browser window identity')
            return False
        if self.primary_description is None:
            context = self.primary
            with self.ready:
                self.primary_description = description
                self.ready.notify_all()
        else:
            context = self.prepare(self.factory(mini=description.get('presentation') in ('mini', 'devtools')))
        context['description'] = description
        if description.get('presentation') == 'devtools':
            window = context['window']
            window.developer_tools = True
            window.set_default_size(1100, 760)
            window.toolbar.set_visible(False)
            window.mini_presentation.expand.set_visible(False)
            window.mini_presentation.idle.close()
        self.contexts[identity] = context
        if description.get('visibility_control') and getattr(self, 'frame_receiver', None):
            from native_visibility import NativeVisibility
            context['visibility'] = NativeVisibility(context['window'], identity, self.frame_receiver)
        if context is not self.primary:
            context['window'].submit(lambda: self.connect(context), lambda _: self.connected(context))
        return False

    def wait_primary(self):
        deadline = time.monotonic() + 15
        with self.ready:
            while self.primary_description is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError('Native engine did not announce its browser window')
                self.ready.wait(left)
        return self.primary_description

    def connect(self, context):
        description = context['description']
        context['services'].connect(targets={
            'sidebar': description['sidebar_target_id'],
            'overlay': description['overlay_target_id']})

    def connected(self, context):
        window = context['window']
        def identity(model):
            window.identity_menu = NativeIdentityMenu(window, model)
            def show(_=None):
                if self.present:
                    window.present()
                    if tab or window.mini:
                        window.page.grab_focus()
                    else:
                        # One initial focus assignment only. Subsequent frames
                        # and state updates must not interrupt typing or clicks.
                        window._focus_address()
            tab = self.detached_tabs.pop(window.services.state.get('activeUrl'), None)
            if tab:
                placeholder = window.services.state['activeTabId']
                def transfer():
                    window.services.send('sidebar', 'tab:activate', {'tabId': tab})
                    window.services.wait_state(lambda state: state.get('activeTabId') == tab)
                    if placeholder != tab:
                        window.services.send('sidebar', 'tab:close', {'tabId': placeholder})
                window.submit(transfer, show)
            else:
                show()
        window.submit(lambda: application_menu(window.services), identity)

    def detach_tab(self, window, tab_id):
        if window.native_context['closed'] or window.services.state.get('incognito'):
            return
        url = 'data:text/html,%3Ctitle%3ENew%20window%3C/title%3E#viola-' + uuid.uuid4().hex
        self.detached_tabs[url] = tab_id
        def create():
            try:
                return self.engine.call('Target.createTarget', {
                    'url': url, 'newWindow': True, 'background': False, 'windowState': 'normal'})
            except Exception:
                self.detached_tabs.pop(url, None)
                raise
        window.submit(create)

    def frame(self, texture, metadata):
        context = self.contexts.get(metadata['window_id'])
        if context and not context['closed']:
            context['window'].receive_page_frame(texture, metadata)

    def cursor(self, metadata):
        context = self.contexts.get(metadata['window_id'])
        if context and not context['closed']:
            context['window'].page.cursor_changed(metadata)
        return False

    def active_window(self):
        active = self.application.get_active_window()
        if active and getattr(active, 'native_context', {}).get('closed') is False:
            return active
        return next((ctx['window'] for ctx in self.contexts.values() if not ctx['closed']), None)

    def command(self, window, label):
        def invoke():
            services = window.services
            model = application_menu(services)
            row = next((row for row in model['items']
                        if row['label'] == label and row['enabled'] and row['visible']), None)
            if row is None:
                services.dismiss_menu(model['nonce'])
                raise RuntimeError('Original browser command is unavailable: ' + label)
            services.activate_menu(model['nonce'], [row['index']])
            return row
        return window.submit(invoke)

    def backend_closed(self, identity):
        if self.shutting_down:
            return False
        context = self.contexts.get(identity)
        if context:
            self.close_window(context['window'], close_backend=False)
        return False

    def close_window(self, window, close_backend=True):
        context = window.native_context
        if context['closed']:
            return
        context['closed'] = True
        identity = (context['description'] or {}).get('window_id')
        self.contexts.pop(identity, None)
        if window.pip_window:
            window.pip_window.dismiss()
        context['dialogs'].dismiss()
        if window.mini:
            window.mini_presentation.idle.close()
        window.sidebar.reorder.close()
        window.sidebar.preferences.close()
        window.page_input.close()
        context['media'].close()
        window.favicons.close()
        window.address_suggestions.close()
        if window.open_popover:
            window.open_popover.popdown()
        window.pending_page_frame = None
        window.page.clear()
        window.destroy()
        def finish_backend():
            try:
                if close_backend and self.engine.process.poll() is None:
                    services = context['services']
                    model = application_menu(services)
                    row = next(row for row in model['items'] if row['label'] == 'Close window')
                    context['close_command'] = services.activate_menu(model['nonce'], [row['index']], wait=False)
            except RuntimeError as error:
                if not any(text in str(error) for text in ('Target closed', 'Session with given id not found', 'No target with given id', 'engine pipe closed')):
                    raise
            finally:
                context['services'].close(local_only=True)
        def finished(_):
            if context['client'] in self.clients:
                self.clients.remove(context['client'])
            if not self.contexts and not self.shutting_down:
                self.loop.quit()
        self.submit(finish_backend, finished)

    def shutdown(self):
        self.shutting_down = True
        for context in list(self.contexts.values()):
            self.close_window(context['window'], close_backend=False)
