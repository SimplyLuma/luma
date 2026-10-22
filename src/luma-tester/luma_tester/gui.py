# SPDX-License-Identifier: Apache-2.0
"""LumaUI simulator workspace with shared navigation and a native guest display."""
import os
import json
from pathlib import Path
import platform
import subprocess
import sys
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

# The finished interface requires the real shared toolkit. No stock GTK fallback.
try:
    from luma_appkit import Command, CommandGroup, CommandRegistry
    from luma_appkit import icons
    from luma_appkit.content_cards import Card
    from luma_appkit.content_type import apply_type
    from luma_appkit.rows_navigation import SidebarRow, SidebarSection, RowLead, sidebar_width
    from luma_appkit.menus import Menu
    from luma_appkit.action_toast import Toast, ToastHost
    from luma_appkit.action_center import ActionCenter, BarAction, BarWidget, SEPARATOR, make_control
    from luma_appkit.structure_layers import LayerHost
    from luma_appkit.widgets import AppWindow, IconButton, Island, IslandOverlaySplitView, NavigationSidebar, ScrollView, add_style_sheet
except (ImportError, ValueError) as exc:
    raise ImportError('Luma Tester requires the LumaUI runtime (luma-developer-platform).') from exc

from .core import Session, TesterError, data_root, read_manifest
from .display import GuestDisplay
from . import presets


def launch(state=None):
    GLib.set_prgname('io.luma.Tester')
    GLib.set_application_name('Luma Tester')
    Gtk.Window.set_default_icon_name('io.luma.Tester')
    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.luma.Tester')
            self.session = None
            self.child = None
            self.log = None
            self.busy = False
            self.intent = 'try'
            self.target = 'mobile'
            self.project = None
            self.preferences = data_root(state) / 'preferences.json'
            self.preset_path = data_root(state) / 'presets.json'
            self.saved_presets = presets.load(self.preset_path)
            try:
                paths = json.loads(self.preferences.read_text()).get('images', [])
                self.imported = [Path(p) for p in paths if isinstance(p, str)]
            except (OSError, ValueError, AttributeError):
                self.imported = []
            self.catalog_targets = set()
            for path in self.available_manifests():
                try:
                    self.catalog_targets.add(read_manifest(path)['target'])
                except (OSError, TesterError, ValueError, TypeError):
                    continue
            if 'desktop' in self.catalog_targets:
                self.target = 'desktop'
            self.advanced = None
            self.viewer = None
            self.graphical_ready = False
            self.display_failure = None
            self.shutting_down = False
            self.home_busy = False
            self.selected_images = {}

        def label(self, text, role='body', muted=False, centered=False):
            widget = Gtk.Label(label=text, wrap=True, xalign=0.5 if centered else 0)
            apply_type(widget, role, muted=muted)
            return widget

        def button(self, text, callback, primary=False):
            button = Gtk.Button(label=text)
            button.connect('clicked', lambda _button: callback())
            if primary:
                button.add_css_class('suggested-action')
            return button

        def fit_selector(self, selector):
            factory = Gtk.SignalListItemFactory()
            factory.connect('setup', lambda _factory, item: item.set_child(
                Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)))
            def bind(_factory, item):
                text = item.get_item().get_string()
                item.get_child().set_label(text)
                item.get_child().set_tooltip_text(text)
            factory.connect('bind', bind)
            selector.set_factory(factory)

        def do_activate(self):
            if self.get_active_window():
                self.get_active_window().present()
                return
            icons.ensure_icons(Gdk.Display.get_default())
            self.commands = CommandRegistry((CommandGroup(None, (
                Command('tester.try', 'Try Luma', lambda: self.select_intent('try'), icons.icon_name('play')),
                Command('tester.develop', 'Develop for Luma', lambda: self.select_intent('develop'), icons.icon_name('code')),
                Command('tester.back', 'Back', lambda: self.stack.set_visible_child_name('welcome'), icons.icon_name('chevron-left'), shortcut=('Alt', 'Left')),
                Command('tester.open', 'Open Luma', self.start, icons.icon_name('play'), enabled=lambda: not self.active()),
                Command('tester.advanced', 'Advanced…', self.show_advanced, icons.icon_name('settings')),
                Command('tester.pause', 'Pause or resume', self.pause, icons.icon_name('pause'), enabled=lambda: self.running()),
                Command('tester.shutdown', 'Shut down', self.stop, icons.icon_name('power'), enabled=lambda: self.running()),
                Command('tester.quit', 'Quit', self.window_close, icons.icon_name('log-out'), shortcut=('Ctrl', 'Q')),
            )),))
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(Path(__file__).parent))
            self.window = AppWindow(application=self, app_id='io.luma.Tester', title='Luma Tester',
                                    icon_name='io.luma.Tester', commands=self.commands,
                                    default_width=780, default_height=650,
                                    minimum_width=480, minimum_height=480)
            add_style_sheet(str(Path(__file__).with_name('tester.css')))
            self.island = Island()
            self.build_sidebar()
            self.content_host = ToastHost(self.island)
            self.layout = IslandOverlaySplitView(sidebar=self.sidebar, content=self.content_host,
                min_sidebar_width=sidebar_width('destinations', 'narrow'), max_sidebar_width=sidebar_width('destinations', 'narrow'), sidebar_width_unit=Adw.LengthUnit.PX)
            self.window.set_body(self.layout)
            self.last_workspace_size = None
            self.window.add_tick_callback(self.adapt_workspace)
            self.build_toolbar()
            self.stack = Gtk.Stack(vexpand=True, hexpand=True)
            self.island.append(self.stack)
            self.build_welcome()
            self.build_session()
            self.build_live()
            self.stack.connect('notify::visible-child-name', self.workspace_changed)
            self.stack.set_visible_child_name('welcome')
            self.window.connect('close-request', self.close_request)
            self.window.present()
            GLib.timeout_add_seconds(2, self.refresh)

        def build_sidebar(self):
            self.sidebar = NavigationSidebar(variant='destinations', width='narrow')
            self.sidebar.set_visible(False)
            self.sidebar.append_section('Experiences')
            self.device_rows = {}
            for target, title, icon in [('desktop', 'Desktop', 'monitor'), ('tablet', 'Tablet', 'smartphone'),
                                        ('mobile', 'Mobile', 'smartphone'), ('tv', 'TV', 'monitor'),
                                        ('smart-device', 'Smart Device', 'cpu')]:
                row = SidebarRow(title, lead=RowLead.icon(icon))
                row.target = target
                supported = target in ('desktop', 'mobile')
                row.set_sensitive(True)
                if not supported:
                    row.set_tooltip_text('A dedicated Luma image profile is required.')
                self.sidebar.append_row(row)
                self.device_rows[target] = row
            self.sidebar.list.connect('row-activated', self.device_activated)
            # Detailed selectors live in Advanced; the workspace exposes builds
            # through the toolkit menu and presets as destination rows.
            self.image_paths = []
            self.image_selector = Gtk.DropDown(model=Gtk.StringList.new([]), hexpand=True)
            self.fit_selector(self.image_selector)
            self.image_selector.connect('notify::selected', self.image_selected)
            self.preset_selector = Gtk.DropDown(model=Gtk.StringList.new(
                ['Choose a preset…'] + [item['name'] for item in self.saved_presets]))
            self.fit_selector(self.preset_selector)
            self.preset_selector.connect('notify::selected', self.preset_selected)
            self.preset_heading = SidebarSection('Presets', action=('plus', 'Save preset', self.name_preset))
            self.sidebar.list.append(self.preset_heading)
            self.preset_rows = []
            self.refresh_preset_rows()
            self.preset_handle = None
            form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=280)
            form.append(self.label('Save preset', 'group-heading'))
            self.preset_name = Gtk.Entry(placeholder_text='Preset name', max_length=80)
            self.preset_name.connect('activate', lambda _entry: self.save_preset())
            form.append(self.preset_name)
            actions = Gtk.Box(spacing=8, homogeneous=True)
            actions.append(self.button('Cancel', self.close_preset))
            actions.append(self.button('Save', self.save_preset, True))
            form.append(actions)
            self.preset_editor = Card(form)
            foot = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            foot.add_css_class('luma-navigation-list')
            for title, icon, callback in [('Add Image…', 'plus', self.choose_image),
                                           ('Advanced…', 'settings', self.show_advanced)]:
                row = SidebarRow(title, lead=RowLead.icon(icon))
                row.callback = callback
                foot.append(row)
            foot.connect('row-activated', lambda _list, row: row.callback())
            self.sidebar.append_footer(foot)

        def refresh_preset_rows(self):
            for row in self.preset_rows:
                self.sidebar.list.remove(row)
            self.preset_rows = []
            for index, item in enumerate(self.saved_presets, 1):
                row = SidebarRow(item['name'], lead=RowLead.icon('bookmark'))
                row.preset_index = index
                self.sidebar.list.append(row)
                self.preset_rows.append(row)
            if not self.saved_presets:
                row = SidebarRow('No presets', missing=True)
                row.set_sensitive(False)
                self.sidebar.list.append(row)
                self.preset_rows.append(row)

        def build_toolbar(self):
            self.toolbar = ActionCenter()
            self.toolbar.attach(self.content_host, safe_area=False)
            self.sidebar_toggle = make_control(BarAction('menu', tooltip='Show or hide sidebar',
                on_activate=lambda: self.layout.set_show_sidebar(not self.layout.get_show_sidebar())))
            self.workspace_title = self.label('Luma Desktop', 'label')
            self.workspace_title.set_wrap(False)
            self.build_menu = Gtk.MenuButton(label='Build', tooltip_text='Choose a build')
            self.build_menu.get_first_child().add_css_class('lumaui-bar-button')
            self.build_menu.get_first_child().add_css_class('text')
            self.run_button = make_control(BarAction('', 'Start', on_activate=self.start, primary=True))
            self.pause_button = make_control(BarAction('', 'Pause', on_activate=self.pause))
            self.shutdown_button = make_control(BarAction('', 'Shut Down', on_activate=self.stop))
            self.home_button = make_control(BarAction('', 'Home', on_activate=self.home))
            self.home_button.set_visible(False)
            self.tools_button = make_control(BarAction('', 'Project',
                on_activate=lambda: self.stack.set_visible_child_name('session')))
            self.tools_button.set_visible(False)
            self.toolbar.show_bar([BarWidget(self.sidebar_toggle), BarWidget(self.workspace_title),
                SEPARATOR, BarWidget(self.build_menu), BarWidget(self.home_button),
                BarWidget(self.run_button), BarWidget(self.pause_button), BarWidget(self.shutdown_button),
                BarWidget(self.tools_button)])
            self.toolbar.set_visible(False)

        def workspace_changed(self, *_args):
            shown = self.stack.get_visible_child_name() in ('session', 'live')
            self.toolbar.set_visible(shown)
            self.sidebar.set_visible(shown)
            self.last_workspace_size = None
            if shown:
                self.workspace_title.set_label('Luma ' + self.target.replace('-', ' ').title())
                self.sidebar.list.select_row(self.device_rows[self.target])
                self.update_images()

        def adapt_workspace(self, _widget, _clock):
            shown = self.stack.get_visible_child_name() in ('session', 'live')
            size = (self.window.get_width() < 900, shown)
            if size != self.last_workspace_size:
                self.last_workspace_size = size
                self.layout.set_collapsed(size[0])
                self.layout.set_show_sidebar(shown and not size[0])
            return True

        def device_activated(self, _list, row):
            if hasattr(row, 'preset_index'):
                self.preset_selector.set_selected(row.preset_index)
                return
            if not hasattr(row, 'target'):
                return
            if self.active():
                self.sidebar.list.select_row(self.device_rows[self.target])
                Toast.show(self.island, 'Shut down Luma to switch devices.', kind='warning')
                return
            self.select_target(row.target)
            self.session_title.set_label('Luma ' + self.target.replace('-', ' ').title())
            self.workspace_title.set_label('Luma ' + self.target.replace('-', ' ').title())
            self.status_label.set_label('Ready to start')
            self.stack.set_visible_child_name('session')
            self.update_images()

        def update_images(self):
            self.image_paths = []
            names = []
            for path in self.available_manifests():
                try:
                    manifest = read_manifest(path)
                    if manifest['target'] == self.target:
                        self.image_paths.append(path)
                        names.append(manifest.get('build') or manifest['id'])
                except (OSError, TesterError, ValueError, TypeError):
                    continue
            selected = self.select_image()
            self.updating_images = True
            self.image_selector.set_model(Gtk.StringList.new(names))
            if selected in self.image_paths:
                self.image_selector.set_selected(self.image_paths.index(selected))
            self.updating_images = False
            self.image_selector.set_sensitive(not self.active() and bool(names))
            self.update_build_menu(names)
            if not self.active():
                title = self.target.replace('-', ' ').title()
                self.status_label.set_label('Ready to start' if names else f'No {title} builds available yet.')
                self.developer_card.set_visible(self.intent == 'develop' and bool(names))
            self.refresh_preset_rows()

        def update_build_menu(self, names=None):
            if not hasattr(self, 'build_menu'):
                return
            if names is None:
                names = [read_manifest(path).get('build') or path.stem for path in self.image_paths]
            choices = tuple(Command('build.' + str(index), name,
                lambda selected=index: self.image_selector.set_selected(selected),
                checked=lambda path=path: self.select_image() == path,
                enabled=lambda: not self.active()) for index, (path, name) in enumerate(zip(self.image_paths, names)))
            registry = CommandRegistry((CommandGroup(None, choices), CommandGroup(None, (
                Command('build.import', 'Add Image…', self.choose_image, icons.icon_name('plus'), enabled=lambda: not self.active()),
            ))))
            self.build_menu.set_popover(Menu(registry, variant='compact'))

        def image_selected(self, *_args):
            if getattr(self, 'updating_images', False) or self.active():
                return
            index = self.image_selector.get_selected()
            if index < len(self.image_paths):
                self.selected_images[self.target] = self.image_paths[index]
                self.update_build_menu()

        def name_preset(self):
            if not self.select_image():
                self.status_label.set_label('Choose an image before saving a preset.')
                return
            self.preset_name.set_text(self.target.title())
            self.preset_handle = LayerHost.window_host(self.island).present_modal(
                self.preset_editor, initial_focus=self.preset_name)
            self.preset_name.grab_focus()
            self.preset_name.select_region(0, -1)

        def close_preset(self):
            if self.preset_handle:
                self.preset_handle.close()
                self.preset_handle = None

        def save_preset(self):
            try:
                path = self.select_image()
                if path is None:
                    raise TesterError('Choose an image before saving a preset.')
                self.saved_presets = presets.save(self.preset_path, self.preset_name.get_text(), path, self.intent)
                self.preset_selector.set_model(Gtk.StringList.new(
                    ['Choose a preset…'] + [item['name'] for item in self.saved_presets]))
                self.close_preset()
                self.refresh_preset_rows()
            except (OSError, ValueError, TesterError) as exc:
                self.status_label.set_label(str(exc))

        def preset_selected(self, *_args):
            index = self.preset_selector.get_selected()
            if index == 0 or index > len(self.saved_presets):
                return
            try:
                if self.active():
                    raise TesterError('Shut down this session before loading a preset.')
                item = self.saved_presets[index - 1]
                path = presets.resolve(item)
                if path not in self.available_manifests():
                    raise TesterError('Add this preset’s image manifest again before loading it.')
                self.selected_images[item['target']] = path
                self.select_intent(item['intent'])
                self.select_target(item['target'])
                if self.layout.get_collapsed():
                    self.layout.set_show_sidebar(False)
            except (OSError, ValueError, KeyError, TesterError) as exc:
                self.status_label.set_label(str(exc))
            self.preset_selector.set_selected(0)

        def page(self, name, back=False):
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                              margin_start=40, margin_end=40, margin_top=32, margin_bottom=32,
                              valign=Gtk.Align.CENTER)
            clamp = Adw.Clamp(maximum_size=580, child=content, vexpand=True)
            scroll = ScrollView(clamp, fade_top=False, fade_bottom=False)
            if back:
                frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
                navigation = Gtk.Box(margin_start=16, margin_end=16, margin_top=12)
                button = IconButton(icons.icon_name('chevron-left'), 'Back', context=self.window.context, quiet=True)
                button.connect('clicked', lambda _button: self.commands.invoke('tester.back'))
                navigation.append(button)
                frame.append(navigation)
                frame.append(scroll)
                self.stack.add_named(frame, name)
            else:
                self.stack.add_named(scroll, name)
            return content

        def heading(self, page, title, subtitle):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_bottom=8)
            box.append(self.label(title, 'hero', centered=True))
            if subtitle:
                box.append(self.label(subtitle, muted=True, centered=True))
            page.append(box)

        def choice(self, title, description, icon, callback, selected=False, toggle=False):
            # The shared Card and type/icon roles own the visual language.
            button = Gtk.ToggleButton(hexpand=True, active=selected) if toggle else Gtk.Button(hexpand=True)
            button.add_css_class('tester-choice')
            button.add_css_class('flat')
            if selected:
                button.add_css_class('selected')
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                             halign=Gtk.Align.FILL, margin_start=24, margin_end=24,
                             margin_top=24, margin_bottom=24, valign=Gtk.Align.CENTER,
                             vexpand=True)
            graphic = icons.image(icon, pixel_size=48)
            graphic.add_css_class('tester-choice-icon')
            column.append(graphic)
            column.append(self.label(title, 'title-2', centered=True))
            if description:
                column.append(self.label(description, muted=True, centered=True))
            button.set_child(Card(column, padded=False))
            button.connect('clicked', lambda _button: callback())
            return button

        def build_welcome(self):
            page = self.page('welcome')
            self.heading(page, 'Hello, Luma.', None)
            choices = Gtk.Box(spacing=16, homogeneous=True)
            choices.append(self.choice('Try Luma', None, 'play',
                                       lambda: self.commands.invoke('tester.try')))
            choices.append(self.choice('Develop for Luma', None, 'code',
                                       lambda: self.commands.invoke('tester.develop')))
            page.append(choices)

        def build_session(self):
            page = self.page('session')
            self.session_title = self.label('Luma Mobile', 'title-1', centered=True)
            page.append(self.session_title)
            loading = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
            self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER, width_request=20, height_request=20)
            loading.append(self.spinner)
            self.status_label = self.label('Getting Luma ready…', centered=True)
            loading.append(self.status_label)
            page.append(loading)
            self.progress = Gtk.ProgressBar()
            page.append(self.progress)
            self.controls = Gtk.Box(spacing=12, homogeneous=True)
            self.developer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            self.developer_box.append(self.label('Your project, on Luma.', 'title-2'))
            self.developer_status = self.label('Connect a project to get started.', muted=True)
            self.developer_box.append(self.developer_status)
            self.developer_box.append(self.button('Connect a project…', self.choose_project))
            self.developer_box.append(self.button('Sync Project', self.sync_project))
            self.developer_box.append(self.button('Copy Development Command', self.copy_development_command))
            page.append(Card(self.developer_box))
            self.developer_card = self.developer_box.get_parent()
            self.return_button = self.button('Back to Luma', self.return_to_luma)
            self.return_button.set_visible(False)
            page.append(self.return_button)

        def build_live(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                           margin_start=24, margin_end=24, margin_top=24, margin_bottom=104)
            self.live_title = self.label('Luma Mobile', 'title-2')
            self.live_title.set_hexpand(True)
            self.viewer_holder = Gtk.Box(hexpand=True, vexpand=True)
            page.append(self.viewer_holder)
            self.viewer_holder.set_tooltip_text('Click to interact. Ctrl+Alt returns to launcher controls.')
            self.stack.add_named(page, 'live')

        def return_to_luma(self):
            self.stack.set_visible_child_name('live' if self.running() and self.graphical_ready else 'session')

        def connect_display(self):
            if self.viewer or not self.session.display_path.is_socket():
                return
            self.viewer = GuestDisplay(self.session.display_path,
                touch=self.session.touch if self.session.manifest['target'] == 'mobile' else None)
            self.viewer.connect('display-error', self.display_failed)
            self.viewer.connect('connected', lambda _v: self.reveal_display(self.session) if self.graphical_ready else None)
            self.viewer_holder.append(self.viewer)
            self.viewer.open()
            self.live_title.set_label('Luma ' + self.target.replace('-', ' ').title())
            self.tools_button.set_label('Project' if self.intent == 'develop' else 'Controls')
            self.tools_button.set_visible(self.intent == 'develop')
            # Keep first boot console text behind the native loading page. The
            # actual Wayland session must exist before revealing the live display.
            session = self.session
            def wait_for_graphics():
                import time
                try:
                    session.wait_graphical(180)
                    if session.manifest['target'] == 'mobile':
                        session.home()
                    time.sleep(1)
                    GLib.idle_add(self.reveal_display, session)
                except Exception as exc:
                    GLib.idle_add(self.graphics_failed, session, str(exc))
            threading.Thread(target=wait_for_graphics, daemon=True).start()

        def graphics_failed(self, session, message):
            if self.session is session:
                self.display_failure = message
                self.status_label.set_label(message)
            return False

        def display_failed(self, _viewer, message):
            if _viewer is not self.viewer:
                return
            self.display_failure = message
            self.status_label.set_label(message)
            self.stack.set_visible_child_name('session')

        def reveal_display(self, session):
            if self.session is session and self.running():
                self.graphical_ready = True
            if self.session is session and self.running() and self.viewer and self.viewer.ready:
                self.return_button.set_sensitive(True)
                self.return_button.set_label('Show Luma')
                self.return_button.set_visible(True)
                if self.stack.get_visible_child_name() != 'live':
                    self.window.set_default_size(*( (1000, 920) if self.target == 'mobile' else (1280, 820) ))
                self.stack.set_visible_child_name('live')
            return False

        def select_intent(self, intent):
            if self.active():
                self.intent = intent
                self.developer_card.set_visible(intent == 'develop')
                self.tools_button.set_visible(intent == 'develop')
                self.stack.set_visible_child_name('session' if intent == 'develop' else 'live' if self.graphical_ready else 'session')
                return
            self.intent = intent
            self.developer_card.set_visible(intent == 'develop')
            self.tools_button.set_visible(intent == 'develop')
            self.session_title.set_label('Luma ' + self.target.replace('-', ' ').title())
            self.status_label.set_label('Ready to start')
            self.spinner.stop()
            self.progress.set_visible(False)
            self.stack.set_visible_child_name('session')
            self.window.set_default_size(1100, 760)
            self.refresh()

        def select_target(self, target):
            if self.active():
                return
            self.target = target
            self.session_title.set_label('Luma ' + target.replace('-', ' ').title())
            self.workspace_title.set_label('Luma ' + target.replace('-', ' ').title())
            self.sidebar.list.select_row(self.device_rows[target])
            self.update_images()

        def available_manifests(self):
            directory = os.environ.get('LUMA_TESTER_CATALOG')
            paths = list(self.imported)
            if directory:
                paths += sorted(Path(directory).glob('*.json'))
            return paths

        def select_image(self):
            selected = self.selected_images.get(self.target)
            if selected in self.available_manifests():
                try:
                    if read_manifest(selected)['target'] == self.target:
                        return selected
                except (OSError, TesterError, ValueError, TypeError):
                    pass
            native = 'aarch64' if platform.machine().lower() in ('aarch64', 'arm64') else 'x86_64'
            preferred = 'aarch64' if self.target == 'mobile' else native
            candidates = []
            for priority, path in enumerate(self.available_manifests()):
                try:
                    manifest = read_manifest(path)
                    if manifest['target'] == self.target:
                        candidates.append((manifest['arch'] != preferred, priority, str(path)))
                except (OSError, TesterError, ValueError, TypeError):
                    continue
            if not candidates:
                return None
            candidates.sort()
            return Path(candidates[0][2])

        def active(self):
            return self.busy or bool(self.child and self.child.poll() is None) or self.running()

        def running(self):
            return bool(self.session and self.session.status() != 'stopped')

        def start(self):
            if self.active():
                self.stack.set_visible_child_name('session')
                return
            path = self.select_image()
            if path is None:
                self.status_label.set_label('Add a Luma image to start this experience.')
                return
            try:
                self.session = Session(path, state)
            except Exception as exc:
                self.status_label.set_label(str(exc))
                return
            self.manifest_path = str(path.resolve())
            if self.viewer:
                self.viewer.close()
                self.viewer_holder.remove(self.viewer)
                self.viewer = None
            self.graphical_ready = False
            self.display_failure = None
            self.shutting_down = False
            self.busy = True
            self.session_title.set_label('Luma ' + self.target.replace('-', ' ').title())
            self.status_label.set_label('Getting your Luma workspace ready…')
            self.spinner.start()
            self.progress.set_visible(True)
            self.controls.set_visible(False)
            self.return_button.set_sensitive(False)
            self.developer_card.set_visible(self.intent == 'develop')
            self.stack.set_visible_child_name('session')
            session, manifest_path = self.session, self.manifest_path
            def worker():
                try:
                    session.prepare(lambda message: GLib.idle_add(self.status_label.set_label, message))
                    GLib.idle_add(self.spawn, session, manifest_path)
                except Exception as exc:
                    GLib.idle_add(self.failed, str(exc))
            threading.Thread(target=worker, daemon=True).start()

        def spawn(self, session, manifest_path):
            try:
                self.log = open(session.directory / 'launcher.log', 'a')
                args = [sys.executable, '-m', 'luma_tester', '--state', str(session.root), 'start', manifest_path, '--embedded']
                env = dict(os.environ)
                env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get('PYTHONPATH', '')
                self.child = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, env=env)
                self.busy = False
                self.status_label.set_label('Opening Luma…')
            except Exception as exc:
                self.failed(str(exc))
            return False

        def failed(self, message):
            self.busy = False
            self.spinner.stop()
            self.progress.set_visible(False)
            self.return_button.set_sensitive(True)
            self.status_label.set_label(message)
            return False

        def refresh(self):
            if not hasattr(self, 'status_label'):
                return True
            if self.busy:
                self.progress.pulse()
            running = self.running()
            if self.child and self.child.poll() is not None:
                code = self.child.returncode
                self.child = None
                if self.log:
                    self.log.close()
                    self.log = None
                self.spinner.stop()
                self.progress.set_visible(False)
                self.controls.set_visible(False)
                self.return_button.set_sensitive(True)
                self.return_button.set_visible(False)
                self.stack.set_visible_child_name('session')
                self.status_label.set_label('Stopped' if code == 0 else 'Luma could not open. Open Advanced to view its startup log.')
            if running:
                self.connect_display()
                self.spinner.set_spinning(not self.graphical_ready and not self.display_failure)
                self.progress.set_visible(False)
                self.controls.set_visible(True)
                self.status_label.set_label(('Shutting down Luma…' if self.shutting_down else self.display_failure) or ('Luma is paused.' if self.session.status() == 'paused' else
                                           ('Running' if self.graphical_ready else 'Starting Luma…')))
                self.developer_status.set_label(str(self.project) if self.project else 'Connect a project, then sync your changes.')
            active = self.active()
            self.run_button.set_sensitive(not active and self.select_image() is not None)
            self.run_button.set_visible(not active)
            self.pause_button.set_visible(running)
            self.shutdown_button.set_visible(running)
            self.home_button.set_visible(running and self.target == 'mobile')
            self.home_button.set_sensitive(running and self.graphical_ready and not self.shutting_down and not self.home_busy)
            self.pause_button.set_sensitive(running and not self.shutting_down)
            self.shutdown_button.set_sensitive(running and not self.shutting_down)
            self.pause_button.set_label('Resume' if running and self.session.status() == 'paused' else 'Pause')
            self.image_selector.set_sensitive(not active and bool(self.image_paths))
            self.preset_selector.set_sensitive(not active and bool(self.saved_presets))
            self.build_menu.set_sensitive(not active)
            self.preset_heading.action_button.set_sensitive(not active and bool(self.image_paths))
            for row in self.preset_rows:
                row.set_sensitive(not active and hasattr(row, 'preset_index'))
            for name, row in self.device_rows.items():
                row.set_sensitive(True)
                row.set_trailing('Soon' if name not in self.catalog_targets else '')
            return True

        def home(self):
            if not self.running() or not self.graphical_ready or self.shutting_down or self.home_busy:
                return
            session = self.session
            self.home_busy = True
            self.home_button.set_sensitive(False)
            def finished():
                self.home_busy = False
                return False
            def worker():
                try:
                    session.home()
                except Exception as exc:
                    GLib.idle_add(self.display_failed, self.viewer, str(exc))
                finally:
                    GLib.idle_add(finished)
            threading.Thread(target=worker, daemon=True).start()

        def pause(self):
            if self.running():
                self.control('cont' if self.session.status() == 'paused' else 'stop')

        def stop(self):
            if not self.running() or self.shutting_down:
                return
            self.shutting_down = True
            session = self.session
            def worker():
                try:
                    session.shutdown()
                except Exception as exc:
                    GLib.idle_add(self.shutdown_failed, session, str(exc))
            threading.Thread(target=worker, daemon=True).start()

        def shutdown_failed(self, session, message):
            if self.session is session:
                self.shutting_down = False
                self.display_failure = message
                self.status_label.set_label(message)
            return False

        def control(self, command):
            try:
                self.session.qmp(command)
            except Exception as exc:
                self.status_label.set_label(str(exc))

        def choose_project(self):
            dialog = Gtk.FileDialog(title='Connect your project')
            dialog.select_folder(self.window, None, self.project_chosen)

        def project_chosen(self, dialog, result):
            try:
                path = dialog.select_folder_finish(result).get_path()
                if path:
                    self.project = Path(path)
                    self.developer_status.set_label(str(self.project))
            except GLib.Error:
                pass

        def sync_project(self):
            if not self.project or not self.running():
                self.developer_status.set_label('Open Luma and connect a project first.')
                return
            self.developer_status.set_label('Syncing your project…')
            session, project = self.session, self.project
            def worker():
                try:
                    session.wait()
                    session.sync(project)
                    GLib.idle_add(self.developer_status.set_label, 'Project synced. Copy the development command to start your app.')
                except Exception as exc:
                    GLib.idle_add(self.developer_status.set_label, str(exc))
            threading.Thread(target=worker, daemon=True).start()

        def copy_development_command(self):
            if not self.project or not self.session:
                self.developer_status.set_label('Connect a project and open Luma first.')
                return
            import shlex
            command = [os.environ.get('LUMA_TESTER_EXECUTABLE', 'luma-tester'), '--state', str(self.session.root), 'dev', self.manifest_path, str(self.project), '--', 'YOUR_APP_COMMAND']
            self.window.get_clipboard().set(shlex.join(command))
            self.developer_status.set_label('Copied. Replace YOUR_APP_COMMAND with the command that starts your app.')

        def show_advanced(self):
            if self.advanced:
                self.advanced.present()
                return
            self.advanced = AppWindow(application=self, app_id='io.luma.Tester.Advanced',
                                      title='Advanced', icon_name='settings-symbolic', commands=CommandRegistry(),
                                      default_width=560, default_height=440, minimum_width=440, minimum_height=360)
            self.advanced.set_transient_for(self.window)
            island = Island()
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                           margin_start=28, margin_end=28, margin_top=28, margin_bottom=28)
            body.append(self.label('Developer images', 'title-1'))
            body.append(self.label('Architecture is chosen automatically. Add a trusted image manifest to use a local build or a verified download.', muted=True))
            body.append(self.label('Build', 'group-heading'))
            if self.image_selector.get_parent():
                self.image_selector.get_parent().remove(self.image_selector)
            body.append(self.image_selector)
            if self.saved_presets:
                body.append(self.label('Preset', 'group-heading'))
                if self.preset_selector.get_parent():
                    self.preset_selector.get_parent().remove(self.preset_selector)
                body.append(self.preset_selector)
            body.append(self.button('Add Image…', self.choose_image))
            body.append(self.button('Open Workspace', self.workspace))
            body.append(self.label('For architecture overrides, firmware, memory, and headless sessions, use the CLI. Images are virtual environments; device hardware needs separate tests.', muted=True))
            island.append(body)
            self.advanced.set_body(island)
            self.advanced.connect('close-request', self.advanced_closed)
            self.advanced.present()

        def advanced_closed(self, _window):
            self.advanced = None
            return False

        def choose_image(self):
            if self.active():
                return
            dialog = Gtk.FileDialog(title='Add a developer image')
            dialog.open(self.advanced or self.window, None, self.image_chosen)

        def image_chosen(self, dialog, result):
            try:
                path = dialog.open_finish(result).get_path()
                if path:
                    manifest = read_manifest(path)
                    self.imported = [Path(path)] + [p for p in self.imported if p != Path(path)]
                    self.preferences.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    self.preferences.write_text(json.dumps({'images': [str(p) for p in self.imported[:16]]}))
                    self.preferences.chmod(0o600)
                    self.catalog_targets.add(manifest['target'])
                    self.select_target(manifest['target'])
                    self.selected_images[manifest['target']] = Path(path)
                    self.update_images()
                    self.status_label.set_label('Ready to start')
                    if self.advanced:
                        self.advanced.close()
            except GLib.Error:
                pass
            except Exception as exc:
                self.status_label.set_label(str(exc))

        def workspace(self):
            if self.session and self.session.directory.exists():
                Gio.AppInfo.launch_default_for_uri(self.session.directory.as_uri(), None)

        def window_close(self):
            self.window.close()

        def close_request(self, _window):
            if self.active():
                self.status_label.set_label('Shut down Luma before closing. Your workspace will be saved.')
                self.stack.set_visible_child_name('session')
                return True
            if self.advanced:
                self.advanced.close()
            return False

    return Application().run([])
