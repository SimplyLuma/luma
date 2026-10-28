# SPDX-License-Identifier: Apache-2.0
"""LumaUI simulator workspace with shared navigation and a native guest display."""
import os
os.environ['LUMA_APPKIT_FIXED_DARK'] = '1'
import json
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

# The finished interface requires the real shared toolkit. No stock GTK fallback.
try:
    from luma_appkit import Command, CommandGroup, CommandRegistry
    from luma_appkit import icons
    from luma_appkit.content_cards import Card
    from luma_appkit.content_controls import TextButton, Switch, IconOnlyButton
    from luma_appkit.content_field import TextField
    from luma_appkit.structure_placement import ModeSwitch
    from luma_appkit.content_type import apply_type
    from luma_appkit.rows_navigation import SidebarRow, SidebarSection, RowLead, sidebar_width
    from luma_appkit.menus import Menu
    from luma_appkit.action_toast import Toast, ToastHost
    from luma_appkit.action_center import ActionCenter, BarAction, BarWidget, SEPARATOR, make_control
    from luma_appkit.structure_layers import LayerHost
    from luma_appkit.widgets import AppWindow, IconButton, Island, IslandOverlaySplitView, NavigationSidebar, ScrollView, add_style_sheet
except (ImportError, ValueError) as exc:
    raise ImportError('Try Luma requires the LumaUI runtime (luma-developer-platform).') from exc

from .core import Session, TesterError, data_root, read_manifest
from .display import GuestDisplay
from .downloads import architecture_notice, host_arch
from . import presets, vm, experiences, device_profiles, image_library, host_devices, updates


def launch(state=None):
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    GLib.set_prgname('io.luma.Tester')
    GLib.set_application_name('Try Luma')
    Gtk.Window.set_default_icon_name('io.luma.Tester')
    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.luma.Tester')
            self.session = None
            self.child = None
            self.log = None
            self.busy = False
            self.intent = 'try'
            self.target = 'desktop'
            self.project = None
            self.closing = False
            self.settings = vm.load(data_root(state))
            self.developer_rows = []
            self.transfer_progress = None
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
            self.catalog_targets.update(('tv','smart-device','tablet'))
            self.advanced = None
            self.viewer = None
            self.graphical_ready = False
            self.display_failure = None
            self.shutting_down = False
            self.force_stop_available = False
            self.home_busy = False
            self.ssh_busy = False
            self.device_busy = False
            self.selected_images = {}
            self.architectures, self.architecture_choices = experiences.architecture_preferences({}, host_arch())
            self.library_items = image_library.cached(data_root(state))
            self.library_loading = False
            self.library_error = None
            self.update_checking = False
            self.pending_update = None
            self.update_preferences = data_root(state)/'update-preferences.json'
            self.selected_variants = {}
            try:
                preferences=json.loads(self.preferences.read_text())
                self.architectures, self.architecture_choices = experiences.architecture_preferences(preferences, host_arch())
                for key,value in preferences.get("selected_variants",{}).items():
                    target,architecture=key.split(":")
                    image=read_manifest(value)
                    if experiences.compatible(target,image) and image["arch"]==architecture:
                        self.selected_variants[key]=Path(value)
                        if Path(value) not in self.imported:self.imported.append(Path(value))
            except (OSError,ValueError,TypeError,TesterError,AttributeError):pass
            try:
                saved=json.loads(self.preferences.read_text()).get('selected_images',{})
                for target,path in saved.items():
                    path=Path(path)
                    if target in experiences.CHOICES and experiences.compatible(target,read_manifest(path)):
                        self.selected_images[target]=path
                        self.catalog_targets.add(target)
                        if path not in self.imported:self.imported.append(path)
            except (OSError,ValueError,TypeError,TesterError,AttributeError):pass
            self.restore_workspace=False
            self.restored_target=None
            allowed=set()
            for path in self.available_manifests():
                try:
                    image=read_manifest(path)
                    identity=(image['target'],image['arch'])
                    allowed.add((*identity,image['image']['sha256']))
                    allowed.update((*identity,value) for value in image.get('preserves_sessions',[]) if isinstance(value,str))
                except (OSError,ValueError,TesterError,TypeError):pass
            previous=list((data_root(state)/'sessions').glob('*/manifest.json'))
            previous.sort(key=lambda p:p.stat().st_mtime_ns,reverse=True)
            for path in previous:
                try:
                    image=read_manifest(path)
                    if (image['target'],image['arch'],image['image']['sha256']) not in allowed:continue
                    if image['target'] not in self.selected_images:
                        self.selected_images[image['target']]=path
                        self.imported.append(path)
                        self.catalog_targets.add(image['target'])
                    self.restore_workspace=True
                    if self.restored_target is None:self.restored_target=image['target']
                except (OSError,ValueError,TesterError,TypeError):pass

        def label(self, text, role='body', muted=False, centered=False):
            widget = Gtk.Label(label=text, wrap=True, xalign=0.5 if centered else 0)
            apply_type(widget, role, muted=muted)
            return widget

        def button(self, text, callback, primary=False, icon=None):
            button = TextButton(text, icon=icon, style='key' if primary else 'fill', on_click=callback)
            return button

        def words(self, button, text):
            if hasattr(button, 'label_widget'):
                button.label_widget.set_label(text)
                button.update_property([Gtk.AccessibleProperty.LABEL], [text])
            else:
                button.set_label(text)

        def remember(self):
            selected=dict(self.selected_images)
            variants=dict(self.selected_variants)
            chosen=self.select_image() if self.session else None
            if (self.session and chosen and read_manifest(chosen)['id']==self.session.manifest['id']
                    and (self.session.directory/'manifest.json').is_file()):
                selected[self.target]=self.session.directory/'manifest.json'
                variants[self.target+':'+self.session.manifest['arch']]=selected[self.target]
            vm.atomic_json(self.preferences, dict(images=[str(p) for p in self.imported[:16]],
                selected_images={target:str(path) for target,path in selected.items()},
                architectures=self.architectures, architecture_choices=sorted(self.architecture_choices), selected_variants={key:str(path) for key,path in variants.items()},
                intent=self.intent, target=self.target, workspace=True))

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
                Command('tester.updates', 'Check for Updates…', lambda:self.check_app_updates(manual=True), icons.icon_name('download')),
                Command('tester.pause', 'Pause or resume', self.pause, icons.icon_name('pause'), enabled=lambda: self.running()),
                Command('tester.shutdown', 'Shut down', self.stop, icons.icon_name('power'), enabled=lambda: self.running()),
                Command('tester.quit', 'Quit', self.window_close, icons.icon_name('log-out'), shortcut=('Ctrl', 'Q')),
            )),))
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(Path(__file__).parent))
            self.window = AppWindow(application=self, app_id='io.luma.Tester', title='Try Luma',
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
            try:saved=json.loads(self.preferences.read_text())
            except (OSError,ValueError,TypeError):saved={}
            if not isinstance(saved,dict):saved={}
            if saved.get('workspace') or self.restore_workspace:
                self.select_intent(saved.get('intent','try'))
                restored=saved.get('target',self.restored_target)
                if restored in self.catalog_targets:self.select_target(restored)
            self.window.present()
            self.refresh_library()
            self.check_app_updates()
            GLib.timeout_add_seconds(2, self.refresh)

        def build_sidebar(self):
            self.sidebar = NavigationSidebar(variant='destinations', width='narrow')
            self.sidebar.set_visible(False)
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                margin_start=16, margin_end=16, margin_top=12, margin_bottom=12)
            top = Gtk.Box()
            top.append(self.label('Workspace', 'group-heading'))
            top.get_first_child().set_hexpand(True)
            top.append(IconOnlyButton('settings-2', 'Virtual machine settings', size='small', on_click=self.show_advanced))
            header.append(top)
            self.mode_switch = ModeSwitch([('try', 'Try'), ('develop', 'Developer')],
                current=self.intent, on_change=self.select_intent, labels_only=True, fill=True, label='Workspace mode')
            header.append(self.mode_switch)
            self.sidebar.prepend(header)
            self.sidebar.append_section('Experiences')
            self.device_rows = {}
            for target, title, icon in [('desktop', 'Desktop', 'monitor'), ('tablet', 'Tablet', 'smartphone'),
                                        ('mobile', 'Mobile', 'smartphone'), ('tv', 'TV', 'monitor'),
                                        ('smart-device', 'Smart Home', 'house')]:
                row = SidebarRow(title, lead=RowLead.icon(icon))
                row.target = target
                row.set_sensitive(True)
                self.sidebar.append_row(row)
                self.device_rows[target] = row
            self.sidebar.list.connect('row-activated', self.device_activated)
            from . import guest_preview
            self.preview_rows = [SidebarSection('Preview login', ruled=True)]
            username = SidebarRow('Username')
            username.set_trail(self.label(guest_preview.USER, 'caption'))
            password = SidebarRow('Password')
            values = Gtk.Box(spacing=8)
            value = self.label(guest_preview.PASSWORD, 'caption');value.set_selectable(True)
            values.append(value)
            values.append(IconOnlyButton('copy', 'Copy preview password', size='small',
                on_click=lambda: self.window.get_clipboard().set(guest_preview.PASSWORD)))
            password.set_trail(values)
            note = Gtk.ListBoxRow(selectable=False, activatable=False)
            note.set_child(self.label('Use this when installing apps.', 'caption', muted=True))
            self.preview_rows.extend([username, password, note])
            for row in self.preview_rows:self.sidebar.list.append(row)
            self.developer_heading = SidebarSection('Developer', ruled=True)
            self.sidebar.list.append(self.developer_heading)
            self.developer_rows.append(self.developer_heading)
            self.ssh_row = SidebarRow('SSH access', lead=RowLead.icon('terminal'))
            self.ssh_row.callback = self.toggle_ssh
            self.ssh_switch = Switch(active=self.settings['agent_ssh'], label='SSH access',
                on_toggle=lambda enabled: self.toggle_ssh(enabled))
            self.ssh_row.set_trail(self.ssh_switch)
            self.sidebar.append_row(self.ssh_row)
            self.developer_rows.append(self.ssh_row)
            self.ssh_details = Gtk.ListBoxRow(selectable=False, activatable=False)
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                margin_start=24, margin_end=16, margin_top=4, margin_bottom=12)
            self.ssh_status = self.label('', 'caption', muted=True)
            details.append(self.ssh_status)
            self.ssh_values = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.ssh_fields = {}
            for title in ('Host', 'Port', 'Username'):
                line = Gtk.Box(spacing=12)
                label = self.label(title, 'caption', muted=True);label.set_hexpand(True)
                value = self.label('', 'caption');value.set_selectable(True)
                line.append(label);line.append(value);self.ssh_values.append(line)
                self.ssh_fields[title] = value
            details.append(self.ssh_values)
            self.ssh_copy = self.button('Copy SSH Command', self.copy_ssh_command, icon='copy')
            details.append(self.ssh_copy)
            self.ssh_details.set_child(details)
            self.sidebar.list.append(self.ssh_details)
            for title, icon, callback in [('Copy Agent Prompt', 'clipboard', self.copy_agent_prompt)]:
                row = SidebarRow(title, lead=RowLead.icon(icon));row.callback = callback
                self.sidebar.append_row(row);self.developer_rows.append(row)
            self.hardware_heading = SidebarSection('Hardware', ruled=True,
                action=('plus', 'Add hardware', self.show_hardware))
            self.sidebar.list.append(self.hardware_heading);self.developer_rows.append(self.hardware_heading)
            self.hardware_summary = SidebarRow('Audio, camera & USB', lead=RowLead.icon('usb'))
            self.hardware_summary.callback=self.show_hardware
            self.sidebar.append_row(self.hardware_summary);self.developer_rows.append(self.hardware_summary)
            self.folders_heading = SidebarSection('Shared Folders',
                action=('plus', 'Share a folder', self.choose_shared_folder))
            self.sidebar.list.append(self.folders_heading);self.developer_rows.append(self.folders_heading)
            self.folders_summary = SidebarRow('Choose folders to share', lead=RowLead.icon('folder'))
            self.folders_summary.callback=self.show_folders
            self.sidebar.append_row(self.folders_summary);self.developer_rows.append(self.folders_summary)
            self.snapshot_heading = SidebarSection('Snapshots', action=('plus', 'Save snapshot', self.save_snapshot))
            self.sidebar.list.append(self.snapshot_heading)
            self.snapshot_rows = []
            self.snapshot_signature = None
            self.pending_snapshot = None
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
            self.sidebar_toggle = make_control(BarAction('panel-left', tooltip='Show or hide sidebar',
                on_activate=lambda: self.layout.set_show_sidebar(not self.layout.get_show_sidebar())))
            self.workspace_title = self.label('')  # not displayed; title belongs in the workspace
            self.build_menu = Gtk.MenuButton(label='Images', tooltip_text='Choose an image')
            self.build_menu.get_first_child().add_css_class('lumaui-bar-button')
            self.build_menu.get_first_child().add_css_class('dropdown')
            self.arch_menu = Gtk.MenuButton(label='Automatic', tooltip_text='Guest CPU architecture')
            self.arch_menu.get_first_child().add_css_class('lumaui-bar-button')
            self.arch_menu.get_first_child().add_css_class('dropdown')
            self.run_button = self.button('Start', self.start, primary=True, icon='play')
            self.pause_button = self.button('Pause', self.pause, icon='pause')
            self.shutdown_button = self.button('Shut Down', self.stop, icon='power')
            self.device_button = self.button('Device', self.show_device, icon='smartphone')
            self.home_button = self.button('Home', self.home, icon='house')
            self.home_button.set_visible(False)
            self.toolbar.show_bar([BarWidget(self.sidebar_toggle), SEPARATOR, BarWidget(self.arch_menu), BarWidget(self.build_menu),
                SEPARATOR, BarWidget(self.device_button), BarWidget(self.home_button), BarWidget(self.run_button),
                BarWidget(self.pause_button), BarWidget(self.shutdown_button)])
            self.toolbar.set_visible(False)

        def workspace_changed(self, *_args):
            shown = self.stack.get_visible_child_name() in ('session', 'live')
            self.toolbar.set_visible(shown)
            self.sidebar.set_visible(shown)
            self.last_workspace_size = None
            if shown:
                self.workspace_title.set_label('Luma ' + experiences.LABELS[self.target])
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
            if hasattr(row, 'callback'):
                row.callback()
                return
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
            self.session_title.set_label('Luma ' + experiences.LABELS[self.target])
            self.workspace_title.set_label('Luma ' + experiences.LABELS[self.target])
            self.status_label.set_label('Ready to start')
            self.stack.set_visible_child_name('session')
            self.update_images()

        def select_architecture(self, architecture):
            if self.active():
                Toast.show(self.island, 'Shut down Luma to change architecture.', kind='warning')
                return
            if architecture not in ('auto','x86_64','aarch64'):
                return
            self.architectures[self.target]=architecture
            self.architecture_choices.add(self.target)
            self.update_images()
            self.remember()

        def remember_image(self, target, path):
            self.selected_images[target]=Path(path)
            self.selected_variants[target+':'+read_manifest(path)['arch']]=Path(path)

        def update_images(self):
            architecture=self.architectures.get(self.target,'auto')
            labels={'auto':'Automatic','x86_64':'x86-64','aarch64':'ARM64'}
            self.arch_menu.set_label(labels[architecture])
            choices=tuple(Command('arch.'+key, label, lambda value=key:self.select_architecture(value),
                checked=lambda value=key:self.architectures.get(self.target,'auto')==value,
                enabled=lambda:not self.active()) for key,label in labels.items())
            self.arch_menu.set_popover(Menu(CommandRegistry((CommandGroup('Architecture',choices),)), variant='compact', keep_parent=True))
            self.image_paths = []
            names = []
            for path in self.available_manifests():
                try:
                    manifest = read_manifest(path)
                    if experiences.compatible(self.target,manifest) and (architecture=='auto' or manifest['arch']==architecture):
                        self.image_paths.append(path)
                        names.append((manifest.get('build') or manifest['id'])+' · '+('ARM64' if manifest['arch']=='aarch64' else 'x86-64'))
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
            architectures = {read_manifest(path)['arch'] for path in self.image_paths}
            notice=architecture_notice(architectures)
            if selected and read_manifest(selected)['arch']!=host_arch():
                notice=('ARM64' if read_manifest(selected)['arch']=='aarch64' else 'x86-64')+' runs with emulation on this computer.'
            if self.target in ('tv','smart-device'):
                notice=(notice+' ' if notice else '')+'Uses the shared image’s current interface until OS mode support is available.'
            self.arch_notice.set_label(notice)
            self.arch_notice.set_visible(bool(names) and bool(self.arch_notice.get_label()))
            if not self.active():
                title = experiences.LABELS[self.target]
                missing=(f'Choose a Luma {title} image in Images.' if architecture!='auto' else
                    'Add an ARM64 ISO or developer image.' if self.target in ('tv','smart-device') else f'Add a Luma {title} image.')
                installer=bool(selected and read_manifest(selected).get('media')=='iso')
                self.status_label.set_label('Ready to start.' if selected else 'Start to download Luma and open your workspace.')
                self.run_button.set_sensitive(self.target not in experiences.COMING_SOON)

            self.refresh_preset_rows()

        def image_available(self, path):
            from urllib.parse import urlparse, unquote
            manifest=read_manifest(path)
            image=manifest['image']
            cached=data_root(state)/'images'/(image['sha256']+('.iso' if manifest.get('media')=='iso' else '.qcow2'))
            local=urlparse(image['url'])
            return cached.is_file() or (local.scheme=='file' and Path(unquote(local.path)).is_file())

        def update_build_menu(self, names=None):
            groups=[];local=[];seen=set();selected=self.select_image()
            for path in self.available_manifests():
                try:
                    image=read_manifest(path)
                    identity=(image['target'],image['arch'],image['image']['sha256'])
                    if identity in seen or not experiences.compatible(self.target,image):continue
                    seen.add(identity)
                    architecture='ARM64' if image['arch']=='aarch64' else 'x86-64'
                    session=Session(path,state)
                    from . import provision
                    status=('Workspace ready' if provision.ready(session) else
                        'ISO · '+('Downloaded' if self.image_available(path) else 'Download')
                        if image.get('media')=='iso' else
                        'Installed' if session.disk.exists() else 'Downloaded' if self.image_available(path) else 'Download')
                    local.append(Command('image.local.'+str(len(local)),image.get('build') or image['id'],
                        lambda p=path:self.use_image(p), description=architecture+' · '+status,
                        checked=lambda p=path:p==selected, enabled=lambda:not self.active()))
                except (OSError,ValueError,TypeError,TesterError):continue
            if local:groups.append(CommandGroup('On this computer',tuple(local)))
            architecture=self.architectures.get(self.target,'auto')
            published=[item for item in self.library_items
                if image_library.listed(item,data_root(state)) and image_library.supports(item,self.target)
                and (architecture=='auto' or item.get('arch')==architecture)]
            commands={'tester_image':[], 'os_image':[]}
            for index,item in enumerate(published):
                kind=item.get('kind');entries=commands[kind]
                downloaded=(data_root(state)/'images'/(item['sha256']+('.qcow2' if kind=='tester_image' else '.iso'))).is_file()
                detail=('ARM64 · ' if item['arch']=='aarch64' else 'x86-64 · ')
                detail+=('Latest · ' if not entries else '')+('Ready to run · ' if kind=='tester_image' else '')
                detail+=('Downloaded' if downloaded else f"{item['size_bytes']/10**9:.1f} GB · Download")
                entries.append(Command('image.remote.'+str(index),item.get('display_name') or item['file'],
                    lambda a=item:self.download_image(a),description=detail,icon=icons.icon_name('download'),enabled=lambda:not self.active()))
            for kind,title in (('tester_image','Ready to try'),('os_image','Luma images')):
                entries=commands[kind]
                if not entries:continue
                groups.append(CommandGroup(title,tuple(entries)))
            status='Checking for images…' if self.library_loading else self.library_error
            actions=[]
            if status:actions.append(Command('image.status',status,lambda:None,enabled=lambda:False))
            actions.extend((Command('image.file','Add from File…',self.choose_local_image,icon=icons.icon_name('folder'),enabled=lambda:not self.active()),
                Command('image.refresh','Check for New Images',self.refresh_library,icon=icons.icon_name('refresh-cw'),enabled=lambda:not self.library_loading)))
            groups.append(CommandGroup(None,tuple(actions)))
            self.build_menu.set_popover(Menu(CommandRegistry(groups),variant='compact',keep_parent=True))

        def use_image(self,path):
            if self.active():return
            self.architectures[self.target]=read_manifest(path)['arch']
            self.architecture_choices.add(self.target)
            self.remember_image(self.target,path)
            self.update_images();self.remember()

        def image_selected(self, *_args):
            if getattr(self, 'updating_images', False) or self.active():
                return
            index = self.image_selector.get_selected()
            if index < len(self.image_paths):
                self.remember_image(self.target, self.image_paths[index])
                self.update_images()
                self.remember()

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
                manifest=read_manifest(path)
                pinned=data_root(state)/'preset-images'/(manifest['id']+'-'+manifest['image']['sha256'][:12]+'.json')
                vm.atomic_json(pinned,manifest)
                path=pinned
                if path not in self.imported:self.imported.append(path)
                self.saved_presets = presets.save(self.preset_path, self.preset_name.get_text(), path, self.intent, self.settings, experience=self.target)
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
                    self.imported.append(path)
                self.remember_image(item['target'],path)
                self.architectures[item['target']]=read_manifest(path)['arch']
                self.architecture_choices.add(item['target'])
                if 'vm' in item:
                    self.settings = vm.save(data_root(state), item['vm'])
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
            graphic = icons.image('monitor', pixel_size=56)
            self.empty_graphic = graphic
            graphic.add_css_class('tester-choice-icon')
            graphic.set_halign(Gtk.Align.CENTER)
            page.append(graphic)
            self.session_title = self.label('Your Luma workspace', 'title-1', centered=True)
            page.append(self.session_title)
            self.spinner = Gtk.Spinner(halign=Gtk.Align.CENTER, width_request=24, height_request=24)
            self.spinner.set_visible(False)
            page.append(self.spinner)
            self.status_label = self.label('Choose an image, then start exploring.', centered=True, muted=True)
            page.append(self.status_label)
            self.arch_notice = self.label('', muted=True, centered=True)
            self.arch_notice.set_visible(False)
            page.append(self.arch_notice)
            self.progress = Gtk.ProgressBar(visible=False)
            page.append(self.progress)
            self.controls = Gtk.Box()  # lifecycle state; controls themselves live in the action bar
            self.return_button = self.button('Show Luma', self.return_to_luma, icon='monitor')
            self.return_button.set_visible(False)
            page.append(self.return_button)

        def build_live(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                           margin_start=24, margin_end=24, margin_top=24, margin_bottom=104)
            self.live_title = self.label('Luma Mobile', 'title-2')
            self.live_title.set_hexpand(True)
            self.viewer_holder = Gtk.Box(hexpand=True, vexpand=True)
            page.append(self.viewer_holder)
            self.viewer_holder.set_overflow(Gtk.Overflow.VISIBLE)
            self.stack.add_named(page, 'live')

        def return_to_luma(self):
            self.stack.set_visible_child_name('live' if self.running() and self.graphical_ready else 'session')

        def connect_display(self):
            if self.viewer or not self.session.display_available():
                return
            self.viewer = GuestDisplay(self.session.display_path,
                touch=self.session.touch if self.target == 'mobile' else None)
            self.viewer.connect('display-error', self.display_failed)
            self.viewer.connect('connected', lambda _v: self.reveal_display(self.session) if self.graphical_ready else None)
            self.viewer_holder.append(self.viewer)
            self.viewer.open()
            self.live_title.set_label('Luma ' + experiences.LABELS[self.target])

            # Keep first boot console text behind the native loading page. The
            # actual Wayland session must exist before revealing the live display.
            session = self.session
            def wait_for_graphics():
                import time
                try:
                    from . import provision
                    if provision.ready(session) or session.manifest.get('guest_protocol') == 'luma-unified-v1':
                        # First boot can prepare bundled apps before the login session starts.
                        session.wait_graphical(1800)
                        vm.configure_guest(session,vm.load(session.root))
                        GLib.idle_add(self.reveal_display,session)
                        return
                    if not session.manifest['cloud_init']:
                        deadline=time.monotonic()+60
                        while time.monotonic()<deadline and self.running():
                            if self.viewer and self.viewer.ready:
                                GLib.idle_add(self.reveal_display,session)
                                return
                            time.sleep(.2)
                        raise TesterError('The installation media did not provide a display.')
                    session.wait_graphical(180)
                    GLib.idle_add(self.status_label.set_label,'Preparing your saved workspace…')
                    vm.repair_legacy_renderer(session)
                    vm.ensure_mobile_display(session)
                    vm.configure_guest(session, vm.load(session.root))
                    if session.manifest['target'] == 'mobile':
                        capabilities = session.manifest.get('capabilities', {})
                        if capabilities.get('home', True):session.home()
                        configured=vm.load(session.root).get('device')
                        if configured and capabilities.get('dynamic_display', True):device_profiles.apply(session,configured)
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
            if _viewer is not self.viewer or self.shutting_down:
                return
            self.display_failure = message
            self.status_label.set_label(message)
            self.stack.set_visible_child_name('session')

        def reveal_display(self, session):
            if self.session is session and self.running():
                self.graphical_ready = True
            if self.session is session and self.running() and self.viewer and self.viewer.ready:
                try:self.viewer.set_device(json.loads((session.directory/'device.json').read_text()) if self.settings.get('device') else None)
                except (OSError,ValueError):self.viewer.set_device(None)
                self.return_button.set_sensitive(True)
                self.words(self.return_button, 'Show Luma')
                self.return_button.set_visible(True)
                if self.stack.get_visible_child_name() != 'live':
                    self.window.set_default_size(*( (1000, 920) if self.target == 'mobile' else (1280, 820) ))
                self.stack.set_visible_child_name('live')
            return False

        def select_intent(self, intent):
            self.intent = intent
            if self.mode_switch.current != intent:
                self.mode_switch.set_current(intent)
            for row in self.developer_rows:
                row.set_visible(intent == 'develop')
            if self.stack.get_visible_child_name() == 'welcome':
                self.stack.set_visible_child_name('live' if self.graphical_ready else 'session')
                self.window.set_default_size(1100, 760)
            self.remember()
            self.refresh()

        def select_target(self, target):
            if target in experiences.COMING_SOON:
                self.notify('This experience is coming soon.');return
            if self.active():
                return
            self.target = target
            self.empty_graphic.set_from_icon_name(icons.icon_name('smartphone' if target in ('mobile','tablet') else 'house' if target=='smart-device' else 'monitor'))
            self.session_title.set_label('Luma ' + experiences.LABELS[target])
            self.workspace_title.set_label('Luma ' + experiences.LABELS[target])
            self.sidebar.list.select_row(self.device_rows[target])
            self.update_images()
            self.remember()

        def available_manifests(self):
            return image_library.manifest_paths(data_root(state), self.imported,
                os.environ.get('LUMA_TESTER_CATALOG'))

        def select_image(self):
            architecture=self.architectures.get(self.target,'auto')
            selected = self.selected_variants.get(self.target+':'+architecture, self.selected_images.get(self.target))
            if selected in self.available_manifests():
                try:
                    image=read_manifest(selected)
                    if experiences.compatible(self.target,image) and (architecture=='auto' or image['arch']==architecture):
                        return selected
                except (OSError, TesterError, ValueError, TypeError):
                    pass
            native = 'aarch64' if platform.machine().lower() in ('aarch64','arm64') else 'x86_64'
            candidates=[]
            for path in self.available_manifests():
                try:candidates.append((path,read_manifest(path)))
                except (OSError,TesterError,ValueError,TypeError):continue
            return experiences.select(self.target,candidates,native,architecture)

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
                self.start_recommended()
                return
            try:
                self.session = Session(path, state)
                self.session.experience=self.target
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
            self.force_stop_available = False
            self.busy = True
            self.transfer_progress = None
            self.session_title.set_label('Luma ' + experiences.LABELS[self.target])
            self.status_label.set_label('Getting your Luma workspace ready…')
            self.spinner.set_visible(True)
            self.spinner.start()
            self.progress.set_visible(True)
            self.controls.set_visible(False)
            self.return_button.set_sensitive(False)

            self.stack.set_visible_child_name('session')
            session, manifest_path = self.session, self.manifest_path
            def worker():
                try:
                    if session.manifest['cloud_init']:
                        session.prepare(lambda message: GLib.idle_add(self.status_label.set_label, message),
                            progress=lambda value: GLib.idle_add(self.show_transfer_progress, value))
                    else:
                        # The supervisor verifies large ISO media once before boot.
                        session.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
                    GLib.idle_add(self.spawn, session, manifest_path)
                except Exception as exc:
                    GLib.idle_add(self.failed, str(exc))
            threading.Thread(target=worker, daemon=True).start()

        def spawn(self, session, manifest_path):
            if self.closing:
                self.busy=False
                self.window.close()
                return False
            try:
                self.log = open(session.directory / 'launcher.log', 'a')
                args = [sys.executable, '-m', 'luma_tester', '--state', str(session.root), 'start', manifest_path, '--embedded', '--experience',self.target]
                env = dict(os.environ)
                env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get('PYTHONPATH', '')
                self.child = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, env=env)
                self.busy = False
                self.status_label.set_label('Opening Luma…')
            except Exception as exc:
                self.failed(str(exc))
            return False

        def show_transfer_progress(self, value):
            self.transfer_progress = value
            self.transfer_updated = time.monotonic()
            self.status_label.set_label(value.message)
            self.progress.set_visible(True)
            if value.phase == 'connecting':
                self.progress.pulse()
            else:
                self.progress.set_fraction(value.fraction)
            return False

        def failed(self, message):
            self.busy = False
            self.spinner.stop()
            self.spinner.set_visible(False)
            self.progress.set_visible(False)
            self.return_button.set_sensitive(True)
            self.status_label.set_label(message)
            return False

        def refresh(self):
            if not hasattr(self, 'status_label'):
                return True
            if self.busy and (self.transfer_progress is None or self.transfer_progress.phase == 'connecting'):
                self.progress.pulse()
            if (self.busy and self.transfer_progress is not None
                    and self.transfer_progress.phase == 'downloading'
                    and time.monotonic() - self.transfer_updated >= 5):
                self.status_label.set_label(self.transfer_progress.message + ' · Waiting for data…')
            running = self.running()
            if self.child and self.child.poll() is None and not running and self.session:
                try:
                    setup=json.loads((self.session.directory/'setup.json').read_text())
                    if setup.get('phase')=='preparing':
                        self.status_label.set_label(setup['message'])
                        self.spinner.set_visible(True);self.spinner.start()
                        self.progress.set_visible(True);self.progress.pulse()
                except (OSError,ValueError,KeyError,TypeError):pass
            if self.child and self.child.poll() is not None:
                code = self.child.returncode
                self.child = None
                if self.log:
                    self.log.close()
                    self.log = None
                self.spinner.stop()
                self.spinner.set_visible(False)
                self.progress.set_visible(False)
                self.controls.set_visible(False)
                self.return_button.set_sensitive(True)
                self.return_button.set_visible(False)
                self.stack.set_visible_child_name('session')
                self.status_label.set_label('Your workspace is saved.' if code == 0 else 'Luma could not open. View startup details in Settings.')
            if running:
                self.connect_display()
                self.spinner.set_spinning(not self.graphical_ready and not self.display_failure)
                self.spinner.set_visible(not self.graphical_ready and not self.display_failure)
                self.progress.set_visible(False)
                self.controls.set_visible(True)
                self.status_label.set_label(('Shutting down Luma…' if self.shutting_down else self.display_failure) or ('Luma is paused.' if self.session.status() == 'paused' else
                                           ('Running' if self.graphical_ready else 'Starting Luma…')))

            if self.closing and running and not self.shutting_down:
                self.stop()
            if self.pending_snapshot and not self.active():
                name=self.pending_snapshot;self.pending_snapshot=None;self.create_snapshot(name)
            active = self.active()
            self.run_button.set_sensitive(not active and self.target not in experiences.COMING_SOON)
            selected = self.select_image()
            emulated = bool(selected and read_manifest(selected)['arch'] != host_arch())
            installer=bool(selected and read_manifest(selected).get('media')=='iso')
            action='Start'
            self.words(self.run_button, action+' with Emulation' if emulated else action)
            self.run_button.set_visible(not active)
            self.pause_button.set_visible(running)
            self.shutdown_button.set_visible(running)
            self.device_button.set_visible(self.target=='mobile' and not installer)
            capabilities = self.session.manifest.get('capabilities', {}) if self.session else {}
            if not running and selected:capabilities = read_manifest(selected).get('capabilities', {})
            self.device_button.set_sensitive(not self.busy and not self.device_busy and not self.shutting_down and
                capabilities.get('dynamic_display', True) and
                (not running or (self.graphical_ready and self.session.status()=='running')))
            self.device_button.set_tooltip_text(None if capabilities.get('dynamic_display', True) else 'This image uses a fixed phone display.')
            self.home_button.set_visible(running and not installer and self.target == 'mobile' and capabilities.get('home', True))
            self.home_button.set_sensitive(running and self.graphical_ready and not self.shutting_down and not self.home_busy)
            self.pause_button.set_sensitive(running and not self.shutting_down)
            self.shutdown_button.set_sensitive(running and not self.shutting_down)
            self.words(self.pause_button, 'Resume' if running and self.session.status() == 'paused' else 'Pause')
            self.image_selector.set_sensitive(not active and bool(self.image_paths))
            self.preset_selector.set_sensitive(not active and bool(self.saved_presets))
            self.build_menu.set_sensitive(not active)
            self.arch_menu.set_sensitive(not active)
            self.preset_heading.action_button.set_sensitive(not active and bool(self.image_paths))
            for row in self.preset_rows:
                row.set_sensitive(not active and hasattr(row, 'preset_index'))
            for name, row in self.device_rows.items():
                row.set_sensitive(True)
                row.set_trailing('Coming soon' if name in experiences.COMING_SOON else '')
                row.set_sensitive(name not in experiences.COMING_SOON)
            path = self.select_image() if not self.session else None
            preview = (self.session.manifest.get('guest_protocol') == 'luma-unified-v1' if self.session else
                not path or read_manifest(path).get('guest_protocol') == 'luma-unified-v1')
            for row in self.preview_rows:row.set_visible(preview)
            for row in self.developer_rows:
                row.set_visible(self.intent == 'develop')
            self.ssh_switch.set_active(self.settings['agent_ssh'])
            self.folders_summary.set_title(str(len(self.settings['folders']))+' shared folder'+('s' if len(self.settings['folders'])!=1 else '') if self.settings['folders'] else 'Choose folders to share')
            self.refresh_ssh_details()
            self.ssh_row.set_sensitive(not self.ssh_busy and self.managed_guest())
            self.words(self.shutdown_button,'Force Stop' if self.force_stop_available else 'Shut Down')
            self.refresh_snapshots()
            modal = getattr(self, 'sheet_handle', None)
            if self.pending_update and not active and not self.closing and (modal is None or modal.closed):
                artifact = self.pending_update
                self.pending_update = None
                self.show_app_update(artifact)
            if self.closing and not self.active():
                self.window.close()
            return True

        def show_device(self):
            config=dict(device_profiles.DEFAULT,**(self.settings.get('device') or {}))
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            labels=[('phone','Luma Phone'),('pixel-pro','Pixel 10 Pro'),('pixel-xl','Pixel 10 Pro XL'),
                ('fold','Pixel 10 Pro Fold'),('duo','Surface Duo'),('titan','Unihertz Titan'),('custom','Custom')]
            picker=Gtk.DropDown(model=Gtk.StringList.new([label for _,label in labels]))
            picker.set_selected(next(i for i,(key,_) in enumerate(labels) if key==config['profile']))
            picker.update_property([Gtk.AccessibleProperty.LABEL],['Screen profile'])
            body.append(self.label('Screen','group-heading'));body.append(picker)
            custom=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
            width=TextField(label='Width in pixels',value=str(config['width']))
            height=TextField(label='Height in pixels',value=str(config['height']))
            scale=TextField(label='UI scale',value=str(config['scale']))
            for field in (width,height,scale):custom.append(field)
            body.append(custom)
            posture=ModeSwitch([('folded','Folded'),('book','Book'),('flat','Open')],
                current=config['posture'],labels_only=True,fill=True,label='Posture')
            body.append(posture)
            rotation=ModeSwitch([('0','Portrait'),('90','Landscape')],
                current=str(config['rotation'] if config['rotation'] in (0,90) else 0),labels_only=True,fill=True,label='Orientation')
            body.append(rotation)
            detail=self.label('',muted=True);body.append(detail)
            def changed(*_args):
                key=labels[picker.get_selected()][0]
                custom.set_visible(key=='custom');posture.set_visible(key in ('fold','duo'))
                if key!='custom':
                    preview=device_profiles.describe(dict(config,profile=key,posture=posture.current,rotation=int(rotation.current)))
                    panel=preview['native_pixels'];logical=preview['logical']
                    detail.set_label(str(panel['width'])+' × '+str(panel['height'])+' device pixels · '+str(logical['width'])+' × '+str(logical['height'])+' UI\n'+str(round(100/preview['render_divisor']))+'% preview resolution')
                else:detail.set_label('The guest screen resizes; applications keep running.')
            posture.on_change=lambda _value:changed()
            rotation.on_change=lambda _value:changed()
            picker.connect('notify::selected',changed);changed()
            handle=[None]
            def apply():
                try:
                    value=device_profiles.validate(dict(profile=labels[picker.get_selected()][0],posture=posture.current,
                        rotation=int(rotation.current),width=int(width.text),height=int(height.text),scale=float(scale.text)))
                except (ValueError,TesterError) as exc:
                    detail.set_label(str(exc));return
                handle[0].cancel();self.change_device(value)
            body.append(self.button('Apply',apply,primary=True))
            handle[0]=self.sheet('Device',body)

        def change_device(self, value):
            if self.device_busy or self.busy or self.shutting_down:return
            if not self.running():
                self.save_settings(device=value);return
            if not self.graphical_ready or self.session.status()!='running':return
            self.device_busy=True;session=self.session
            def finished(actual,error):
                self.device_busy=False
                if error:self.notify(str(error));return False
                self.save_settings(device=value)
                if self.viewer:self.viewer.set_device(actual)
                return False
            def worker():
                try:actual=device_profiles.apply(session,value);error=None
                except Exception as exc:actual=None;error=exc
                GLib.idle_add(finished,actual,error)
            threading.Thread(target=worker,daemon=True).start()

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
            if self.force_stop_available:
                body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
                body.append(self.label('The guest has not shut down. Force Stop cuts its power; unsaved changes may be lost.'))
                handle=[None]
                def force():
                    self.control('quit');self.force_stop_available=False;handle[0].cancel()
                body.append(self.button('Force Stop',force,primary=True,icon='power'))
                handle[0]=self.sheet('Force Stop',body)
                return
            self.shutting_down = True
            session = self.session
            def worker():
                try:
                    if session.manifest['cloud_init']:session.wait(180)
                    session.shutdown()
                    if not session.manifest['cloud_init']:
                        import time
                        deadline=time.monotonic()+30
                        while time.monotonic()<deadline and session.status()!='stopped':time.sleep(.5)
                        if session.status()!='stopped':GLib.idle_add(self.offer_force_stop)
                except Exception as exc:
                    GLib.idle_add(self.shutdown_failed, session, str(exc))
            threading.Thread(target=worker, daemon=True).start()

        def offer_force_stop(self):
            if self.running():
                self.shutting_down=False;self.closing=False;self.force_stop_available=True
                self.notify('Use the guest power menu, or Force Stop if it is unresponsive.')
            return False

        def shutdown_failed(self, session, message):
            if self.session is session:
                self.shutting_down = False
                self.display_failure = message
                self.closing = False
                self.status_label.set_label(message)
            return False

        def control(self, command):
            try:
                self.session.qmp(command)
            except Exception as exc:
                self.status_label.set_label(str(exc))

        def notify(self, text):
            Toast.show(self.island, text)

        def selected_session(self):
            path = self.select_image()
            if path is None:
                raise TesterError('Choose an image first.')
            return Session(path, state)

        def sheet(self, title, body, width=360):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                width_request=min(width,max(280,self.window.get_width()-96)), margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
            header = Gtk.Box(spacing=12)
            heading = self.label(title, 'title-2');heading.set_hexpand(True)
            header.append(heading)
            handle = [None]
            close = IconOnlyButton('x', 'Close', size='small', on_click=lambda: handle[0].cancel())
            header.append(close);column.append(header)
            footer=body.get_last_child()
            if footer is not None and footer.has_css_class('key'):
                body.remove(footer)
            else:
                footer=None
            scroll = ScrollView(body)
            scroll.set_max_content_height(min(440,max(160,self.window.get_height()-260)))
            scroll.set_propagate_natural_height(True)
            column.append(scroll)
            if footer is not None:column.append(footer)
            handle[0] = LayerHost.window_host(self.island).present_modal(Card(column, padded=False))
            self.sheet_handle = handle[0]
            return handle[0]

        def save_settings(self, **changes):
            self.settings = vm.save(data_root(state), dict(self.settings, **changes))
            return self.settings

        def toggle_ssh(self, enabled=None):
            if self.ssh_busy:
                return
            if not self.managed_guest():
                self.notify('Managed SSH requires a developer image.')
                return
            enabled = not self.settings['agent_ssh'] if enabled is None else enabled
            if enabled == self.settings['agent_ssh']:
                return
            self.save_settings(agent_ssh=enabled)
            self.ssh_switch.set_active(enabled)
            if self.running() and self.graphical_ready:
                session = self.session
                self.ssh_busy=True
                self.ssh_row.set_sensitive(False)
                self.refresh_ssh_details()
                def worker():
                    try:
                        vm.configure_guest(session, self.settings, mount_folders=False)
                        GLib.idle_add(self.notify, 'SSH access enabled.' if self.settings['agent_ssh'] else 'SSH access disabled.')
                    except Exception as exc:
                        GLib.idle_add(self.ssh_failed, not enabled, str(exc))
                    finally:
                        GLib.idle_add(self.ssh_finished)
                threading.Thread(target=worker, daemon=True).start()
            else:
                self.refresh_ssh_details()
                self.notify('SSH access will be enabled on start.' if self.settings['agent_ssh'] else 'SSH access disabled.')

        def ssh_failed(self, previous, message):
            self.save_settings(agent_ssh=previous)
            self.ssh_switch.set_active(previous)
            self.notify(message)
            return False

        def ssh_finished(self):
            self.ssh_busy=False
            self.ssh_row.set_sensitive(True)
            self.refresh_ssh_details()
            return False

        def managed_guest(self):
            from . import provision
            if self.running():return self.session.manifest['cloud_init'] or provision.ready(self.session)
            path=self.select_image()
            return not path or read_manifest(path)['cloud_init'] or provision.ready(Session(path,state))

        def refresh_ssh_details(self):
            self.ssh_details.set_visible(self.intent == 'develop' and self.settings['agent_ssh'])
            self.ssh_values.set_visible(False)
            self.ssh_copy.set_sensitive(False)
            if not self.managed_guest():
                self.ssh_status.set_label('Managed SSH requires a developer image.')
                return
            if self.ssh_busy:
                self.ssh_status.set_label('Enabling SSH access…')
                return
            if not self.running() or not self.graphical_ready or self.shutting_down:
                self.ssh_status.set_label('Start Luma to connect.')
                return
            try:
                connection = vm.ssh_connection(self.session)
                for title, key in [('Host', 'host'), ('Port', 'port'), ('Username', 'user')]:
                    self.ssh_fields[title].set_label(str(connection[key]))
                self.ssh_values.set_visible(True)
                self.ssh_status.set_label('SSH key · No password needed')
                self.ssh_copy.set_sensitive(True)
            except (OSError, ValueError, TypeError, TesterError):
                self.ssh_status.set_label('Preparing your connection…')

        def copy_ssh_command(self):
            try:
                if self.ssh_busy or not self.running() or not self.graphical_ready or self.shutting_down:
                    raise TesterError('Start Luma and enable SSH access first.')
                self.window.get_clipboard().set(vm.ssh_connection(self.session)['command'])
                self.notify('SSH command copied.')
            except Exception as exc:
                self.notify(str(exc))

        def copy_agent_prompt(self):
            try:
                if not self.running() or not self.graphical_ready:
                    raise TesterError('Start Luma and enable SSH access first.')
                text = vm.agent_prompt(self.session, self.manifest_path,
                    os.environ.get('LUMA_TESTER_EXECUTABLE', 'luma-tester'))
                self.window.get_clipboard().set(text)
                self.notify('Agent prompt copied.')
            except Exception as exc:
                self.notify(str(exc))

        def show_hardware(self):
            self.close_sheet()
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            grid=Gtk.Grid(column_homogeneous=True,column_spacing=12,row_spacing=12)
            choices=[('Audio','volume-2',self.show_audio),('Microphone','mic',self.show_audio),
                ('Camera','camera',lambda:self.show_usb('camera')),('USB Devices','usb',lambda:self.show_usb('usb'))]
            for i,(title,icon,callback) in enumerate(choices):
                button=self.button(title,callback,icon=icon);button.set_size_request(-1,84)
                grid.attach(button,i%2,i//2,1,1)
            body.append(grid)
            body.append(self.label('Hardware changes apply on the next start. Keyboard and pointer input are already shared.', 'caption',muted=True))
            self.sheet('Hardware',body)

        def show_audio(self):
            self.close_sheet()
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            for title,key in [('Audio output','audio'),('Microphone','microphone')]:
                line=Gtk.Box(spacing=16);label=self.label(title);label.set_hexpand(True);line.append(label)
                line.append(Switch(active=self.settings[key],label=title,
                    on_toggle=lambda enabled,k=key:self.set_audio(k,enabled)))
                body.append(line)
            body.append(self.label('Uses your computer’s selected audio devices. Microphone sharing enables audio. Changes apply on the next start.','caption',muted=True))
            self.sheet('Audio & Microphone',body)

        def set_audio(self,key,enabled):
            changes={key:enabled}
            if key=='audio' and not enabled:changes['microphone']=False
            if key=='microphone' and enabled:changes['audio']=True
            self.save_settings(**changes)
            self.show_audio()

        def show_usb(self,kind):
            self.close_sheet()
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
            inventory=vm.usb_devices()
            devices=[d for d in inventory if (d['kind']=='camera')==(kind=='camera')]
            if kind=='camera':
                for device in host_devices.integrated_cameras():
                    body.append(self.label(device['name'],'group-heading'))
                    body.append(self.label(device['reason'],'caption',muted=True))
            for device in devices:
                line=Gtk.Box(spacing=12);words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
                words.set_hexpand(True);words.append(self.label(device['name']))
                reason='Eject this drive on your computer before sharing.' if device['mounted'] else 'Host access permission is needed.' if not device['accessible'] else 'Available to share'
                words.append(self.label(reason,'caption',muted=True));line.append(words)
                active=any((d['bus'],d['address'],d['name'])==(device['bus'],device['address'],device['name']) for d in self.settings['usb'])
                def toggle(enabled,d=device):
                    selected=[v for v in self.settings['usb'] if (v['bus'],v['address'])!=(d['bus'],d['address'])]
                    if enabled:selected.append({key:d[key] for key in ('name','bus','address')})
                    self.save_settings(usb=selected)
                switch=Switch(active=active,label=device['name'],on_toggle=toggle)
                switch.set_sensitive(active or (device['accessible'] and not device['mounted']))
                line.append(switch);body.append(line)
            known={(d['bus'],d['address'],d['name']) for d in inventory}
            for saved in self.settings['usb']:
                if (saved['bus'],saved['address'],saved['name']) in known:continue
                line=Gtk.Box(spacing=12);label=self.label(saved['name']+' · Disconnected');label.set_hexpand(True);line.append(label)
                line.append(IconOnlyButton('x','Remove device',size='small',on_click=lambda d=saved:self.forget_usb(d)))
                body.append(line)
            if not devices and (kind!='camera' or not host_devices.integrated_cameras()):
                body.append(self.label('Connect a USB camera to share it.' if kind=='camera' else 'Connect a USB device to share it.',muted=True))
            body.append(self.label('Selected devices connect on the next start.','caption',muted=True))
            self.sheet('Cameras' if kind=='camera' else 'USB Devices',body)

        def forget_usb(self,device):
            self.save_settings(usb=[d for d in self.settings['usb'] if d!=device]);self.close_sheet()

        def show_folders(self):
            self.close_sheet()
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            if not self.settings['folders']:body.append(self.label('Share a project folder with Luma.',muted=True))
            for index,folder in enumerate(self.settings['folders']):
                content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
                line=Gtk.Box(spacing=12);title=self.label(Path(folder['path']).name,'group-heading');title.set_hexpand(True);line.append(title)
                line.append(IconOnlyButton('x','Stop sharing folder',size='small',on_click=lambda path=folder['path']:self.remove_folder(path)))
                content.append(line)
                path_label=self.label(folder['path'],'caption',muted=True);path_label.set_selectable(True);content.append(path_label)
                content.append(self.label('In Luma: /mnt/shared/'+str(index+1),'caption',muted=True))
                row=Gtk.Box(spacing=12);label=self.label('Allow changes');label.set_hexpand(True);row.append(label)
                row.append(Switch(active=folder['writable'],label='Allow changes to '+Path(folder['path']).name,
                    on_toggle=lambda enabled,path=folder['path']:self.folder_access(path,enabled)))
                content.append(row);body.append(Card(content))
            body.append(self.button('Share Folder…',self.choose_shared_folder,primary=True,icon='folder-plus'))
            self.sheet('Shared Folders',body)

        def folder_access(self,path,enabled):
            self.save_settings(folders=[dict(folder,writable=enabled) if folder['path']==path else folder for folder in self.settings['folders']])
            if self.running():self.notify('Folder access changes apply on the next start.')

        def remove_folder(self,path):
            self.save_settings(folders=[folder for folder in self.settings['folders'] if folder['path']!=path])
            self.show_folders()
            if self.running():self.notify('Folder will disconnect on the next start.')

        def choose_shared_folder(self):
            self.close_sheet()
            dialog=Gtk.FileDialog(title='Share a folder with Luma')
            def chosen(dialog,result):
                try:
                    path=dialog.select_folder_finish(result).get_path()
                    if path:
                        path=str(Path(path).resolve())
                        folders=[folder for folder in self.settings['folders'] if folder['path']!=path]
                        self.save_settings(folders=folders+[dict(path=path,writable=False)])
                        self.show_folders()
                        if self.running():self.notify('Folder connects on the next start.')
                except GLib.Error:pass
                except Exception as exc:self.notify(str(exc))
            dialog.select_folder(self.window,None,chosen)

        def refresh_snapshots(self):
            self.snapshot_heading.action_button.set_sensitive(bool(self.select_image()) and not self.busy)
            if self.busy:return
            try:
                session = self.selected_session()
                signature = (str(session.disk), session.disk.stat().st_mtime_ns if session.disk.exists() else 0)
                if signature == self.snapshot_signature:return
                self.snapshot_signature = signature
                def worker():
                    try:
                        items = vm.checkpoints(session)
                        GLib.idle_add(self.render_snapshots, items)
                    except Exception as exc:GLib.idle_add(self.notify, str(exc))
                threading.Thread(target=worker, daemon=True).start()
            except TesterError:pass

        def render_snapshots(self, items):
            for row in self.snapshot_rows:self.sidebar.list.remove(row)
            self.snapshot_rows=[]
            for item in items:
                row=SidebarRow(item['name'], lead=RowLead.icon('history'))
                row.callback=lambda name=item['name']: self.restore_snapshot(name)
                self.sidebar.list.insert(row, self.snapshot_heading.get_index()+1+len(self.snapshot_rows));self.snapshot_rows.append(row)
            return False

        def save_snapshot(self):
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            body.append(self.label('Saves the guest disk while Luma stays open. Unsaved app memory and shared host folders are not included.', muted=True))
            name=TextField('Snapshot name',value='Before changes')
            body.append(name);handle=[None]
            def save():
                label=name.text.strip()
                if not label:self.notify('Name the snapshot first.');return
                handle[0].cancel()
                self.create_snapshot(label)
            body.append(self.button('Save Snapshot',save,primary=True,icon='camera'))
            handle[0]=self.sheet('Save Snapshot',body)

        def create_snapshot(self, name):
            self.busy=True
            session=self.selected_session()
            def worker():
                try:
                    vm.checkpoint(session,name)
                    GLib.idle_add(self.notify,'Snapshot saved.')
                except Exception as exc:GLib.idle_add(self.notify,str(exc))
                finally:GLib.idle_add(self.snapshot_finished)
            threading.Thread(target=worker,daemon=True).start()

        def snapshot_finished(self):
            self.busy=False;self.snapshot_signature=None
            self.refresh();return False

        def restore_snapshot(self, name):
            if self.active():self.notify('Shut down Luma before restoring a snapshot.');return
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
            body.append(self.label('Restore “'+name+'”? Changes made since this snapshot will be replaced.',muted=True))
            handle=[None]
            def restore():
                handle[0].cancel();self.busy=True;session=self.selected_session()
                def worker():
                    try:
                        vm.restore(session,name);GLib.idle_add(self.notify,'Snapshot restored. Start Luma to continue.')
                    except Exception as exc:GLib.idle_add(self.notify,str(exc))
                    finally:GLib.idle_add(self.snapshot_finished)
                threading.Thread(target=worker,daemon=True).start()
            body.append(self.button('Restore Snapshot',restore,primary=True,icon='history'))
            handle[0]=self.sheet('Restore Snapshot',body)

        def show_advanced(self):
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            body.append(self.label('Applies on the next start. Your existing disk stays intact.',muted=True))
            controls={}
            for key,title,value,low,high,step in [('memory','Memory (GiB)',self.settings['memory']//1024,1,64,1),
                    ('cpus','Processors',self.settings['cpus'],1,32,1),
                    ('disk_gib','Virtual disk (GiB)',self.settings['disk_gib'],8,256,8)]:
                control=TextField(title,value=str(value),purpose='number')
                body.append(control);controls[key]=control
            body.append(self.label('Disk space grows as used. Existing guest volumes may need filesystem expansion.',muted=True))
            graphics=ModeSwitch([('auto','Auto'),('virgl','Hardware'),('software','Software')],
                current=self.settings['graphics'],labels_only=True,fill=True,label='Graphics rendering')
            body.append(self.label('Graphics','group-heading'));body.append(graphics)
            selected=self.select_image()
            if selected and read_manifest(selected).get('media')=='iso':
                body.append(self.label('Installation media','group-heading'))
                body.append(Switch(active=self.settings['boot_iso'],label='Boot from ISO',
                    on_toggle=lambda enabled:self.save_settings(boot_iso=enabled)))
                body.append(self.label('Turn off Boot from ISO after installation to start the saved disk.',muted=True))
            body.append(self.button('Hardware…',self.show_hardware,icon='usb'))
            body.append(self.button('Open Workspace',self.workspace,icon='folder'))
            handle=[None]
            def apply():
                try:
                    values={key:int(control.text) for key,control in controls.items()}
                    values['memory']*=1024
                    try:
                        selected=self.select_image()
                        disk=Session(selected,state).disk if selected else None
                        current=0
                        if disk and self.running():
                            current=getattr(self.session,'disk_capacity',self.settings['disk_gib']*1024**3)
                        elif disk and disk.exists():
                            info=json.loads(subprocess.check_output(['qemu-img','info','--output=json',str(disk)],timeout=5))
                            current=info['virtual-size']
                        if values['disk_gib']*1024**3 < current:
                            raise TesterError('Virtual disks can grow. Shrinking this workspace is unavailable.')
                    except FileNotFoundError:pass
                    self.save_settings(**values,graphics=graphics.current)
                    handle[0].cancel();self.notify('Virtual machine settings saved.')
                except Exception as exc:self.notify(str(exc))
            body.append(self.button('Save Settings',apply,primary=True,icon='check'))
            handle[0]=self.sheet('Virtual Machine',body)

        def advanced_closed(self, _window):
            self.advanced = None
            return False

        def close_sheet(self):
            handle=getattr(self,'sheet_handle',None)
            if handle:handle.cancel();self.sheet_handle=None

        def show_images(self):
            if self.active():return
            self.build_menu.popup()

        def refresh_library(self):
            if self.library_loading:return
            self.library_loading=True;self.library_error=None
            def done(items,error):
                self.library_loading=False;self.library_error=error
                if items is not None:self.library_items=items
                # Replace the popup only after it closes: never unparent a live menu.
                if self.build_menu.get_active():
                    self.build_menu.connect('notify::active', self.library_menu_closed)
                else:self.update_build_menu()
                return False
            def worker():
                try:items=image_library.refresh(data_root(state));error=None
                except Exception:items=None;error='Couldn’t refresh downloads. Your local images are still available.'
                GLib.idle_add(done,items,error)
            threading.Thread(target=worker,daemon=True).start()

        def check_app_updates(self, manual=False):
            if self.update_checking:return
            self.update_checking=True
            def done(artifact,error):
                self.update_checking=False
                if error:
                    if manual:self.notify('Couldn’t check for updates. Try again later.')
                elif artifact is None:
                    if manual:self.notify('Try Luma is up to date.')
                else:
                    try:saved=json.loads(self.update_preferences.read_text())
                    except (OSError,ValueError):saved={}
                    shown=saved.get('offered_version') if isinstance(saved,dict) else None
                    if manual or shown != updates.package_identity(artifact)[0]:
                        self.pending_update=artifact
                        self.refresh()
                return False
            def worker():
                try:artifact=updates.check();error=None
                except Exception as exc:artifact=None;error=str(exc)
                GLib.idle_add(done,artifact,error)
            threading.Thread(target=worker,daemon=True).start()

        def show_app_update(self, artifact):
            if self.active():
                self.pending_update=artifact
                return
            release,_,_=updates.package_identity(artifact)
            # Separate preferences do not rewrite workspace/snapshot selections.
            try:vm.atomic_json(self.update_preferences,dict(offered_version=release))
            except OSError:pass
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            body.append(self.label(f'Try Luma {release} is available.'))
            body.append(self.label(f"{artifact['size_bytes']/10**6:.0f} MB · Download now and install when you’re ready.",muted=True))
            buttons=Gtk.Box(spacing=12,homogeneous=True)
            buttons.append(self.button('Later',self.close_sheet))
            buttons.append(self.button('Download',lambda:self.download_app_update(artifact),primary=True,icon='download'))
            body.append(buttons)
            self.sheet('Update Try Luma',body,width=400)

        def download_app_update(self, artifact):
            if self.active():return
            previous=self.stack.get_visible_child_name()
            self.close_sheet();self.busy=True;self.transfer_progress=None
            self.progress.set_fraction(0);self.progress.set_visible(True)
            self.spinner.set_visible(True);self.spinner.start()
            self.status_label.set_label('Downloading Try Luma…')
            self.stack.set_visible_child_name('session');self.refresh()
            def done(path,error):
                self.busy=False;self.transfer_progress=None
                self.progress.set_visible(False);self.spinner.stop();self.spinner.set_visible(False)
                self.stack.set_visible_child_name(previous);self.refresh()
                if error:self.notify(error)
                else:
                    self.notify('Update downloaded. Install it when you’re ready.')
                    launcher=Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))
                    def revealed(source,result):
                        try:source.open_containing_folder_finish(result)
                        except GLib.Error:self.notify('Update saved in '+str(path.parent))
                    # Reveal the package. Never execute it or close a running VM.
                    launcher.open_containing_folder(self.window,None,revealed)
                return False
            def worker():
                try:
                    path=updates.stage(artifact,data_root(state),
                        report=lambda message:GLib.idle_add(self.status_label.set_label,message),
                        progress=lambda value:GLib.idle_add(self.show_transfer_progress,value));error=None
                except Exception as exc:path=None;error=str(exc)
                GLib.idle_add(done,path,error)
            threading.Thread(target=worker,daemon=True).start()

        def library_menu_closed(self, button, _property):
            if not button.get_active():
                button.disconnect_by_func(self.library_menu_closed)
                self.update_build_menu()

        def start_recommended(self):
            """Resolve approved media without sending a new user through menus."""
            if self.active():return
            target=self.target
            architecture=self.architectures.get(target,'auto')
            if architecture=='auto':architecture=experiences.preferred_arch(target,host_arch())
            self.busy=True;self.stack.set_visible_child_name('session')
            self.status_label.set_label('Finding the latest Luma image…')
            self.spinner.set_visible(True);self.spinner.start();self.refresh()
            def done(items,error):
                self.busy=False
                if self.closing:
                    self.window.close();return False
                if error:self.failed(error);return False
                self.library_items=items
                candidates=[item for item in items if item['arch']==architecture and image_library.supports(item,target)]
                if not candidates:
                    self.failed('No approved Luma image is available for '+architecture+'. Check your connection and try again.')
                else:self.download_image(candidates[0],autostart=True)
                return False
            def worker():
                try:items=image_library.refresh(data_root(state));error=None
                except Exception as exc:
                    items=image_library.cached(data_root(state))
                    error=None if items else 'Couldn’t reach Luma downloads. Check your connection and try Start again.'
                GLib.idle_add(done,items,error)
            threading.Thread(target=worker,daemon=True).start()

        def download_image(self,artifact,autostart=False):
            if self.active():return
            if not image_library.listed(artifact,data_root(state)):
                self.notify('This image is no longer in the approved catalog.')
                return
            self.busy=True
            self.transfer_progress=None
            self.close_sheet()
            self.progress.set_fraction(0)
            self.progress.set_visible(True)
            self.spinner.set_visible(True)
            self.spinner.start()
            self.status_label.set_label('Downloading '+(artifact.get('display_name') or artifact['file'])+'…')
            self.stack.set_visible_child_name('session');self.refresh()
            def done(path,error):
                self.busy=False
                self.transfer_progress=None
                self.progress.set_visible(False)
                self.spinner.stop()
                self.spinner.set_visible(False)
                if error:self.failed(error)
                else:
                    self.add_manifest(path)
                    if autostart and not self.closing:self.start()
                return False
            def worker():
                try:
                    path=image_library.fetch(artifact,data_root(state),self.target,
                        lambda message:GLib.idle_add(self.status_label.set_label,message),
                        progress=lambda value:GLib.idle_add(self.show_transfer_progress,value));error=None
                except Exception as exc:path=None;error=str(exc)
                GLib.idle_add(done,path,error)
            threading.Thread(target=worker,daemon=True).start()

        def choose_local_image(self):
            if self.active():
                return
            dialog = Gtk.FileDialog(title='Add an ISO or developer image')
            dialog.open(self.advanced or self.window, None, self.image_chosen)

        def choose_image(self):
            self.show_images()

        def image_chosen(self, dialog, result):
            try:
                path = dialog.open_finish(result).get_path()
                if path:
                    if Path(path).suffix.lower()=='.iso':
                        self.choose_iso_arch(Path(path));return
                    self.add_manifest(Path(path))
            except GLib.Error:
                pass
            except Exception as exc:
                self.status_label.set_label(str(exc))

        def add_manifest(self,path):
            if path:
                manifest = read_manifest(path)
                self.imported = [Path(path)] + [p for p in self.imported if p != Path(path)]
                self.preferences.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.remember()
                self.preferences.chmod(0o600)
                self.catalog_targets.add(manifest['target'])
                target=self.target if experiences.compatible(self.target,manifest) else manifest['target']
                self.remember_image(target,path)
                self.architectures[target]=manifest['arch']
                self.architecture_choices.add(target)
                self.select_target(target)
                self.update_images()
                self.status_label.set_label('Choose an image, then start exploring.')
                if self.advanced:
                    self.advanced.close()

        def choose_iso_arch(self,path):
            body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            body.append(self.label(path.name,'group-heading'))
            body.append(self.label('Choose the CPU architecture of this ISO.',muted=True))
            arch=ModeSwitch([('aarch64','ARM64'),('x86_64','x86-64')],
                current=self.architectures.get(self.target) if self.architectures.get(self.target) in ('x86_64','aarch64') else experiences.preferred_arch(self.target,host_arch()),labels_only=True,fill=True,label='ISO architecture')
            body.append(arch)
            body.append(self.label('Use a generic UEFI image. Raspberry Pi-specific disk images require their own hardware profile.',muted=True))
            handle=[None]
            def add():
                architecture=arch.current;handle[0].cancel();self.busy=True
                self.status_label.set_label('Verifying local ISO…')
                def worker():
                    try:
                        path_manifest=experiences.import_iso(path,data_root(state),architecture)
                        GLib.idle_add(finished,path_manifest,None)
                    except Exception as exc:GLib.idle_add(finished,None,str(exc))
                def finished(manifest,error):
                    self.busy=False
                    if error:self.notify(error)
                    else:self.add_manifest(manifest)
                    self.refresh();return False
                threading.Thread(target=worker,daemon=True).start()
            body.append(self.button('Add ISO',add,primary=True,icon='plus'))
            handle[0]=self.sheet('Add ISO',body)

        def workspace(self):
            if self.session and self.session.directory.exists():
                Gio.AppInfo.launch_default_for_uri(self.session.directory.as_uri(), None)

        def window_close(self):
            self.window.close()

        def close_request(self, _window):
            self.remember()
            if self.active():
                self.closing = True
                self.status_label.set_label('Saving your workspace and closing…')
                self.stack.set_visible_child_name('session')
                if self.running() and not self.shutting_down:
                    self.stop()
                elif self.child and self.child.poll() is None:
                    # File cancellation also works on Windows, where killing
                    # the supervisor would leave its setup QEMU running.
                    (self.session.directory/'setup-cancel').touch()
                return True
            if self.advanced:
                self.advanced.close()
            return False

    return Application().run([])
