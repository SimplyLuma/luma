# SPDX-License-Identifier: GPL-3.0-only
"""Isolated native composition, not the normal browser launcher.

Default execution inspects a live unpresented window and disposable engine.
--qa-present is an explicit internal QA mode, not release qualification.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import time
import signal

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry, ConnectedButtonGroup, IconButton, Island, Toolbar, install_appkit
from browser_services import BrowserServices
from engine_pipe import EnginePipe
from frame_receiver import FrameReceiver
from gpu_page import GpuPage
from measure_service_navigation import Fixture
from native_menu import NativeMenu
from page_input import PageInput
from native_sidebar import NativeSidebar
from fixed_sidebar import FixedSidebar
from browser_favicons import BrowserFavicons

# The signed portable deployment owns its real application ID, including the
# shared portal update monitor. The installed native application retains its
# existing desktop and activation identity.
APP_ID = ('com.rhyme.viola' if os.environ.get('FLATPAK_ID') == 'com.rhyme.viola'
          else 'org.projectluma.Viola.NativeIntegration')


class IntegratedWindow(AppWindow):
    def __init__(self, application, loop, submit, errors, *, mini=False):
        self.mini = mini
        self.phone = False
        self.loop = loop
        self.submit = submit
        self.errors = errors
        self.services = None
        self.favicons = None
        self.state = None
        self.rows = {}
        self.new_tab_pending = False
        self.open_popover = None
        self.pending_page_frame = None
        commands = CommandRegistry((CommandGroup(None, (
            Command('window.close', 'Close window', self.close, 'window-close-symbolic', shortcut=('Ctrl', 'Shift', 'W')),
            Command('browser.address', 'Open address', self._focus_address, shortcut=('Ctrl', 'L')),
            Command('browser.new-tab', 'New tab', self._new_tab, shortcut=('Ctrl', 'T')),
            Command('browser.restore-tab', 'Reopen closed tab', self._restore_tab, shortcut=('Ctrl', 'Shift', 'T')),
            Command('browser.new-mini', 'New Mini Viola', self._new_mini, shortcut=('Ctrl', 'Alt', 'N')),
            Command('browser.new-window', 'New window', self._new_window, shortcut=('Ctrl', 'N')),
            Command('browser.new-private-window', 'New private window', self._new_private_window, shortcut=('Ctrl', 'Shift', 'N')),
            Command('browser.close-tab', 'Close tab', self._close_tab, shortcut=('Ctrl', 'W')),
            Command('browser.sidebar', 'Toggle sidebar', self._toggle_sidebar, shortcut=('Ctrl', 'S')),
            Command('browser.reload', 'Reload', lambda: self.send('nav:reload'), shortcut=('Ctrl', 'R')),
            Command('browser.back', 'Back', lambda: self.send('nav:back'), shortcut=('Alt', 'Left')),
            Command('browser.forward', 'Forward', lambda: self.send('nav:forward'), shortcut=('Alt', 'Right')),

        )),))
        super().__init__(application=application, app_id=APP_ID, title='Viola',
                         icon_name='com.rhyme.viola', commands=commands,
                         default_width=600 if mini else 1180, default_height=740 if mini else 820,
                         minimum_width=460 if mini else 360, minimum_height=380 if mini else 460,
                         geometry_scope='mini' if mini else '')
        self.on_native_close = None
        self.pip_window = None
        self.connect('close-request', self.close_requested)
        # Mouse history buttons belong to the browser, including while the
        # pointer is over native chrome. Claim each press before page dispatch
        # so Chromium receives exactly the same command as the toolbar.
        self.history_buttons = []
        for button, channel in ((8, 'nav:back'), (9, 'nav:forward')):
            gesture = Gtk.GestureClick(button=button)
            gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            def navigate(controller, _count, _x, _y, command=channel):
                controller.set_state(Gtk.EventSequenceState.CLAIMED)
                self.send(command)
            gesture.connect('pressed', navigate)
            self.add_controller(gesture)
            self.history_buttons.append(gesture)
        composition = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.sidebar = NativeSidebar(self)
        sidebar_bin = Adw.BreakpointBin(child=self.sidebar, hexpand=False, vexpand=True)
        sidebar_bin.set_size_request(252, 1)
        fixed_sidebar = FixedSidebar(sidebar_bin)
        for threshold, property_name in ((699, 'compact-media'), (559, 'compact-notifications')):
            breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f'max-height: {threshold}px'))
            breakpoint.add_setter(self.sidebar.footer, property_name, True)
            if threshold == 559:
                # AdwBreakpointBin activates the last matching breakpoint;
                # the shorter case must retain the taller case's media rule.
                breakpoint.add_setter(self.sidebar.footer, 'compact-media', True)
            sidebar_bin.add_breakpoint(breakpoint)
        self.rows = self.sidebar.rows
        composition.append(fixed_sidebar)
        self.sidebar_bin = fixed_sidebar
        self.island = Island()
        self.island.set_hexpand(True)
        self.toolbar = Toolbar()
        self.island.append(self.toolbar)
        self.toggle = self.button('sidebar-show-symbolic', 'Toggle sidebar', self._toggle_sidebar)
        self.toolbar.append(self.toggle)
        navigation = ConnectedButtonGroup()
        self.back = self.button('go-previous-symbolic', 'Back', lambda: self.send('nav:back'))
        self.forward = self.button('go-next-symbolic', 'Forward', lambda: self.send('nav:forward'))
        navigation.append(self.back)
        navigation.append(self.forward)
        self.toolbar.append(navigation)
        self.reload = self.button('view-refresh-symbolic', 'Reload', lambda: self.send('nav:reload'))
        self.toolbar.append(self.reload)
        self.address = Gtk.Entry(hexpand=True, placeholder_text='Search or enter address')
        self.address.set_icon_sensitive(Gtk.EntryIconPosition.PRIMARY, False)
        self.address.set_max_width_chars(70)
        self.address.connect('activate', self._navigate)
        focus = Gtk.EventControllerFocus()
        focus.connect('enter', self._address_focus)
        self.address.add_controller(focus)
        self.address_field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.address_field.add_css_class('viola-address')
        self.address_field.append(self.address)
        self.bookmark = self.button('non-starred-symbolic', 'Bookmark this page', lambda: self.send('tab:bookmark'))
        self.address_field.append(self.bookmark)
        address_clamp = Adw.Clamp(maximum_size=720, tightening_threshold=720, hexpand=True,
                                 child=self.address_field)
        self.toolbar.append(address_clamp)
        self.new_tab = self.button('luma-plus-symbolic', 'New tab', self._new_tab)
        self.toolbar.append(self.new_tab)
        self.menu_button = self.button('view-more-horizontal-symbolic', 'Browser menu', self._browser_menu)
        self.toolbar.append(self.menu_button)
        self.page = GpuPage()
        self.island.append(self.page)
        composition.append(self.island)
        from resizable_sidebar import ResizableSidebar
        self.sidebar_layout = ResizableSidebar(self, composition, fixed_sidebar, sidebar_bin)
        self.set_body(self.sidebar_layout.overlay)
        self.toolbar.set_sensitive(False)
        # Capture host commands before the page's DOM/IME event controllers.
        # Actions and shortcut spellings still come from the AppKit registry.
        from luma_appkit.menus import accelerator
        shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        shortcuts.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        for group in commands.groups:
            for command in group.commands:
                if command.shortcut:
                    shortcuts.add_shortcut(Gtk.Shortcut.new(
                        Gtk.ShortcutTrigger.parse_string(accelerator(command.shortcut)),
                        Gtk.CallbackAction.new(lambda _widget, _args, cid=command.id:
                                               commands.invoke(cid))))
        self.add_controller(shortcuts)
        from native_zoom_shortcuts import install_zoom_shortcuts
        self.zoom_keys = install_zoom_shortcuts(self)
        address_keys = Gtk.EventControllerKey()
        address_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        address_keys.connect('key-pressed', self._address_key)
        self.address.add_controller(address_keys)
        from native_address import NativeAddress
        self.address_suggestions = NativeAddress(self)
        from responsive_chrome import ResponsiveChrome
        self.responsive = ResponsiveChrome(self)
        if mini:
            from native_mini import NativeMini
            self.mini_presentation = NativeMini(self)

    def close_requested(self, *_):
        if self.on_native_close:
            self.on_native_close()
        if hasattr(self, 'window_manager'):
            self.window_manager.close_window(self)
        else:
            self.loop.quit()
        return True

    def _restore_tab(self):
        if self.mini:
            return
        if hasattr(self, 'window_manager'):
            self.window_manager.command(self, 'Reopen closed tab')

    def _new_mini(self):
        if hasattr(self, "window_manager"):
            self.window_manager.command(self, "New Mini Viola")

    def _new_window(self):
        if hasattr(self, 'window_manager'):
            self.window_manager.command(self, 'New window')

    def _new_private_window(self):
        if hasattr(self, 'window_manager'):
            self.window_manager.command(self, 'New private window')

    def button(self, icon, label, action):
        button = IconButton(icon, label, context=self.context, quiet=True)
        button.connect('clicked', lambda *_: action())
        return button

    def send(self, channel, payload=None):
        if self.services:
            self.submit(lambda: self.services.send('sidebar', channel, payload))

    def _toggle_sidebar(self):
        if self.phone:
            self.responsive.show_tabs()
            return
        if not self.mini:
            self.sidebar_layout.toggle()

    def address_focused(self):
        focus = self.get_focus()
        return bool(focus and (focus is self.address or focus.is_ancestor(self.address)))

    def _address_focus(self, *_):
        if hasattr(self, 'address_suggestions') and self.address_suggestions.restoring_focus:
            return
        if self.state and not self.new_tab_pending:
            url = self.state.get('activeUrl', '')
            self.address.set_text('' if url == 'about:blank' else url)
        self.address.select_region(0, -1)
        if hasattr(self, "address_suggestions"):
            self.address_suggestions.changed()

    def _focus_address(self):
        if self.phone:
            self.responsive.focus_address()
            return
        self.new_tab_pending = False
        self.address.grab_focus()
        self._address_focus()

    def _close_tab(self):
        if getattr(self, 'developer_tools', False):
            self.close()
            return
        if self.mini:
            self.close()
            return
        if self.state and self.state.get('activeTabId'):
            self.send('tab:close', {'tabId': self.state['activeTabId']})

    def _address_key(self, _controller, key, _code, _state):
        if key == Gdk.KEY_Down:
            return self.address_suggestions.enter_results()
        if key != Gdk.KEY_Escape:
            return False
        self.address_suggestions.close()
        self.new_tab_pending = False
        self.page.grab_focus()
        if self.state:
            self.address.set_text('' if self.state.get('activeUrl') == 'about:blank'
                                  else self.state.get('nativePrettyUrl') or self.state.get('activeUrl', ''))
        return True

    def _new_tab(self):
        if getattr(self, "mini", False):
            self._new_window()
            return
        if not self.services:
            return
        self.submit(self.services.create_new_tab)

    def _edit_blank_tab(self):
        self.new_tab_pending = True
        self.address.set_text('')
        self.address.grab_focus()
        self.address_suggestions.changed()
        if self.phone:
            self.responsive.render(force=True)
            self.responsive.focus_address()

    def _navigate(self, *_):
        text = self.address.get_text()
        if not text.strip() or not self.services:
            return
        self.address_suggestions.close()
        self.new_tab_pending = False
        self.submit(lambda: self.services.navigate(text))
        self.page.grab_focus()

    def _activate_tab(self, _list, row):
        if row:
            self.send('tab:activate', {'tabId': row.tab_id})

    def _browser_menu(self):
        anchor = {'anchorRect': {'x': 10, 'y': 10, 'width': 28, 'height': 28}}
        self.submit(lambda: self.services.open_menu('browser:menu', anchor),
                    lambda model: self.show_menu(self.menu_button, model))

    def open_retained_menu(self, parent, channel, payload, x=None, y=None):
        if not self.services:
            return
        def present(model):
            menu = self.show_menu(parent, model, x, y, hide_root_title=channel == 'tab:menu')
            if not menu or channel not in ('tabs:menu', 'space:menu') or not payload.get('spaceId'):
                return
            original = menu._activate
            def activate(nonce, path):
                row = next((row for row in model['items'] if path == [row['index']]), None)
                label = row['label'] if row and row['enabled'] and row['visible'] else None
                if label in ('Settings…', 'Workspace settings…', 'Rename workspace…', 'New workspace…', 'Delete workspace…'):
                    self.submit(lambda: self.services.dismiss_menu(nonce))
                    if label == 'Delete workspace…':
                        from workspace_editor import confirm_delete
                        confirm_delete(self, payload['spaceId'])
                    elif label == 'New workspace…':
                        self.sidebar.create_workspace()
                    else:
                        self.sidebar.edit_workspaces()
                else:
                    original(nonce, path)
            menu._activate = activate
        self.submit(lambda: self.services.open_menu(channel, payload), present)

    def show_menu(self, parent, description, x=None, y=None, local_activate=None, position=Gtk.PositionType.BOTTOM, hide_root_title=False):
        if self.open_popover:
            self.open_popover.popdown()
        menu = NativeMenu(description, local_activate or (lambda nonce, path: self.submit(
            lambda: self.services.activate_menu(nonce, path))),
            repeat=self.repeat_menu_action, context=self.context,
            prefer_submenu_left=parent is self.menu_button, hide_root_title=hide_root_title)
        self.open_popover = menu
        # A toolbar-owned popup inherits the kit's descendant button rules,
        # which override native menu rows. Anchor geometrically at the source
        # control while the window owns the popup's widget hierarchy.
        valid, bounds = parent.compute_bounds(self)
        if not valid:
            self.open_popover = None
            if not local_activate:
                self.submit(lambda: self.services.dismiss_menu(description['nonce']))
            return
        menu.set_parent(self)
        menu.set_position(position)
        menu.set_halign(Gtk.Align.START if description.get('role') == 'select' else Gtk.Align.END)
        point = Gdk.Rectangle()
        point.x = int(bounds.get_x() + (x if x is not None else 0))
        point.y = int(bounds.get_y() + (y if y is not None else 0))
        point.width = 1 if x is not None else int(bounds.get_width())
        point.height = 1 if y is not None else int(bounds.get_height())
        menu.set_pointing_to(point)
        def closed(popup):
            if not popup.invoked and not local_activate:
                self.submit(lambda: self.services.dismiss_menu(popup.nonce))
            if self.open_popover is popup:
                self.open_popover = None
            GLib.idle_add(lambda: popup.unparent() or False)
        menu.connect('closed', closed)
        menu.popup()
        return menu

    def repeat_menu_action(self, popup, path):
        if self.open_popover is not popup or not popup.get_sensitive():
            return
        self.zoom_repeat_calls = getattr(self, 'zoom_repeat_calls', 0) + 1
        popup.set_sensitive(False)
        nonce = popup.nonce
        def apply_and_refresh():
            self.services.activate_menu(nonce, list(path))
            return self.services.open_menu('browser:menu',
                {'anchorRect': {'x': 10, 'y': 10, 'width': 28, 'height': 28}})
        def refreshed(description):
            if self.open_popover is not popup or not popup.get_mapped():
                self.submit(lambda: self.services.dismiss_menu(description['nonce']))
                return
            popup.reset(description)
            popup.set_sensitive(True)
            for _, widget in popup._placed:
                for button in getattr(widget, 'buttons', ()):
                    if getattr(button, 'retained_path', None) == path:
                        button.grab_focus()
        self.submit(apply_and_refresh, refreshed)

    def native_menu(self, description):
        if description.get('dismissed'):
            if self.open_popover and self.open_popover.nonce == description.get('nonce'):
                self.open_popover.popdown()
            return GLib.SOURCE_REMOVE
        if description.get('surface') != 'page':
            return GLib.SOURCE_REMOVE
        if not self.get_mapped():
            # Adapter qualification constructs and activates its own native
            # menu without asking GTK to map a popover on a hidden window.
            return GLib.SOURCE_REMOVE
        if (description.get('target_id') != self.page_input.target
                or not self.page_input.valid_identity()):
            self.submit(lambda: self.services.dismiss_menu(description['nonce']))
            return GLib.SOURCE_REMOVE
        self.page_input.release_pointer_buttons()
        x, y = self.page_input.pointer
        if description.get('role') == 'select' and description.get('anchor'):
            # ExternalPopupMenu supplies root-view DIPs, already independent
            # of the emulated device pixel scale used by the frame transport.
            x = description['anchor']['x']
            y = description['anchor']['y']
        self.show_menu(self.page, description, x, y)
        return GLib.SOURCE_REMOVE

    def receive_page_frame(self, texture, metadata):
        # Service state and GPU frames use independent channels. Keep one
        # pending texture when its state notification has not arrived yet.
        self.pending_page_frame = (texture, metadata)
        self.present_selected_frame()

    def present_selected_frame(self):
        active = self.state.get('activeTabId') if self.state else None
        if self.pending_page_frame and self.pending_page_frame[1].get('tab_id') == active:
            self.page.set_frame(*self.pending_page_frame)
        elif self.page.frame and self.page.frame.get('tab_id') != active:
            self.page.clear()

    def render_state(self, state):
        if self.state and self.open_popover and any(
                self.state.get(key) != state.get(key) for key in ('activeTabId', 'activeUrl')):
            self.open_popover.popdown()
        if self.state and self.state.get('activeTabId') != state.get('activeTabId'):
            self.address_suggestions.close()
            self.new_tab_pending = False
            self.address.set_text('' if state.get('activeUrl') == 'about:blank'
                                  else state.get('nativePrettyUrl') or state.get('activeUrl', ''))
        blank_selected = (state.get('activeUrl') == 'about:blank' and
                          (not self.state or self.state.get('activeTabId') != state.get('activeTabId')))
        self.state = state
        if blank_selected:
            self._edit_blank_tab()
        self.present_selected_frame()
        self.toolbar.set_sensitive(True)
        self.back.set_sensitive(state.get('canGoBack', False))
        self.forward.set_sensitive(state.get('canGoForward', False))
        if not self.address_focused() and not self.address_suggestions.owns_focus() and not self.new_tab_pending:
            self.address.set_text(state.get('nativePrettyUrl') or state.get('activeUrl', ''))
        self.address_suggestions.refresh_icon()
        self.bookmark.get_child().set_from_icon_name('starred-symbolic' if state.get('bookmarked') else 'non-starred-symbolic')
        if self.mini:
            self.mini_presentation.render(state)
        else:
            self.sidebar.render(state)
        self.responsive.render()
        return GLib.SOURCE_REMOVE

    def native_event(self, name, payload):
        if name == 'media:preview':
            if 'nativePip' in payload:
                if payload['nativePip'] and not self.pip_window:
                    from native_pip import NativePip
                    self.pip_window = NativePip(self, payload['tabId'])
                    media = self.native_context['media']
                    self.pip_window.update(media.paintable, media.tab_id)
                    self.pip_window.present()
                elif not payload['nativePip'] and self.pip_window:
                    self.pip_window.dismiss()
            else:
                self.sidebar.footer.preview(payload)
        elif self.state:
            state = dict(self.state)
            if name == 'download:update' and isinstance(payload, dict):
                for source, destination in (('notifications', 'notifications'), ('downloads', 'downloads'), ('summary', 'downloadSummary')):
                    if source in payload:
                        state[destination] = payload[source]
            elif name == 'permission:prompt':
                state['permissionPrompt'] = payload
            self.render_state(state)
        return GLib.SOURCE_REMOVE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, help='Persistent profile for an explicitly installed interactive host')
    parser.add_argument('urls', nargs='*')
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--qa-present', action='store_true')
    parser.add_argument('--interactive', action='store_true', help='User-requested isolated test session without a timeout')
    parser.add_argument('--start-url', default=None)
    parser.add_argument('--qualify-manual-identity', action='store_true')
    parser.add_argument('--qualify-controls', action='store_true')
    parser.add_argument('--representative', action='store_true')
    parser.add_argument('--media-fixture', type=Path)
    parser.add_argument('--qualify-input', action='store_true')
    parser.add_argument('--qualify-page-menu', action='store_true')
    parser.add_argument('--capture-widget', action='store_true')
    parser.add_argument('--measure-presentation', action='store_true')
    parser.add_argument('--seconds', type=int, default=10)
    args = parser.parse_args()
    if args.interactive:
        if args.representative or args.qualify_controls or args.qualify_input or args.qualify_page_menu:
            parser.error('--interactive cannot run automated fixtures')
        args.qa_present = True
    if args.profile and not args.interactive:
        parser.error('--profile requires --interactive')
    had_profile = bool(args.profile and args.profile.exists() and any(args.profile.iterdir()))
    args.work_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='native-window-', dir=args.work_root))
    engine_digest = hashlib.sha256()
    with args.engine.open('rb') as executable:
        for chunk in iter(lambda: executable.read(1024 * 1024), b''):
            engine_digest.update(chunk)
    source_root = Path(__file__).resolve().parent
    provenance = {'engine': str(args.engine.resolve()), 'engine_sha256': engine_digest.hexdigest(),
                  'host_sources': {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in sorted(source_root.rglob('*'))
                                   if path.is_file() and path.suffix in ('.py', '.css')}}
    (run / 'runtime-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    # Register actual packaged artwork in this process's isolated data root.
    # Installed sessions use their actual desktop entry and persistent XDG roots;
    # disposable QA keeps its isolated identity artwork.
    data = run / 'data'
    applications = data / 'applications'
    applications.mkdir(parents=True)
    icon = Path(__file__).resolve().parent / 'assets/org.projectluma.Viola.NativeIntegration.svg'
    if not icon.is_file():
        raise RuntimeError('The existing Luma browser artwork is required')
    (applications / (APP_ID + '.desktop')).write_text(
        '[Desktop Entry]\nType=Application\nName=Viola\nIcon=' + str(icon) + '\nExec=false\nNoDisplay=true\n')
    if not args.profile:
        os.environ['XDG_DATA_HOME'] = str(data)
        os.environ['XDG_STATE_HOME'] = str(run / 'state')
    application = Adw.Application(application_id=APP_ID, flags=(Gio.ApplicationFlags.HANDLES_OPEN if args.profile else Gio.ApplicationFlags.NON_UNIQUE))
    application.register(None)
    if application.get_is_remote():
        # Let GApplication forward activation/open and unregister cleanly.
        application.run([APP_ID, *args.urls])
        return
    install_appkit()
    # Keep layout and rasterization unhinted together. GTK's automatic path
    # combines unhinted metrics with hinted outlines on lower-DPI monitors,
    # which can clip glyph tops (upstream GTK #7400). This is process-local;
    # AppKit still supplies the font, size, weight, colors and widget geometry.
    font_settings = Gtk.Settings.get_default()
    font_settings.set_property('gtk-font-rendering', Gtk.FontRendering.MANUAL)
    font_settings.set_property('gtk-hint-font-metrics', False)
    font_settings.set_property('gtk-xft-hinting', 0)
    appearance_before = Adw.StyleManager.get_default().get_dark()
    if os.environ.get('VIOLA_QA_APPEARANCE') == 'dark':
        if (not os.environ.get('WAYLAND_DISPLAY', '').startswith('viola-qa-')
                or 'viola-qa-bus-' not in os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')):
            raise RuntimeError('Explicit QA appearance requires the owned private compositor')
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
        str(Path(__file__).resolve().parent / 'assets/icons'))
    loop = GLib.MainLoop()
    errors = []
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='viola-services')
    input_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='viola-input-writes')
    def error(message):
        errors.append(str(message))
        loop.quit()
        return GLib.SOURCE_REMOVE
    from command_timeout import CommandTimeoutPolicy
    def timeout_notice():
        parent = windows.active_window()
        if parent:
            dialog = Adw.AlertDialog(
                heading='The browser is taking too long',
                body='An operation has not responded yet. Your windows remain open. Check the result before trying again.')
            dialog.add_response('close', 'OK')
            dialog.set_default_response('close')
            dialog.set_close_response('close')
            dialog.present(parent)
    def record_timeout(item):
        print('VIOLA_COMMAND_TIMEOUT ' + json.dumps(item), flush=True)
        path = args.work_root / 'command-timeouts.json'
        temporary = path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(timeout_policy.snapshot(), indent=2) + '\n')
            temporary.replace(path)
        except OSError:
            # A full/unwritable diagnostics directory must not suppress recovery.
            print('VIOLA_COMMAND_TIMEOUT_REPORT_UNAVAILABLE', flush=True)
    timeout_policy = CommandTimeoutPolicy(
        lambda: engine.process.poll() is None, timeout_notice, record_timeout)
    def command_failed(exception):
        if timeout_policy.handle(exception):
            return GLib.SOURCE_REMOVE
        return error(str(exception))
    def submit(work, done=None):
        future = pool.submit(work)
        def complete(result):
            try:
                value = result.result()
                if done:
                    def deliver():
                        try:
                            done(value)
                        except Exception as exception:
                            error(str(exception))
                        return GLib.SOURCE_REMOVE
                    GLib.idle_add(deliver)
            except Exception as exception:
                if not args.interactive:
                    import traceback
                    traceback.print_exception(exception)
                GLib.idle_add(command_failed, exception)
        future.add_done_callback(complete)
        return future
    window = IntegratedWindow(application, loop, submit, errors)
    from native_windows import NativeWindows
    def preview_ready(owner):
        if owner.sidebar.footer.has_preview:
            (args.work_root / 'native-media-ready').write_text('ready\n')
    windows = NativeWindows(application, loop, submit, input_pool.submit, error,
        lambda mini=False: IntegratedWindow(application, loop, submit, errors, mini=mini), window,
        present=args.qa_present, manual=args.interactive or args.qualify_manual_identity,
        preview_ready=preview_ready)
    from native_clipboard import publish_offer
    receiver = FrameReceiver(windows.frame, error, windows.cursor, publish_offer,
                             windows.announced, windows.backend_closed)
    windows.frame_receiver = receiver
    engine = EnginePipe(args.engine, args.profile or run / 'profile', windows.event,
                        native_frame_probe=True, frame_socket=receiver.path, native_menu_probe=True, native_media_probe=True,
                        manual_browsing=args.interactive or args.qualify_manual_identity, reuse_profile=bool(args.profile),
                        on_closed=lambda: GLib.idle_add(lambda: loop.quit() or False))
    windows.engine = engine
    from native_clipboard import EngineClipboard
    clipboard_bridge = EngineClipboard(engine.display)
    services = window.services
    native_media = windows.primary['media']
    page_input = window.page_input
    receiver.start(engine.process.pid)
    if args.media_fixture:
        if not args.media_fixture.is_file() or args.media_fixture.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Media fixture must be an existing bounded test asset')
        Fixture.video_path = args.media_fixture
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    layout_report = {}
    def connect():
        if os.environ.get('VIOLA_QA_INVENTORY') == '1':
            requests = [engine.request('Target.createTarget', {'url': 'about:blank',
                'newWindow': True, 'background': False, 'windowState': 'normal'}) for _ in range(2)]
            for request in requests: request.result(timeout=15)
        else:
            engine.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True,
                                               'background': False, 'windowState': 'normal'})
        windows.wait_primary()
        windows.connect(windows.primary)
        origin = 'http://127.0.0.1:' + str(server.server_port)
        if args.start_url or not had_profile:
            services.navigate(args.start_url or ("https://www.google.com/" if args.interactive else origin + ('/cadence' if args.measure_presentation else '/native-window')))
        for url in args.urls:
            engine.open_external(url)
        if args.representative:
            from seed_native_layout import seed
            layout_report.update(seed(services, origin, run / 'fixture-downloads', media=bool(args.media_fixture)))
    def connected(_):
        from native_identity_menu import NativeIdentityMenu
        def install_identity(description):
            window.identity_menu = NativeIdentityMenu(window, description)
            if args.qa_present:
                window.present()
                window._focus_address()
            timeout_policy.ready = True
            (args.work_root / 'native-ready').write_text('ready\n')
            if os.environ.get('VIOLA_QA_WINDOWS') == '1':
                from qualify_native_windows import WindowQualification
                if os.environ.get('VIOLA_QA_DEVTOOLS') == '1':
                    from qualify_devtools import WindowQualification
                if os.environ.get('VIOLA_QA_WINDOW_FOCUS') == '1':
                    from qualify_window_focus import WindowQualification
                if os.environ.get("VIOLA_QA_MINI") == "1":
                    from qualify_mini import MiniQualification as WindowQualification
                if os.environ.get("VIOLA_QA_INVENTORY") == "1":
                    from qualify_tab_inventory import TabInventoryQualification as WindowQualification
                if os.environ.get("VIOLA_QA_CLOSE_BURST") == "1":
                    from qualify_close_burst import CloseBurstQualification as WindowQualification
                window.windows_qualification = WindowQualification(windows,
                    layout_report.setdefault('windows_qualification', {}), loop.quit)
            if args.representative:
                if args.media_fixture:
                    from qualify_media_stats import sample
                    def media_sample():
                        submit(lambda: dict(source=sample(engine, layout_report['media_target']),
                            bridge=services.evaluate('sidebar', "({native:typeof __violaNativeMediaHealthy,answer:String(window.__velaPreviewAnswer).slice(0,300),url:location.href})")),
                               lambda result: layout_report.setdefault('media_samples', []).append(result))
                        return False
                    GLib.timeout_add_seconds(4, media_sample)
                    GLib.timeout_add_seconds(7, media_sample)
                if args.media_fixture and os.environ.get('VIOLA_QA_MEDIA_CHECKS') == '1':
                    from qualify_native_media import MediaQualification
                    window.media_qualification = MediaQualification(window, native_media, services,
                        submit, layout_report, loop.quit)
                else:
                    GLib.timeout_add_seconds(8, lambda: loop.quit() or False)
        submit(lambda: services.open_menu('app:menu', {'anchorRect': {'x': 0, 'y': 0, 'width': 28, 'height': 28}}), install_identity)
    def activate_app(*_):
        active = windows.active_window()
        if active:
            active.present()
    def open_urls(_app, files, _count, _hint):
        active = windows.active_window()
        if active:
            for file in files:
                active.submit(lambda uri=file.get_uri(): engine.open_external(uri))
            active.present()
    application.connect('activate', activate_app)
    application.connect('open', open_urls)
    submit(connect, connected)
    input_report = {}
    if args.qualify_input:
        from qualify_page_input import InputQualification
        def qualified(result):
            input_report.update(result)
            loop.quit()
        qualification = InputQualification(window, page_input, engine, services, submit, qualified,
                                             page_menu=args.qualify_page_menu)
    responsive_report = {}
    if os.environ.get("VIOLA_QA_RESPONSIVE") == "1":
        from qualify_responsive import ResponsiveQualification
        def responsive_done(result):
            responsive_report.update(result)
            loop.quit()
        responsive_qualification = ResponsiveQualification(window, run, responsive_done)
    controls_report = {}
    if args.qualify_controls:
        from qualify_native_controls import ControlQualification
        def controls_done(result):
            controls_report.update(result)
            GLib.timeout_add_seconds(6, lambda: loop.quit() or False)
        controls_qualification = ControlQualification(window, controls_done)
        controls_report = controls_qualification.report
    if args.capture_widget:
        def capture():
            try:
                from capture_native_widget import capture as capture_widget
                capture_widget(window, run / 'native-widget.png')
            except Exception as exception:
                error(str(exception))
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(5, capture)
    if not args.interactive:
        GLib.timeout_add_seconds(max(1, min(args.seconds, 3600)), lambda: loop.quit() or False)
    def presentation_diagnostics():
        active_window = windows.active_window()
        if active_window is None:
            return GLib.SOURCE_CONTINUE
        now = time.monotonic()
        samples = [sample for sample in active_window.page.presentation_samples if sample[0] >= now - 5]
        distinct = []
        for sample in samples:
            if not distinct or sample[1] != distinct[-1][1]:
                distinct.append(sample)
        summary = dict(sample_seconds=5, distinct_presentations=len(distinct),
            distinct_presentations_per_second=len(distinct)/5,
            note='Idle, occluded, or locked windows naturally stop repainting.',
            window_count=len(windows.contexts), window_active=active_window.is_active(),
            frame_size=active_window.page.frame_size_diagnostic,
            imported_frames=receiver.imported, pending_leases=len(receiver.pending),
            input_pending=active_window.page_input.pending,
            input_metrics={key: active_window.page_input.metrics.get(key, 0) for key in (
                'sent', 'identity_rejections', 'coalesced', 'pointer_backpressure',
                'scroll_events', 'inflight_max', 'queue_wait_ms_max', 'ack_ms_max',
                'ack_timeouts')})
        summary['transport_metrics'] = dict(engine.transport_metrics)
        temporary = args.work_root / 'presentation-live.tmp'
        temporary.write_text(json.dumps(summary) + '\n')
        temporary.replace(args.work_root / 'presentation-live.json')
        return GLib.SOURCE_CONTINUE
    diagnostics_timer = GLib.timeout_add_seconds(2, presentation_diagnostics) if args.interactive else None
    stop_source = GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: loop.quit() or False)
    try:
        loop.run()
    finally:
        if diagnostics_timer:
            GLib.source_remove(diagnostics_timer)
        receiver.stop()
        def wide_widgets(widget):
            found = []
            minimum = widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
            if minimum > 230:
                found.append({'type': widget.__gtype__.name, 'classes': widget.get_css_classes(), 'minimum': minimum})
            child = widget.get_first_child()
            while child:
                found.extend(wide_widgets(child))
                child = child.get_next_sibling()
            return found
        geometry = {'wide_footer_widgets': wide_widgets(window.sidebar.footer), 'window': [window.get_width(), window.get_height()],
                    'sidebar_width': window.sidebar.get_width(),
                    'native_dark_before_fixture': appearance_before,
                    'native_dark': Adw.StyleManager.get_default().get_dark(),
                    'sidebar_font': window.sidebar.switcher_label.get_pango_context().get_font_description().to_string(),
                    'media_preview': window.sidebar.footer.has_preview,
                    'icon_theme': Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).get_theme_name(),
                    'titlebar_height': window.title_bar.get_allocated_height(),
                    'toolbar_height': window.toolbar.get_allocated_height(),
                    'page': [window.page.get_width(), window.page.get_height()]}
        window_active = window.is_active()
        windows.shutdown()
        native_media.close()
        window.favicons.close()
        window.address_suggestions.close()
        window.pending_page_frame = None
        window.page.clear()
        window.destroy()
        gc.collect()
        input_pool.shutdown(wait=True)
        pool.shutdown(wait=True)
        services.close()
        report = {'classification': 'native integration, not visual or interaction acceptance',
                  'presented': args.qa_present, 'window_active': window_active,
                  'runtime_provenance': str(run / 'runtime-provenance.json'),
                  'presentation_samples': list(window.page.presentation_samples),
                  'frame_size_diagnostic': window.page.frame_size_diagnostic,
                  'snapshot_count': window.page.snapshot_count,
                  'rejected_sizes': window.page.rejected_sizes,
                  'imported_frames': receiver.imported,
                  'wayland_display': os.environ.get('WAYLAND_DISPLAY'),
                  'accessibility_mode': os.environ.get('GTK_A11Y', 'auto'),
                  'toolkit_resource_probe': os.environ.get('G_RESOURCE_OVERLAYS'),
                  'released_frames': receiver.released, 'pending_leases': len(receiver.pending),
                  'live_tab_rows': len(window.rows), 'errors': errors,
                  'command_timeouts': timeout_policy.snapshot(),
                  'input_metrics': page_input.metrics,
                  'input_qualification': input_report,
                  'controls_qualification': controls_report,
                  'responsive_qualification': responsive_report,
                  'layout_fixture': layout_report,
                  'native_media': native_media.metrics,
                  'allocated_geometry': geometry,
                  'identity_desktop_entry': str(applications / (APP_ID + '.desktop')),
                  'remaining': ['page input and accessibility', 'complete sidebar composition',
                                'all popup families', 'visual parity']}
        try:
            receiver.close()
        except Exception as exception:
            errors.append(str(exception))
        clipboard_bridge.close()
        engine.close()
        report['engine_exit_code'] = engine.process.returncode
        if report['engine_exit_code'] != 0:
            errors.append('Viola engine exited abnormally (code ' +
                          str(report['engine_exit_code']) + '). Your profile is preserved.')
        if os.environ.get('VIOLA_QA_WINDOWS') == '1':
            protocol = (run / 'profile.log').read_text(errors='replace')
            layout_report['windows_qualification']['engine_toplevel_requests'] = sum(
                '.get_toplevel(' in line for line in protocol.splitlines())
            layout_report['windows_qualification']['separate_engine_display'] = bool(
                engine.display and engine.display.display != os.environ.get('WAYLAND_DISPLAY'))
        server.shutdown()
        server.server_close()
    (run / 'integration-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    media_checks = layout_report.get('media_qualification', {}).get('checks', [])
    if os.environ.get('VIOLA_QA_WINDOWS') == '1' and (
            not layout_report.get('windows_qualification', {}).get('completed') or
            layout_report.get('windows_qualification', {}).get('error') or
            not layout_report.get('windows_qualification', {}).get('separate_engine_display') or
            len(layout_report.get('windows_qualification', {}).get('checks', [])) != (8 if os.environ.get('VIOLA_QA_DEVTOOLS') == '1' else 12 if os.environ.get('VIOLA_QA_WINDOW_FOCUS') == '1' else 11 if os.environ.get('VIOLA_QA_MINI') == '1' else 4 if os.environ.get('VIOLA_QA_CLOSE_BURST') == '1' else 12 if os.environ.get('VIOLA_QA_INVENTORY') == '1' else 2 if os.environ.get('VIOLA_QA_POPUP_ONLY') == '1' else 19 if os.environ.get('VIOLA_QA_POPUP') == '1' else 27 if os.environ.get('VIOLA_QA_REPEAT_CLOSE') == '1' else 17)):
        raise SystemExit(1)
    if (os.environ.get('VIOLA_QA_MEDIA_CHECKS') == '1' and (len(media_checks) != (5 if os.environ.get('VIOLA_QA_PIP') == '1' else 3) or not all(all(check.values()) for check in media_checks))):
        raise SystemExit(1)
    if os.environ.get("VIOLA_QA_RESPONSIVE") == "1" and (not responsive_report.get("completed") or responsive_report.get("error") or len(responsive_report.get("checks", [])) != 5):
        raise SystemExit(1)
    if errors or (args.media_fixture and (not native_media.metrics['frames'] or not geometry['media_preview'])) or (args.qualify_controls and (not controls_report.get('completed') or controls_report.get('error'))) or (args.qualify_input and (not input_report or input_report.get('error')
            or not all(input_report.get(key) for key in (
                'pointer_focus', 'gtk_ime_commit', 'stale_frame_rejected', 'previous_tab_unchanged')))):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
