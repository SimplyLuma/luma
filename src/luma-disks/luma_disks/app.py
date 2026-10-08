# SPDX-License-Identifier: MPL-2.0
"""Disks window: LumaUI composition over fixture or UDisks2 facts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import re
import threading
from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from luma_appkit import (
    ActionCenter, AppWindow, BarAction, Card, ChoiceList, Column, Command, CommandGroup, CommandRegistry,
    DestructiveDialog, EmptyState, Island, IslandSplitView, Menu,
    ModeSwitch, Switch,
    NavigationSidebar, PanelHeading, PanelRow, ScrollView, Selection,
    TableHeader, TextField, TitleIsland, Toast, ToastHost, TypeLabel, add_style_sheet, apply_type,
    install_lumaui, lumaui_icon, panel_list,
)
from luma_appkit import lumaui_tokens
from luma_appkit.action_center import make_control
from luma_appkit.rows_navigation import RowLead, SidebarRow, sidebar_width

from .backend import DiskError, UDisksClient, explain_error
from .model import DriveView, VolumeView, display_size as size_text, fixture_drives, live_drives, volume_rows
from .visuals import SpeedGraph, StorageMap, Swatch
from . import partitions


APP_ID = 'org.projectluma.Disks.Preview'
FORMATS = {'btrfs': 'Btrfs', 'ext4': 'Ext4', 'exfat': 'exFAT',
           'ntfs': 'NTFS', 'vfat': 'FAT32'}
FORMAT_CHOICES = (
    ('btrfs', 'For Luma', 'Btrfs. Snapshots and compression; only Linux reads it.'),
    ('ext4', 'For Luma and other Linux', 'Ext4. Simple and dependable.'),
    ('exfat', 'For every computer', 'exFAT. Windows, Mac and Linux can all read and write it.'),
    ('ntfs', 'For Windows', 'NTFS. Best if it will live on a Windows computer.'),
    ('vfat', 'For older devices', 'FAT32. Cameras, TVs and consoles. Files up to 4 GB.'),
)
GROUPS = ('In this computer', 'Plugged in', 'Disk images')
SIDEBAR_WIDTH = 212
KIND_ICONS = {'ssd': 'hard-drive', 'hdd': 'database', 'usb': 'usb', 'iso': 'disc'}


def _column(*children: Gtk.Widget, spacing: int = 6) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for child in children:
        box.append(child)
    return box


def _clear(box: Gtk.Box) -> None:
    while child := box.get_first_child():
        box.remove(child)


def _text(value: str, role: str = 'body', *, wrap: bool = False,
          weight: int | None = None) -> TypeLabel:
    if role == 'caption' and weight is None:
        weight = 400
    label = TypeLabel(value, role=role, wrap=wrap, weight=weight)
    label.set_hexpand(True)
    if wrap:
        label.label.set_hexpand(True)
    if not wrap:
        label.label.set_ellipsize(Pango.EllipsizeMode.END)
    return label


def _plain_fact(label: str, value: str, warning: bool = False, *, first: bool = False) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.add_css_class('disks-fact-row')
    if first:
        row.add_css_class('first')
    title = _text(label, 'body')
    title.set_hexpand(True)
    title.label.add_css_class('disks-fact-label')
    number = _text(value, 'body', weight=550)
    number.set_hexpand(False)
    number.label.add_css_class('disks-fact-value')
    if warning:
        number.label.add_css_class('disks-fact-warning')
    row.append(title)
    row.append(number)
    return row


def _button(icon: str, label: str, callback, *, primary: bool = False,
            danger: bool = False, name: str = '') -> Gtk.Widget:
    button = make_control(BarAction(icon, label, callback, primary=primary, danger=danger))
    if name:
        button.set_name(name)
    return button


def _small_card_button(label: str, callback, name: str) -> Gtk.Button:
    button = make_control(BarAction('', label, callback, filled=True), size='tool')
    button.set_name(name)
    return button


def _format_choices(current: str = 'exfat', *, panel: bool = False) -> tuple[Gtk.Box, Callable[[], str]]:
    group = ChoiceList(FORMAT_CHOICES, selected=current, size='panel' if panel else 'regular')
    group.set_name('dsk-format-choices')
    return group, lambda: group.selected


def _erase_explanation(size: int, write_speed: int | None, mode: str) -> str:
    if mode == 'zero':
        hours = max(1, round(size / 1_000_000_000 / max(1, write_speed or 1) / 3.6))
        return f'Slow (about {hours} hours here), but the old data can’t be recovered.'
    return 'Fast. The old data could be recovered with special tools.'


def _disks_card(content: Gtk.Widget, name: str, *, small: bool = False,
                label: str = '') -> Card:
    surface = Card(content, padded=False, outlined=True)
    surface.set_name(name)
    if label:
        surface.update_property([Gtk.AccessibleProperty.LABEL], [label])
    content.set_margin_top(16 if small else 18)
    content.set_margin_bottom(16 if small else 20)
    content.set_margin_start(18 if small else 20)
    content.set_margin_end(18 if small else 20)
    return surface


class DisksWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.fixture_path = os.environ.get('LUMA_DISKS_FIXTURE')
        self.fixture = bool(self.fixture_path)
        self.client: UDisksClient | None = None
        self.drives: tuple[DriveView, ...] = ()
        self.drive_key = os.environ.get('LUMA_DISKS_SELECTED_DRIVE', 'nvme') if self.fixture else ''
        self.volume_key = os.environ.get('LUMA_DISKS_SELECTED_VOLUME', 'nvme:2') if self.fixture else ''
        self._generation = 0
        self._closed = False
        self._busy = False
        self._remembered_passphrases: dict[str, str] = {}
        self._sheet_key: str | None = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='luma-disks')
        self.cancel = threading.Event()
        super().__init__(application=application, app_id=APP_ID, title='Disks',
                         icon_name='lumaui-hard-drive-symbolic', commands=self._commands(),
                         default_width=980, default_height=660, minimum_width=360,
                         minimum_height=480, narrow_width=720)
        self.split = IslandSplitView(collapsed=False, show_content=True)
        self.split.set_sidebar_width_unit(Adw.LengthUnit.PX)
        # v71 .dskside is 212 wide; the kit's narrowest variant is 224.
        # TODO(kit-request disks-04-sidebar-width): a variant at 212.
        self.split.set_min_sidebar_width(SIDEBAR_WIDTH)
        self.split.set_max_sidebar_width(SIDEBAR_WIDTH)
        self.sidebar = NavigationSidebar(variant='destinations')
        self.sidebar.set_size_request(SIDEBAR_WIDTH, -1)
        self.sidebar.set_name('dsk-sidebar')
        self.sidebar.list.connect('row-selected', self._drive_selected)
        self.split.set_sidebar(Adw.NavigationPage(child=self.sidebar, title='Drives'))

        island = Island()
        island.set_name('dsk-island')
        self.page = _column(spacing=0)
        self.page.set_margin_top(32)
        self.page.set_margin_start(34)
        self.page.set_margin_end(34)
        self.page.set_margin_bottom(110)
        self.page.set_name('dsk-page')
        self.table_desktop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                     accessible_role=Gtk.AccessibleRole.LIST_BOX)
        self.table_phone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   accessible_role=Gtk.AccessibleRole.LIST_BOX)
        self.table_desktop.set_name('dsk-parts')
        self.table_phone.set_name('dsk-parts')
        self.table_phone.set_visible(False)
        self.meta_desktop = Adw.WrapBox(child_spacing=4, line_spacing=0)
        self.meta_stack = self.meta_desktop    # one wrapping line at every width, as v71
        self._facts_pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   spacing=14, homogeneous=True)
        self._facts_pair.set_name('dsk-facts')
        self._facts_pair.set_margin_top(22)
        clamp = Adw.Clamp(maximum_size=860, tightening_threshold=860, child=self.page)
        island.append(ScrollView(clamp, vexpand=True))
        self.host = ToastHost(island)
        self.split.set_content(Adw.NavigationPage(child=self.host, title='Disk'))
        self.set_body(self.split)
        self.actions = ActionCenter(phone_grown_inset=lumaui_tokens.ACTION_CENTER["frame_side"]).attach(self.host)
        self.actions.set_name('dsk-actions')
        self.actions.panel.set_max_content_height(540)    # v71 .dskphbar .fexp
        # The selected drive keeps its semantic icon while the menu is folded.
        self.title_island = TitleIsland(lead='menu', lead_icon='hard-drive',
                                        disclosure=True, grow=self._island_panel)
        self.title_island.set_name('dsk-title-island')
        self.title_island.float_over(island)
        self._breakpoints()
        self.split.connect('notify::collapsed', self._phone_changed)
        self.connect('close-request', self._on_close)
        self._loading()
        if self.fixture:
            # Fixture mode does not construct UDisksClient or inspect a real path.
            self._apply(fixture_drives(self.fixture_path))
            view = os.environ.get('LUMA_DISKS_VIEW')
            if view:
                GLib.idle_add(lambda: (self._dispatch(view), False)[1])
        else:
            self._connect_live()

    def _commands(self) -> CommandRegistry:
        return CommandRegistry((CommandGroup('', (
            Command('disks.attach', 'Attach disk image…', lambda: self._dispatch('attach'), lumaui_icon('plus')),
            Command('disks.health', 'SMART data and self-tests', lambda: self._dispatch('health'), lumaui_icon('shield-check')),
            Command('disks.speed', 'Test speed', lambda: self._dispatch('speed'), lumaui_icon('gauge')),
            Command('disks.save-image', 'Create disk image…', lambda: self._dispatch('image'), lumaui_icon('copy')),
            Command('disks.quit', 'Quit Disks', self.close, shortcut=('Ctrl', 'Q')),
        )),))

    def _breakpoints(self) -> None:
        # Last matching Adw breakpoint wins: phone repeats the narrow setters.
        for threshold in (820, lumaui_tokens.PHONE_MAX_WIDTH):
            point = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f'max-width: {threshold}px'))
            point.add_setter(self.split, 'collapsed', threshold == lumaui_tokens.PHONE_MAX_WIDTH)
            point.add_setter(self.page, 'margin-start', 16)
            point.add_setter(self.page, 'margin-end', 16)
            point.add_setter(self._facts_pair, 'orientation', Gtk.Orientation.VERTICAL)
            point.add_setter(self._facts_pair, 'homogeneous', False)
            point.add_setter(self._facts_pair, 'margin-top', 16)
            if threshold == lumaui_tokens.PHONE_MAX_WIDTH:
                # v71 phone: the page starts under the title island, and Health / Speed
                # stack 16 apart.
                point.add_setter(self.page, 'margin-top', 76)
                point.add_setter(self._facts_pair, 'spacing', 16)
                point.add_setter(self.table_desktop, 'visible', False)
                point.add_setter(self.table_phone, 'visible', True)
            self.add_breakpoint(point)

    def _phone(self) -> bool:
        return self.split.get_collapsed()

    def _phone_changed(self, *_args) -> None:
        """The window crossed the phone width: rows, bar and island change shape."""
        self.title_island.fold()
        if self._drive() is not None:
            self._render()

    def _text_field(self, label: str, **kwargs) -> TextField:
        """A labeled field in the shared phone panel or desktop sheet."""
        return TextField(label, size='panel' if self._bar_ready() else 'regular', **kwargs)

    def _sheet_footer(self) -> Gtk.Box:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if self._bar_ready():
            # v71 .dskact: two 48 buttons sharing the panel's width.
            footer.set_homogeneous(True)
            footer.set_hexpand(True)
            footer.add_css_class('disks-sheet-footer')
        else:
            footer.set_halign(Gtk.Align.END)
        return footer

    def _confirm(self, *, title: str, body: str, action: str, icon: str,
                 on_confirm: Callable[[bool], None]) -> None:
        """A destructive confirm: the grown bar on a phone, the dialog on a desktop."""
        if self._bar_ready():
            self._sheet_key = None    # the confirm replaces the sheet in the grown bar
            DestructiveDialog.in_bar(self.actions, title=title, body=body, action=action,
                                     on_confirm=lambda: on_confirm(False))
        else:
            DestructiveDialog.ask(self.host, title=title, body=body, action=action,
                                  icon=icon, on_confirm=on_confirm)

    def _bar_ready(self) -> bool:
        """A phone's dialogs grow the bar, which needs its row: a chosen volume gives it one."""
        return self._phone() and self._volume() is not None

    def _close_sheet(self) -> None:
        if self._sheet_key is not None and self.actions.grown == self._sheet_key:
            self.actions.fold()
        self._sheet_key = None

    def _pick_drive(self, key: str) -> None:
        if key != self.drive_key:
            self.drive_key = key
            drive = self._drive()
            self.volume_key = next((v.key for v in drive.volumes if not v.protected),
                                   drive.volumes[0].key if drive.volumes else '')
            self._render()
        self.split.set_show_content(True)

    def _island_panel(self) -> Gtk.Widget:
        """What the title island grows into: the drives, then the drive's actions."""
        drive = self._drive()

        def fold_then(callback):
            def run() -> None:
                self.title_island.fold()
                callback()
            return run

        # TODO(kit-request disks-02-island-menu-rows): 340 wide, 48 high two-line rows and a
        # warning dot on a drive that needs attention; the kit draws 320 and 58 until then.
        rows: list[object] = []
        for group, heading in (('In this computer', 'This computer'), ('Plugged in', 'Plugged in'),
                               ('Disk images', 'Disk images')):
            members = [d for d in self.drives if d.group == group]
            if not members and group != 'In this computer':
                continue
            rows.append(heading)
            for member in members:
                rows.append(PanelRow(member.name, icon=KIND_ICONS[member.kind], subtitle=member.model,
                                     detail=size_text(member.size), current=member.key == self.drive_key,
                                     closes=False, on_activate=fold_then(
                                         lambda key=member.key: self._pick_drive(key))))
        rows.append(PanelRow('Attach a disk image', icon='plus', closes=False,
                             on_activate=fold_then(lambda: self._dispatch('attach'))))
        if drive is not None:
            writable = self.fixture or os.environ.get('LUMA_DISKS_ALLOW_WRITES') == '1'
            removable = drive.group != 'In this computer'
            rows.append(drive.name)
            for icon, label, key, enabled in (
                    ('shield-check', 'SMART data and self-tests', 'health', True),
                    ('gauge', 'Test speed', 'speed', True),
                    ('settings-2', 'Drive settings…', 'settings', drive.kind == 'hdd'),
                    ('copy', 'Create disk image…', 'image', True)):
                rows.append(PanelRow(label, icon=icon, closes=False, sensitive=enabled,
                                     on_activate=fold_then(lambda key=key: self._dispatch(key))))
            if removable:
                rows.append(PanelRow('Restore disk image…', icon='rotate-ccw', closes=False,
                                     sensitive=writable,
                                     on_activate=fold_then(lambda: self._dispatch('restore'))))
                rows.append(PanelRow('Detach' if drive.kind == 'iso' else 'Power off', icon='power',
                                     closes=False, sensitive=writable,
                                     on_activate=fold_then(lambda: self._dispatch('power'))))
                rows.append(None)
                rows.append(PanelRow('Format disk…', icon='trash-2', danger=True, closes=False,
                                     sensitive=writable,
                                     on_activate=fold_then(lambda: self._dispatch('format-disk'))))
        return panel_list(rows, label='Drives')

    def _volume_panel(self) -> Gtk.Widget:
        """The phone's ⋯: what you can do to the chosen volume, in the grown bar."""
        volume, drive = self._volume(), self._drive()
        rows: list[object] = []
        if volume is None or drive is None or volume.tone == 'free':
            return panel_list(rows, label='Volume')
        writable = self.fixture or os.environ.get('LUMA_DISKS_ALLOW_WRITES') == '1'
        system = volume.protected
        locked_in = not writable or system
        if system:
            note = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            note.add_css_class('disks-system-note')
            note.append(Gtk.Image(icon_name=lumaui_icon('lock'), pixel_size=15, valign=Gtk.Align.START))
            note.append(_text(f'Luma runs from {volume.name}, so it can’t be resized, repaired or '
                              'deleted while it’s running.', 'meta', wrap=True))
            rows.append(note)
        if not volume.read_only:
            rows.append(PanelRow('Rename', icon='pencil', sensitive=writable and not volume.locked,
                                 on_activate=lambda: self._dispatch('rename')))
            rows.append(PanelRow('Resize', icon='arrow-left-right', sensitive=not locked_in,
                                 on_activate=lambda: self._dispatch('resize')))
            if not volume.locked:
                rows.append(PanelRow('Check filesystem', icon='check-check', sensitive=writable,
                                     on_activate=lambda: self._dispatch('check')))
                rows.append(PanelRow('Repair filesystem', icon='wrench', sensitive=not locked_in,
                                     on_activate=lambda: self._dispatch('repair')))
        if volume.encrypted and not volume.locked:
            rows.append(PanelRow('Lock', icon='lock', sensitive=writable,
                                 on_activate=lambda: self._dispatch('lock')))
        rows.append(PanelRow('Create partition image', icon='copy', sensitive=writable,
                             on_activate=lambda: self._dispatch('image-volume')))
        rows.append(PanelRow('Delete volume', icon='trash-2', danger=True, sensitive=not locked_in,
                             on_activate=lambda: self._dispatch('delete')))
        return panel_list(rows, label=volume.name)

    def _loading(self) -> None:
        _clear(self.page)
        empty = EmptyState('Reading disks', 'Looking for connected drives.', 'lumaui-hard-drive-symbolic')
        empty.set_name('dsk-loading')
        self.page.append(empty)
        self.actions.hide_bar()

    def _connect_live(self) -> None:
        generation = self._generation = self._generation + 1

        def worker():
            try:
                client = UDisksClient(lambda: GLib.idle_add(self._reload))
                drives = live_drives(client.disks())
            except Exception as error:
                GLib.idle_add(self._failed, generation, explain_error(error))
                return
            GLib.idle_add(self._connected, generation, client, drives)

        self.executor.submit(worker)

    def _connected(self, generation: int, client: UDisksClient, drives: tuple[DriveView, ...]) -> bool:
        if self._closed or generation != self._generation:
            client.close()
            return False
        self.client = client
        self._apply(drives)
        return False

    def _reload(self) -> bool:
        if self._closed or self.client is None:
            return False
        generation = self._generation = self._generation + 1
        client = self.client

        def worker():
            try:
                drives = live_drives(client.disks())
            except Exception as error:
                GLib.idle_add(self._failed, generation, explain_error(error))
                return
            GLib.idle_add(self._loaded, generation, drives)

        self.executor.submit(worker)
        return False

    def _loaded(self, generation: int, drives: tuple[DriveView, ...]) -> bool:
        if not self._closed and generation == self._generation:
            self._apply(drives)
        return False

    def _failed(self, generation: int, error: DiskError) -> bool:
        if not self._closed and generation == self._generation:
            self.page.append(_text(str(error), 'body', wrap=True))
            Toast.show(self.host, str(error), kind='error')
        return False

    def _apply(self, drives: tuple[DriveView, ...]) -> None:
        previous = self.drive_key
        self.drives = drives
        if not any(d.key == self.drive_key for d in drives):
            self.drive_key = drives[0].key if drives else ''
            self.volume_key = ''
            if previous and drives:
                Toast.show(self.host, 'The selected drive was disconnected.', kind='notified')
        drive = self._drive()
        if drive and not any(v.key == self.volume_key for v in volume_rows(drive)):
            self.volume_key = next((v.key for v in drive.volumes if not v.protected),
                                   drive.volumes[0].key if drive.volumes else '')
        self._render_sidebar()
        self._render()

    def _drive(self) -> DriveView | None:
        return next((d for d in self.drives if d.key == self.drive_key), None)

    def _volume(self) -> VolumeView | None:
        drive = self._drive()
        return next((v for v in volume_rows(drive) if v.key == self.volume_key), None) if drive else None

    def _render_sidebar(self) -> None:
        self.sidebar.clear()
        self._selecting = True
        for group in GROUPS:
            group_drives = [d for d in self.drives if d.group == group]
            if not group_drives and group != 'Disk images':
                continue
            if group == 'Disk images':
                self.sidebar.append_section(
                    group, action=('plus', 'Attach a disk image',
                                   lambda: self._dispatch('attach')))
                section = self.sidebar.list.get_last_child()
                section.action_button.set_name('dsk-attach')
            else:
                self.sidebar.append_section(
                    'This computer' if group == 'In this computer' else group)
            for drive in group_drives:
                trail = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                if drive.health_state in ('warn', 'fail'):
                    warning = Gtk.Box(width_request=7, height_request=7,
                                      valign=Gtk.Align.CENTER,
                                      accessible_role=Gtk.AccessibleRole.IMG)
                    warning.add_css_class('luma-status-dot')
                    warning.add_css_class('syncing')
                    warning.set_tooltip_text('Needs attention')
                    warning.update_property([Gtk.AccessibleProperty.LABEL], ['Needs attention'])
                    trail.append(warning)
                size = Gtk.Label(label=size_text(drive.size))
                size.add_css_class('lumaui-row-meta')
                trail.append(size)
                row = SidebarRow(drive.name, lead=RowLead.icon(KIND_ICONS[drive.kind]),
                                 trail=trail)
                row.drive_key = drive.key
                row.set_name('dsk-drive-'+drive.key)
                row.set_tooltip_text(drive.model)
                row.update_property([Gtk.AccessibleProperty.LABEL], [drive.model])
                self.sidebar.append_row(row)
                if drive.key == self.drive_key:
                    self.sidebar.list.select_row(row)
            if group == 'Disk images' and not group_drives:
                none = Gtk.ListBoxRow(selectable=False, activatable=False)
                none.set_child(_text('None attached', 'caption'))
                none.set_margin_start(12)
                none.set_name('dsk-no-images')
                self.sidebar.list.append(none)
        self._selecting = False

    def _drive_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if getattr(self, '_selecting', False) or row is None:
            return
        key = getattr(row, 'drive_key', None)
        if key is None:
            self._dispatch('attach')
            return
        self._pick_drive(key)

    def _select_volume(self, key: str) -> None:
        self.volume_key = key
        self._render()

    def _render(self) -> None:
        parent = self.meta_stack.get_parent()
        if isinstance(parent, Gtk.Box):
            parent.remove(self.meta_stack)
        _clear(self.page)
        drive = self._drive()
        if drive is None:
            self.page.append(EmptyState('No disks found', 'Connect a drive or attach a disk image.',
                                        'lumaui-hard-drive-symbolic'))
            self.actions.hide_bar()
            return
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header.set_name('dsk-header')
        _clear(self.meta_desktop)
        for value in (drive.model + ' ·', drive.device, '· ' + drive.scheme):
            part = (_text(value, 'mono_small', weight=500) if value == drive.device
                    else _text(value, 'meta'))
            part.set_hexpand(False)
            self.meta_desktop.append(part)
        big_name = _text(drive.name, 'page-title')
        big_name.set_visible(not self._phone())    # v71 phone: the title island says it
        title = _column(big_name, self.meta_stack, spacing=7)
        title.set_hexpand(True)
        header.append(title)
        icon = {'ok': 'shield-check', 'warn': 'triangle-alert', 'fail': 'triangle-alert',
                'none': 'circle-help'}[drive.health_state]
        pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        pill.append(Gtk.Image(icon_name=lumaui_icon(icon), pixel_size=15))
        pill_label = _text(drive.health_title, 'meta', weight=650)
        pill_label.set_hexpand(False)
        pill_label.label.add_css_class('disks-health-label')
        pill.append(pill_label)
        button = Gtk.Button(child=pill)
        button.set_hexpand(False)
        button.set_valign(Gtk.Align.CENTER)
        button.add_css_class('disks-health-pill')
        button.add_css_class('disks-health-'+drive.health_state)
        button.set_name('dsk-health-button')
        button.connect('clicked', lambda _b: self._dispatch('health'))
        header.append(button)
        self.page.append(header)
        diagram = StorageMap(drive, self.volume_key, self._select_volume)
        diagram.set_margin_top(22)
        self.page.append(diagram)
        self._render_volume_table(drive)
        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        summary.set_name('dsk-used')
        summary.set_margin_top(8)
        summary.set_margin_start(6)
        summary.set_margin_end(6)
        summary.append(_text(f'{size_text(drive.used)} used of {size_text(drive.size)}', 'small'))
        if drive.unallocated > 500_000_000:
            unallocated = _text(f'{size_text(drive.unallocated)} unallocated', 'small')
            unallocated.set_hexpand(False)
            unallocated.set_halign(Gtk.Align.END)
            summary.append(unallocated)
        self.page.append(summary)
        volume = self._volume()
        if volume is not None:
            card = self._volume_card(volume)
            card.set_margin_top(16 if self._phone() else 22)
            self.page.append(card)
        self._render_health_speed(drive)
        self._render_actions()
        health = '' if drive.health_state == 'none' else f' · {drive.health_title}'
        self.title_island.set_title(drive.name, size_text(drive.size) + health)
        self.title_island.set_lead_icon(KIND_ICONS[drive.kind])

    def _render_volume_table(self, drive: DriveView) -> None:
        self._render_desktop_volumes(drive)
        self._render_phone_volumes(drive)
        self.page.append(self.table_desktop)
        self.page.append(self.table_phone)

    def _volume_location(self, volume: VolumeView) -> str:
        return ('' if volume.tone == 'free' else
                volume.mount or ('Locked' if volume.locked else 'Not mounted'))

    def _volume_button(self, volume: VolumeView, line: Gtk.Widget) -> Gtk.Button:
        row = Gtk.Button(child=line, accessible_role=Gtk.AccessibleRole.OPTION)
        row.add_css_class('disks-volume-row')
        row.set_name('dsk-volume-'+volume.key.replace(':', '-'))
        row.update_property([Gtk.AccessibleProperty.LABEL],
                            [f'{volume.name}, {size_text(volume.size)}, {volume.format}'])
        Selection.mark(row, volume.key == self.volume_key)
        if volume.key == self.volume_key:
            row.add_css_class('disks-volume-on')    # the kit draws the chip; this keeps its text ink
        row.connect('clicked', lambda _b, key=volume.key: self._select_volume(key))
        return row

    def _render_desktop_volumes(self, drive: DriveView) -> None:
        table = self.table_desktop
        _clear(table)
        table.set_margin_top(10)
        head = TableHeader([Column(None, '', width=38), Column(None, 'Volume', width=243),
                            Column(None, 'Size', width=102), Column(None, 'Format', width=132),
                            Column(None, 'Mounted at', expand=True)], density='compact')
        head.set_name('dsk-table-header')
        table.append(head)
        for volume in volume_rows(drive):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            swatch_cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            swatch = Swatch(volume.tone)
            swatch.set_margin_start(12)
            swatch_cell.append(swatch)
            line.append(swatch_cell)
            name = _text(volume.name, 'body', weight=600)
            name.label.add_css_class('disks-volume-name')
            line.append(name)
            size = _text(size_text(volume.size), 'meta')
            size.label.add_css_class('disks-volume-secondary')
            line.append(size)
            fmt = _text(('LUKS · ' if volume.encrypted else '') + volume.format, 'meta')
            fmt.label.add_css_class('disks-volume-secondary')
            line.append(fmt)
            where = _text(self._volume_location(volume), 'mono_small', weight=500)
            where.label.add_css_class('disks-volume-where')
            line.append(where)
            head.align(line)
            row = self._volume_button(volume, line)
            row.set_size_request(-1, 36)
            table.append(row)

    def _render_phone_volumes(self, drive: DriveView) -> None:
        """v71 phone: no header; a volume is two lines, name and size over where it is and its format."""
        table = self.table_phone
        _clear(table)
        table.set_margin_top(10)
        table.set_spacing(4)
        for volume in volume_rows(drive):
            name = _text(volume.name, 'title_2', weight=600)
            name.label.add_css_class('disks-volume-name')
            size = _text(size_text(volume.size), 'list_title', weight=600)
            size.label.add_css_class('disks-volume-secondary')
            size.set_hexpand(False)
            first = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            first.append(name)
            first.append(size)
            where = _text(self._volume_location(volume), 'mono_small', weight=500)
            where.label.add_css_class('disks-volume-where')
            fmt = _text(('LUKS · ' if volume.encrypted else '') + volume.format
                        if volume.tone != 'free' else '', 'small')
            fmt.label.add_css_class('disks-volume-where')
            fmt.set_hexpand(False)
            second = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            second.append(where)
            second.append(fmt)
            lines = _column(first, second, spacing=3)
            lines.set_hexpand(True)
            swatch = Swatch(volume.tone)
            swatch.set_valign(Gtk.Align.CENTER)
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            item.set_margin_top(10)
            item.set_margin_bottom(10)
            item.set_margin_start(12)
            # v71's grid declares a fourth column it never fills: 56 + the 10 gap stay empty.
            item.set_margin_end(12 + 66)
            item.append(swatch)
            item.append(lines)
            row = self._volume_button(volume, item)
            row.add_css_class('disks-volume-row-phone')
            row.set_size_request(-1, 62)
            table.append(row)

    def _volume_card(self, volume: VolumeView) -> Gtk.Box:
        content = _column(spacing=0)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        swatch = Swatch(volume.tone)
        title.append(swatch)
        name = _text(volume.name, 'section-title')
        name.set_hexpand(False)
        title.append(name)
        if volume.protected or volume.encrypted:
            tag = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            tag.set_hexpand(False)
            tag.add_css_class('disks-volume-tag')
            if volume.protected:
                tag.update_property([Gtk.AccessibleProperty.LABEL],
                                    ['Luma is running from this volume'])
                tag.set_tooltip_text('Luma is running from this volume')
            tag.append(Gtk.Image(icon_name=lumaui_icon('lock' if volume.protected or volume.locked
                                                       else 'lock-open'), pixel_size=11))
            tag_label = _text('System' if volume.protected else
                              'Locked' if volume.locked else 'Unlocked', 'caption', weight=650)
            tag_label.set_hexpand(False)
            tag.append(tag_label)
            title.append(tag)
        content.append(title)
        if volume.tone != 'free' and not volume.locked and volume.used is not None:
            usage = Gtk.ProgressBar(fraction=min(1, volume.used / max(1, volume.size)))
            usage.set_size_request(-1, 8)
            usage.add_css_class('disks-usage')
            usage.add_css_class('disks-usage-'+volume.tone)
            usage.set_name('dsk-usage')
            usage.set_margin_top(14)
            content.append(usage)
            totals = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            totals.set_margin_top(6)
            used = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3, hexpand=True)
            used_size = _text(size_text(volume.used), 'small', weight=600)
            used_size.set_hexpand(False)
            used_size.label.add_css_class('disks-fact-value')
            used.append(used_size)
            used_label = _text('used', 'small')
            used_label.set_hexpand(False)
            used.append(used_label)
            totals.append(used)
            remaining = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            remaining_size = _text(size_text(volume.free), 'small', weight=600)
            remaining_size.set_hexpand(False)
            remaining_size.label.add_css_class('disks-fact-value')
            remaining.append(remaining_size)
            remaining_label = _text('free', 'small')
            remaining_label.set_hexpand(False)
            remaining_label.label.set_ellipsize(Pango.EllipsizeMode.NONE)
            remaining.append(remaining_label)
            totals.append(remaining)
            content.append(totals)
        facts = Adw.WrapBox(child_spacing=12, line_spacing=14)
        facts.set_margin_top(16)

        def detail(label: str, value: str, *, copy: bool = False, mono: bool = False) -> Gtk.Box:
            group = _column(_text(label, 'caption'), spacing=4)
            group.set_size_request(115, -1)
            group.set_hexpand(True)
            if copy:
                shown = value[:18] + ('…' if len(value) > 18 else '')
                copy_label = Gtk.Label(label=shown, xalign=0,
                                       ellipsize=Pango.EllipsizeMode.END)
                apply_type(copy_label, 'mono_small', weight=500)
                control = Gtk.Button(child=copy_label)
                control.set_hexpand(False)
                control.set_halign(Gtk.Align.START)
                control.update_property([Gtk.AccessibleProperty.DESCRIPTION], ['Copy UUID'])
                control.add_css_class('disks-uuid')
                control.connect('clicked', lambda _b: (Gdk.Display.get_default().get_clipboard().set(value),
                                                       Toast.show(self.host, 'Copied the UUID.')))
                group.append(control)
            else:
                shown_value = (_text(value, 'mono_small', weight=500) if mono
                               else _text(value, 'body', weight=550))
                shown_value.label.add_css_class('disks-card-value')
                group.append(shown_value)
            return group

        drive = self._drive()
        if volume.tone == 'free':
            rows = volume_rows(drive) if drive else ()
            position = next((i for i, item in enumerate(rows) if item.key == volume.key), 0)
            previous = rows[position - 1].name if position > 0 else 'Start of drive'
            facts.append(detail('Size', size_text(volume.size)))
            facts.append(detail('After', previous))
            facts.append(detail('Device', drive.device if drive else volume.device, mono=True))
        else:
            suffix = volume.device.removeprefix(drive.device) if drive else ''
            number = re.sub(r'\D', '', suffix) or '—'
            kind = 'EFI System' if volume.protected and volume.format == 'FAT32' else 'Linux filesystem'
            facts.append(detail('Device', volume.device, mono=True))
            facts.append(detail('Partition', f'{number} · {kind}'))
            facts.append(detail('UUID', volume.uuid or 'Not reported', copy=bool(volume.uuid)))
        content.append(facts)
        return _disks_card(content, 'dsk-volume-card', label=volume.name)

    def _render_health_speed(self, drive: DriveView) -> None:
        pair = self._facts_pair
        _clear(pair)
        health = _column(spacing=0)
        health_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        health_head.set_margin_bottom(8)
        health_title = _text('Health', 'body', weight=650)
        health_title.label.add_css_class('disks-fact-heading')
        health_head.append(health_title)
        health_button = _small_card_button('SMART data',
                                           lambda: self._dispatch('health'), 'dsk-health-details')
        health_button.set_sensitive(drive.health_state != 'none')
        if drive.health_state == 'none':
            health_button.set_tooltip_text('This drive doesn’t report SMART data')
        health_head.append(health_button)
        health.append(health_head)
        if drive.health_rows:
            for index, (label, value, warning) in enumerate(drive.health_rows):
                health.append(_plain_fact(label, value, warning, first=index == 0))
        else:
            health.append(_plain_fact('SMART', 'Not supported by this drive', first=True))
        health_card = _disks_card(health, 'dsk-health-card', small=True, label='Health')
        pair.append(health_card)
        speed = _column(spacing=0)
        speed_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        speed_head.set_margin_bottom(8)
        speed_title = _text('Speed', 'body', weight=650)
        speed_title.label.add_css_class('disks-fact-heading')
        speed_head.append(speed_title)
        speed_head.append(_small_card_button('Test speed',
                                             lambda: self._dispatch('speed'), 'dsk-speed-details'))
        speed.append(speed_head)
        speed.append(_plain_fact('Read', f'{drive.speed_read:,} MB/s' if drive.speed_read is not None else 'Not measured',
                                 first=True))
        speed.append(_plain_fact('Write', f'{drive.speed_write:,} MB/s' if drive.speed_write else 'Read-only'))
        speed.append(_plain_fact('Measured', '14 Sep' if self.fixture else 'Not measured'))
        speed_card = _disks_card(speed, 'dsk-speed-card', small=True, label='Speed')
        pair.append(speed_card)
        self.page.append(pair)

    def _render_phone_actions(self, volume: VolumeView) -> None:
        """v71 phone bar: icons only, the chosen volume's own actions. Drive actions live in the island."""
        allowed = self.fixture or os.environ.get('LUMA_DISKS_ALLOW_WRITES') == '1'
        guard = None if allowed else 'Real disk writes are disabled pending review'
        if volume.tone == 'free':
            self.actions.show_bar([BarAction('plus', tooltip='New volume',
                                             on_activate=lambda: self._dispatch('create'), primary=True)])
            return
        format_blocked = volume.protected or volume.read_only
        more = BarAction('ellipsis', tooltip='More', key='dsk-more', panel=self._volume_panel)
        if volume.locked:
            self.actions.show_bar([
                BarAction('lock-open', tooltip=f'Unlock {volume.name}', primary=True,
                          on_activate=lambda: self._dispatch('unlock'), sensitive=allowed and not format_blocked),
                BarAction('eraser', tooltip='Format', on_activate=lambda: self._dispatch('format'),
                          sensitive=allowed and not format_blocked),
                more])
            return
        items = []
        if not volume.protected:
            items.append(BarAction('eject' if volume.mount else 'hard-drive',
                                   tooltip='Unmount' if volume.mount else 'Mount',
                                   on_activate=lambda: self._dispatch('mount'), sensitive=allowed))
        if volume.mount:
            items.append(BarAction('folder-open', tooltip='Open in Filer',
                                   on_activate=lambda: self._dispatch('open')))
        if not format_blocked:
            items.append(BarAction('eraser', tooltip='Format', on_activate=lambda: self._dispatch('format'),
                                   sensitive=allowed))
        items.append(more)
        self.actions.show_bar(items)

    def _render_actions(self) -> None:
        volume = self._volume()
        if volume is None:
            self.actions.show_bar([])
            return
        if self._phone():
            self._render_phone_actions(volume)
            return
        if volume.tone == 'free':
            self.actions.show_bar([
                BarAction('plus', 'New volume…', lambda: self._dispatch('create'), primary=True),
                BarAction('ellipsis', tooltip='Drive', on_activate=lambda: self._dispatch('more')),
            ])
            self.more_button = self.actions.bar_row.get_last_child()
            self.more_button.set_name('dsk-more')
            return
        allowed = self.fixture or os.environ.get('LUMA_DISKS_ALLOW_WRITES') == '1'
        guard_reason = None if allowed else 'Real disk writes are disabled pending review'
        format_blocked = volume.protected or volume.read_only
        if volume.locked:
            self.actions.show_bar([
                BarAction('lock-open', 'Unlock…', lambda: self._dispatch('unlock'),
                          primary=True, sensitive=allowed and not format_blocked,
                          tooltip=guard_reason),
                BarAction('trash-2', 'Format…', lambda: self._dispatch('format'),
                          sensitive=allowed and not format_blocked,
                          tooltip=guard_reason),
                BarAction('ellipsis', tooltip='More', on_activate=lambda: self._dispatch('more')),
            ])
            self.more_button = self.actions.bar_row.get_last_child()
            self.more_button.set_name('dsk-more')
            return
        mount_label = 'Unmount' if volume.mount else 'Mount'
        mount_icon = 'lock' if volume.protected else 'eject' if volume.mount else 'hard-drive'
        items = [
            BarAction(mount_icon, mount_label, lambda: self._dispatch('mount'),
                      primary=not volume.protected,
                      tooltip=(f'Luma is running from {volume.name}' if volume.protected else guard_reason),
                      sensitive=allowed and not volume.protected),
        ]
        if volume.mount:
            items.append(BarAction('folder', 'Open', lambda: self._dispatch('open')))
        items.extend([
            BarAction('lock' if format_blocked else '', 'Format…', lambda: self._dispatch('format'),
                      tooltip=f'Luma is running from {volume.name}' if volume.protected else
                              'Disk images are read-only' if volume.read_only else guard_reason,
                      sensitive=allowed and not format_blocked),
            BarAction('ellipsis', tooltip='More', on_activate=lambda: self._dispatch('more')),
        ])
        self.actions.show_bar(items)
        self.more_button = self.actions.bar_row.get_last_child()
        self.more_button.set_name('dsk-more')

    def _dispatch(self, key: str) -> None:
        volume, drive = self._volume(), self._drive()
        if key == 'health':
            if drive:
                self._health_sheet(drive)
        elif key == 'speed':
            self._speed()
        elif key == 'format' and volume and drive:
            self._format(volume, drive)
        elif key == 'format-disk' and drive:
            self._format_disk(drive)
        elif key == 'mount' and volume:
            self._mount(volume)
        elif key == 'unlock' and volume:
            self._unlock_sheet(volume)
        elif key == 'lock' and volume:
            self._lock(volume)
        elif key == 'passphrase' and volume:
            self._passphrase_sheet(volume)
        elif key == 'rename' and volume:
            self._rename_sheet(volume)
        elif key == 'check' and volume:
            self._check(volume)
        elif key == 'repair' and volume:
            self._repair(volume)
        elif key == 'resize' and volume and drive:
            self._resize_sheet(volume, drive)
        elif key == 'delete' and volume and drive:
            self._delete_volume(volume, drive)
        elif key == 'create' and volume and drive:
            self._create_volume(volume, drive)
        elif key == 'power' and drive:
            self._remove_disk(drive)
        elif key == 'settings' and drive:
            self._drive_settings(drive)
        elif key == 'mount-options' and volume:
            self._mount_options(volume)
        elif key == 'attach':
            self._attach_image()
        elif key in ('image', 'image-volume') and drive:
            self._save_image(drive, volume if key == 'image-volume' else None)
        elif key in ('restore', 'restore-volume') and drive:
            self._restore_image(drive, volume if key == 'restore-volume' else None)
        elif key == 'more':
            self._more_menu()
        elif key == 'island':
            self.title_island.grow_into()
        elif key == 'open' and volume and volume.mount:
            Gio.AppInfo.launch_default_for_uri(Path(volume.mount).as_uri(), None)

    def _more_menu(self) -> None:
        volume, drive = self._volume(), self._drive()
        if drive is None or volume is None:
            return
        if self._phone():
            if volume.tone != 'free':
                self.actions.grow('dsk-more', self._volume_panel(), anchor=self.actions.bar_row.get_last_child())
            return
        writable = self.fixture or os.environ.get('LUMA_DISKS_ALLOW_WRITES') == '1'
        safe = writable and not volume.protected and not volume.read_only and volume.tone != 'free'
        groups = []
        if volume.tone != 'free':
            edit = [
                Command('volume.rename', 'Rename…', lambda: self._dispatch('rename'), lumaui_icon('pencil'),
                        enabled=lambda: safe and not volume.locked),
                Command('volume.resize', 'Resize…', lambda: self._dispatch('resize'), lumaui_icon('arrow-left-right'),
                        enabled=lambda: safe and not volume.encrypted and not volume.locked),
            ]
            if not volume.locked and not volume.read_only:
                edit += [
                    Command('volume.check', 'Check filesystem', lambda: self._dispatch('check'), lumaui_icon('check-check'),
                            enabled=lambda: safe and not volume.mount),
                    Command('volume.repair', 'Repair filesystem', lambda: self._dispatch('repair'), lumaui_icon('wrench'),
                            enabled=lambda: safe and not volume.mount),
                ]
            if volume.encrypted:
                edit.append(Command('volume.passphrase', 'Change passphrase…',
                                    lambda: self._dispatch('passphrase'), lumaui_icon('key'), enabled=lambda: safe))
                if not volume.locked:
                    edit.append(Command('volume.lock', 'Lock', lambda: self._dispatch('lock'), lumaui_icon('lock'),
                                        enabled=lambda: safe and not volume.mount))
            if not volume.read_only:
                edit.append(Command('volume.mount-options', 'Mount options…',
                                    lambda: self._dispatch('mount-options'), lumaui_icon('settings-2'),
                                    enabled=lambda: safe and not volume.locked))
            groups.append(CommandGroup('', tuple(edit)))
            groups.append(CommandGroup('', (
                Command('volume.image', 'Create partition image…', lambda: self._dispatch('image-volume'),
                        lumaui_icon('copy'), enabled=lambda: writable and not volume.mount),
                Command('volume.restore', 'Restore partition image…', lambda: self._dispatch('restore-volume'),
                        lumaui_icon('rotate-ccw'), enabled=lambda: safe and not volume.mount, destructive=True),
                Command('volume.delete', 'Delete volume…', lambda: self._dispatch('delete'), lumaui_icon('trash-2'),
                        enabled=lambda: safe, destructive=True),
            )))
        groups.append(CommandGroup(drive.name, (
                Command('drive.health', 'SMART data and self-tests', lambda: self._dispatch('health'), lumaui_icon('shield-check')),
                Command('drive.speed', 'Test speed', lambda: self._dispatch('speed'), lumaui_icon('gauge')),
                Command('drive.settings', 'Drive settings…', lambda: self._dispatch('settings'),
                        lumaui_icon('settings-2'), enabled=lambda: drive.kind == 'hdd'),
                Command('drive.image', 'Create disk image…', lambda: self._dispatch('image'), lumaui_icon('copy')),
                Command('drive.restore', 'Restore disk image…', lambda: self._dispatch('restore'),
                        lumaui_icon('rotate-ccw'), enabled=lambda: writable and drive.group != 'In this computer',
                        destructive=True),
                Command('drive.power', 'Power off', lambda: self._dispatch('power'), lumaui_icon('power'),
                        enabled=lambda: writable and drive.group != 'In this computer'),
                Command('drive.format', 'Format disk…', lambda: self._dispatch('format-disk'),
                        lumaui_icon('trash-2'), enabled=lambda: writable and drive.group != 'In this computer',
                        destructive=True),
            )))
        commands = CommandRegistry(tuple(groups))
        if getattr(self, 'more_menu', None) is not None:
            self.more_menu.popdown()
            self.more_menu.unparent()
        menu = Menu(commands)
        menu.set_name('dsk-more-menu')
        menu.set_parent(self.more_button)
        menu.set_position(Gtk.PositionType.TOP)
        menu.set_halign(Gtk.Align.END)    # v71 dskPop: right edge on the button's
        self.more_menu = menu
        menu.popup()

    def _run_write(self, work, success: str) -> None:
        if self.fixture:
            Toast.show(self.host, 'The fixture is read-only.', kind='notified')
            return
        if os.environ.get('LUMA_DISKS_ALLOW_WRITES') != '1':
            Toast.show(self.host, 'Real disk writes are disabled.', kind='warning')
            return
        if self.client is None or self._busy:
            return
        self._busy = True

        def worker():
            try:
                work()
            except Exception as error:
                GLib.idle_add(self._write_done, explain_error(error), '')
            else:
                GLib.idle_add(self._write_done, None, success)

        self.executor.submit(worker)

    def _read(self, work, done) -> None:
        generation = self._generation

        def worker():
            try:
                result = work()
            except Exception as error:
                GLib.idle_add(self._failed, generation, explain_error(error))
                return
            GLib.idle_add(lambda: (done(result) if not self._closed and generation == self._generation else None,
                                   False)[1])

        self.executor.submit(worker)

    def _write_done(self, error: DiskError | None, success: str) -> bool:
        self._busy = False
        if not self._closed:
            Toast.show(self.host, str(error) if error else success, kind='error' if error else 'done')
            self._reload()
        return False

    def _mount(self, volume: VolumeView) -> None:
        if volume.source is not None:
            self._run_write(lambda: self.client.mount(volume.source),
                            f'{volume.name} is {"unmounted" if volume.mount else "mounted"}.')
        else:
            self._fixture_replace_volume(volume, mount=None if volume.mount else f'/run/media/nick/{volume.name}')
            Toast.show(self.host, f'{volume.name} is {"unmounted" if volume.mount else "mounted"}.')

    def _unlock_sheet(self, volume: VolumeView) -> None:
        if not volume.encrypted or not volume.locked:
            return
        content, handle = self._open_sheet(f'Unlock {volume.name}',
                                      'Enter the passphrase for this encrypted volume.',
                                      'dsk-unlock-sheet')
        field = self._text_field('Passphrase', value=self._remembered_passphrases.get(volume.key, ''),
                          purpose='password')
        field.set_name('dsk-unlock-passphrase')
        content.append(field)
        remember = Switch(big=True, active=volume.key in self._remembered_passphrases)
        remember_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        remember_row.add_css_class('disks-protect-row')
        remember_row.append(_text('Remember until I log out', 'label'))
        remember_row.append(remember)
        content.append(remember_row)
        footer = self._sheet_footer()
        footer.append(_button('', 'Cancel', handle.close))

        def unlock() -> None:
            phrase = field.text
            field.text = ''
            if not phrase:
                Toast.show(self.host, 'Enter the passphrase.', kind='warning')
                return
            keep = remember.get_active()
            handle.close()
            if volume.source is None:
                if keep:
                    self._remembered_passphrases[volume.key] = phrase
                else:
                    self._remembered_passphrases.pop(volume.key, None)
                self._fixture_replace_volume(volume, locked=False,
                                             mount=f'/run/media/nick/{volume.name}')
                Toast.show(self.host, f'{volume.name} is unlocked and mounted.')
            else:
                def work() -> None:
                    self.client.unlock(volume.source, phrase)
                    GLib.idle_add(self._remember_after_unlock, volume.key,
                                  phrase if keep else None)
                self._run_write(work,
                                f'{volume.name} is unlocked and mounted.')

        footer.append(_button('', 'Unlock', unlock, primary=True,
                              name='dsk-unlock-go'))
        content.append(footer)

    def _remember_after_unlock(self, key: str, phrase: str | None) -> bool:
        if not self._closed:
            if phrase is None:
                self._remembered_passphrases.pop(key, None)
            else:
                self._remembered_passphrases[key] = phrase
        return False

    def _lock(self, volume: VolumeView) -> None:
        if not volume.encrypted or volume.locked or volume.mount:
            Toast.show(self.host, 'Unmount the encrypted volume before locking it.', kind='warning')
            return
        if volume.source is None:
            self._fixture_replace_volume(volume, locked=True, mount=None)
            Toast.show(self.host, f'{volume.name} is locked.')
        else:
            self._run_write(lambda: self.client.lock(volume.source), f'{volume.name} is locked.')

    def _passphrase_sheet(self, volume: VolumeView) -> None:
        if not volume.encrypted:
            return
        content, handle = self._open_sheet(f'Change passphrase for {volume.name}',
                                      'A LUKS header backup is required before changing the passphrase.',
                                      'dsk-passphrase-sheet')
        old = self._text_field('Current passphrase', purpose='password')
        new = self._text_field('New passphrase', purpose='password')
        again = self._text_field('Confirm new passphrase', purpose='password')
        backup = self._text_field('New header backup file', placeholder='Choose an unused path')
        for field in (old, new, again, backup):
            content.append(field)
        footer = self._sheet_footer()
        footer.append(_button('', 'Cancel', handle.close))

        def change() -> None:
            current, replacement, repeated, target = old.text, new.text, again.text, backup.text.strip()
            old.text = new.text = again.text = ''
            if not current or not replacement or replacement != repeated or not target:
                Toast.show(self.host, 'Complete the passphrases and backup file.', kind='warning')
                return
            handle.close()

            def confirmed(_also) -> None:
                if volume.source is None:
                    Toast.show(self.host, 'Passphrase changed in this fixture.')
                else:
                    self._run_write(lambda: self.client.change_passphrase(volume.source, current,
                                                                           replacement, target),
                                    'Passphrase changed. Keep the header backup safe.')

            self._confirm(
                title=f'Change passphrase for {volume.name}?',
                body='The LUKS header will be backed up before its passphrase changes.',
                action='Change passphrase', icon='lock', on_confirm=confirmed)

        footer.append(_button('', 'Change passphrase', change, primary=True,
                              name='dsk-passphrase-go'))
        content.append(footer)

    def _rename_sheet(self, volume: VolumeView) -> None:
        content, handle = self._open_sheet(f'Rename {volume.name}',
                                      'The name you see in Filer and on other computers.',
                                      'dsk-rename-sheet')
        field = self._text_field('Name', value=volume.name)
        field.set_name('dsk-rename-name')
        content.append(field)
        footer = self._sheet_footer()
        footer.append(_button('', 'Cancel', handle.close))

        def rename() -> None:
            new_name = field.text.strip()
            if not new_name or new_name == volume.name:
                return
            handle.close()
            if volume.source is None:
                self._fixture_replace_volume(volume, name=new_name)
                Toast.show(self.host, f'Renamed to {new_name}.')
            else:
                self._run_write(lambda: self.client.rename(volume.source, new_name),
                                f'Renamed to {new_name}.')

        footer.append(_button('', 'Rename', rename, primary=True, name='dsk-rename-go'))
        content.append(footer)

    def _check(self, volume: VolumeView) -> None:
        if volume.mount or volume.protected:
            Toast.show(self.host, 'Unmount this volume before checking it.', kind='warning')
            return
        if volume.source is None:
            Toast.show(self.host, f'{volume.name}: no errors found.')
            return

        def checked(clean: bool) -> None:
            if clean:
                Toast.show(self.host, f'{volume.name}: no errors found.')
                return
            self._confirm(title=f'Repair {volume.name}?',
                                  body='Repair may change or remove damaged files. Back up what you can first.',
                                  action='Repair', icon='wrench',
                                  on_confirm=lambda _also: self._run_write(
                                      lambda: self.client.check(volume.source, repair=True,
                                                                confirmation=volume.name),
                                      f'{volume.name}: repair finished.'))

        self._read(lambda: self.client.check(volume.source), checked)

    def _repair(self, volume: VolumeView) -> None:
        if volume.mount or volume.protected or volume.locked:
            Toast.show(self.host, 'Unmount this editable volume before repairing it.', kind='warning')
            return

        def do_repair(_also) -> None:
            if volume.source is None:
                Toast.show(self.host, f'{volume.name}: repair finished.')
            else:
                self._run_write(lambda: self.client.check(volume.source, repair=True,
                                                          confirmation=volume.name),
                                f'{volume.name}: repair finished.')

        self._confirm(title=f'Repair {volume.name}?',
                              body='Repair may change or remove damaged files. Back up what you can first.',
                              action='Repair', icon='wrench', on_confirm=do_repair)

    def _resize_sheet(self, volume: VolumeView, drive: DriveView) -> None:
        if volume.protected or volume.tone == 'free' or (volume.mount and not self.fixture):
            Toast.show(self.host, 'Unmount this volume before resizing it.', kind='warning')
            return
        if volume.source is None:
            self._resize_form(volume, drive, None, None)
        else:
            def limits():
                plan = partitions.inspect(self.client, drive.source)
                _flags, upper = partitions.resize_limits(self.client, plan, volume.source)
                return plan, upper
            self._read(limits, lambda result: self._resize_form(volume, drive, *result))

    def _resize_form(self, volume: VolumeView, drive: DriveView, plan, upper: int | None) -> None:
        # The backend makes the final direction and adjacency checks before any write.
        upper = upper if upper is not None else volume.size + drive.unallocated
        unit = partitions.MIB
        minimum = max(unit, partitions.aligned(int((volume.used or 0) * 1.05)))
        if upper < minimum:
            Toast.show(self.host, 'This volume has no safe resize range.', kind='warning')
            return
        content, handle = self._open_sheet(f'Resize {volume.name}',
                                      f'It can shrink to {size_text(minimum)} (what’s on it) or grow into '
                                      'free space next to it.',
                                      'dsk-resize-sheet')
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum / unit,
                                         max(minimum + unit, upper) / unit, 1)
        scale.set_accessible_role(Gtk.AccessibleRole.SLIDER)
        scale.set_value(round(volume.size / unit))
        scale.set_name('dsk-resize-size')
        amount = _text(size_text(volume.size), 'meta', weight=500)
        amount.set_hexpand(False)
        scale.connect('value-changed', lambda s: amount.set_text(size_text(round(s.get_value()) * unit)))
        size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        size_row.append(_text('Size', 'meta', weight=600))
        size_row.append(amount)
        content.append(_column(size_row, scale, spacing=6))
        footer = self._sheet_footer()
        footer.append(_button('', 'Cancel', handle.close))

        def resize() -> None:
            new_size = round(scale.get_value()) * unit
            handle.close()
            def confirmed(_also) -> None:
                if volume.source is None:
                    self._fixture_replace_volume(volume, size=new_size,
                                                 free=max(0, new_size - (volume.used or 0)))
                    Toast.show(self.host, f'{volume.name} is now {size_text(new_size)}.')
                else:
                    self._run_write(lambda: partitions.resize(self.client, drive.source, plan,
                                                               volume.source, new_size),
                                    f'{volume.name} is now {size_text(new_size)}.')

            self._confirm(title=f'Resize {volume.name}?',
                                  body=(f'The partition will change from {size_text(volume.size)} to '
                                        f'{size_text(new_size)}. Back up its files before continuing.'),
                                  action='Resize', icon='arrow-left-right', on_confirm=confirmed)

        footer.append(_button('', 'Resize', resize, primary=True, name='dsk-resize-go'))
        content.append(footer)

    def _delete_volume(self, volume: VolumeView, drive: DriveView) -> None:
        if volume.protected or volume.read_only:
            Toast.show(self.host, 'This system or read-only volume cannot be deleted.', kind='error')
            return

        def confirm(plan) -> None:
            self._confirm(title=f'Delete {volume.name}?',
                                  body=('The volume and everything on it will be gone, and '
                                        f'{size_text(volume.size)} becomes free space.'),
                                  action='Delete', icon='trash-2',
                                  on_confirm=lambda _also: self._confirmed_delete(volume, drive, plan))

        if volume.source is None:
            confirm(None)
        else:
            self._read(lambda: partitions.inspect(self.client, drive.source), confirm)

    def _confirmed_delete(self, volume: VolumeView, drive: DriveView, plan) -> None:
        if volume.source is None:
            remaining = tuple(v for v in drive.volumes if v.key != volume.key)
            self._apply(tuple(replace(d, volumes=remaining) if d.key == drive.key else d
                              for d in self.drives))
            Toast.show(self.host, f'{volume.name} was deleted.', kind='deleted')
        else:
            self._run_write(lambda: partitions.delete(self.client, drive.source, plan,
                                                       volume.source, volume.name),
                            f'{volume.name} was deleted.')

    def _create_volume(self, space: VolumeView, drive: DriveView) -> None:
        if space.tone != 'free':
            return
        if drive.source is None:
            self._create_form(space, drive, None)
        else:
            self._read(lambda: partitions.inspect(self.client, drive.source),
                       lambda plan: self._create_form(space, drive, plan))

    def _create_form(self, space: VolumeView, drive: DriveView, plan) -> None:
        selected_gap = None
        if plan is not None:
            selected_gap = next((gap for gap in plan.gaps if gap[0] == space.free_offset), None)
            if selected_gap is None:
                Toast.show(self.host, 'The selected free area changed. Select it again.', kind='warning')
                return
        available = selected_gap[1] if selected_gap is not None else space.size
        content, handle = self._open_sheet('New volume',
                                      f'Make a volume from {size_text(available)} of free space.',
                                      'dsk-create-sheet')
        field = self._text_field('Name', value=space.name)
        field.set_name('dsk-create-name')
        content.append(field)
        size_units = max(1, round(available / 1_000_000_000))
        size_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        size_line.append(_text('Size', 'meta', weight=600))
        size_value = _text(f'{size_text(available)} of {size_text(available)}', 'caption')
        size_value.set_hexpand(False)
        size_line.append(size_value)
        size_line.set_margin_top(15)
        content.append(size_line)
        size_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, size_units, 1)
        size_scale.set_accessible_role(Gtk.AccessibleRole.SLIDER)
        size_scale.set_draw_value(False)
        size_scale.set_value(size_units)
        size_scale.set_name('dsk-create-size')
        size_scale.update_property([Gtk.AccessibleProperty.LABEL], ['Size'])
        size_scale.connect('value-changed', lambda control: size_value.label.set_text(
            f'{size_text(int(available * control.get_value() / size_units))} of {size_text(available)}'))
        size_scale.set_margin_top(5)
        content.append(size_scale)
        choices, selected_format = _format_choices(panel=self._bar_ready())
        if not self._bar_ready(): choices.set_margin_top(12)
        content.append(choices)
        protect = Switch(big=True, active=False)
        protect.set_name('dsk-create-protect')
        protect_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        protect_row.add_css_class('disks-protect-row')
        protect_copy = _column(_text('Protect with a password', 'body', weight=700),
                               _text('Encrypted with LUKS. You’ll need it every time you plug this in.',
                                     'caption', wrap=True), spacing=2)
        protect_row.append(protect_copy)
        protect_row.append(protect)
        if not self._bar_ready(): protect_row.set_margin_top(12)
        content.append(protect_row)
        phrase = self._text_field('Passphrase', purpose='password')
        repeat = self._text_field('Confirm passphrase', purpose='password')
        phrase.set_visible(False)
        repeat.set_visible(False)
        protect.connect('notify::active', lambda switch, _pspec: (
            phrase.set_visible(switch.get_active()), repeat.set_visible(switch.get_active())))
        content.append(phrase)
        content.append(repeat)
        footer = self._sheet_footer()
        footer.set_margin_top(16)
        footer.append(_button('', 'Cancel', handle.close))

        def create() -> None:
            name = field.text.strip() or space.name
            fmt = selected_format()
            secret = phrase.text if protect.get_active() else None
            if secret is not None and (len(secret) < 8 or secret != repeat.text):
                Toast.show(self.host, 'Enter matching passphrases of at least eight characters.',
                           kind='warning')
                return
            phrase.text = repeat.text = ''
            size = min(available, int(available * size_scale.get_value() / size_units))
            size = size // partitions.MIB * partitions.MIB
            if size < partitions.MIB:
                Toast.show(self.host, 'Choose a larger volume size.', kind='warning')
                return
            handle.close()
            def confirmed(_also) -> None:
                if drive.source is None:
                    new = VolumeView(f'{drive.key}:{len(drive.volumes)}', name, 'green', size,
                                     0, size, FORMATS[fmt], None, drive.device, '',
                                     'A new volume. Mount it to start using it.',
                                     encrypted=secret is not None, locked=secret is not None)
                    self._apply(tuple(replace(d, volumes=d.volumes + (new,)) if d.key == drive.key else d
                                      for d in self.drives))
                    Toast.show(self.host, f'{name} created.', kind='added')
                else:
                    offset, _length = selected_gap
                    self._run_write(lambda: partitions.create(self.client, drive.source, plan,
                                                               offset, size, fmt, name,
                                                               encrypt_passphrase=secret),
                                    f'{name} created.')

            self._confirm(title=f'Create {name}?',
                                  body=(f'A {size_text(size)} {FORMATS[fmt]} partition will be created '
                                        f'on {drive.name}. Check that the selected free space is correct.'),
                                  action='Create', icon='plus', on_confirm=confirmed)

        footer.append(_button('', 'Create', create, primary=True, name='dsk-create-go'))
        content.append(footer)

    def _remove_disk(self, drive: DriveView) -> None:
        def confirmed(_also) -> None:
            if drive.source is None:
                self._apply(tuple(d for d in self.drives if d.key != drive.key))
                Toast.show(self.host, f'{drive.name} is powered off.')
            else:
                self._run_write(lambda: self.client.remove_disk(drive.source),
                                f'{drive.name} is powered off.')

        self._confirm(title=f'Power off {drive.name}?',
                              body='Unmount its volumes and finish any file copies before powering it off.',
                              action='Power off', icon='power', on_confirm=confirmed)

    def _choose_file(self, title: str, action: Gtk.FileChooserAction, done) -> None:
        accept = 'Save' if action == Gtk.FileChooserAction.SAVE else 'Open'
        chooser = Gtk.FileChooserNative.new(title, self, action, accept, 'Cancel')

        def selected(dialog, response) -> None:
            path = dialog.get_file().get_path() if response == Gtk.ResponseType.ACCEPT and dialog.get_file() else None
            dialog.destroy()
            if path:
                done(path)

        chooser.connect('response', selected)
        chooser.show()

    def _attach_image(self) -> None:
        if self.fixture:
            if any(d.key == 'iso' for d in self.drives):
                self.drive_key, self.volume_key = 'iso', 'iso:0'
                self._render()
                return
            image = VolumeView('iso:0', 'LUMA-1.0', 'amber', 6_200_000_000,
                               6_100_000_000, 100_000_000, 'ISO 9660',
                               '/run/media/nick/LUMA-1.0', '/dev/loop0p1',
                               '2026-09-23-18-00-00-00', 'The contents of the image, read-only.',
                               read_only=True)
            drive = DriveView('iso', 'luma-nightly.iso', 'Disk images', 'iso',
                              'Disk image, 6.2 GB', '/dev/loop0', 6_200_000_000,
                              'MBR', 'none', 'Not reported', (),
                              (image,), speed_read=2900, speed_write=0)
            self.drive_key, self.volume_key = 'iso', 'iso:0'
            self._apply(self.drives + (drive,))
            Toast.show(self.host, 'Attached luma-nightly.iso read-only')
            return
        self._choose_file('Attach a disk image', Gtk.FileChooserAction.OPEN,
                          lambda path: self._run_write(lambda: self.client.attach_image(path),
                                                       f'Attached {Path(path).name} read-only'))

    def _save_image(self, drive: DriveView, volume: VolumeView | None = None) -> None:
        if self.fixture:
            Toast.show(self.host, 'Choose where to save the image.', kind='notified')
            return
        if drive.source is None or (volume is not None and volume.source is None):
            return

        def chosen(path: str) -> None:
            if Path(path).exists():
                Toast.show(self.host, 'Choose a new file. Existing images are never replaced.', kind='warning')
                return
            self.cancel.clear()
            source = volume.source if volume is not None else drive.source
            save = self.client.save_partition_image if volume is not None else self.client.save_image
            self._run_write(lambda: save(source, path, lambda _fraction: None, self.cancel),
                            f'Saved an image of {volume.name if volume else drive.name}.')

        self._choose_file(f'Create image of {volume.name if volume else drive.name}',
                          Gtk.FileChooserAction.SAVE, chosen)

    def _restore_image(self, drive: DriveView, volume: VolumeView | None = None) -> None:
        if self.fixture:
            Toast.show(self.host, 'Choose an image and a backup location.', kind='notified')
            return
        target = volume.source if volume is not None else drive.source
        if target is None:
            return

        def image_chosen(image: str) -> None:
            def backup_chosen(backup: str) -> None:
                if Path(backup).exists():
                    Toast.show(self.host, 'Choose a new backup file.', kind='warning')
                    return
                name = volume.name if volume else drive.name
                self._confirm(
                    title=f'Restore image to {name}?',
                    body=(f'{name} will first be backed up to {backup}. Then every byte on it '
                          f'will be replaced by {Path(image).name}. This cannot be undone.'),
                    action='Restore', icon='rotate-ccw',
                    on_confirm=lambda _also: self._begin_restore(target, image, backup, name))
            self._choose_file('Save a complete backup before restoring',
                              Gtk.FileChooserAction.SAVE, backup_chosen)

        self._choose_file('Choose the image to restore', Gtk.FileChooserAction.OPEN, image_chosen)

    def _begin_restore(self, target, image: str, backup: str, name: str) -> None:
        self.cancel.clear()
        self._run_write(lambda: self.client.restore_image(target, image, backup,
                                                          lambda _fraction: None, self.cancel),
                        f'{name} was restored. Its previous contents are in {backup}.')

    def _drive_settings(self, drive: DriveView) -> None:
        if drive.source is None:
            self._drive_settings_form(drive, (10, True, True, True))
        else:
            self._read(lambda: self.client.drive_settings(drive.source),
                       lambda values: self._drive_settings_form(drive, values))

    def _drive_settings_form(self, drive: DriveView, values) -> None:
        minutes, cache_on, can_standby, can_cache = values
        content, handle = self._open_sheet('Drive settings',
                                      f'{drive.name} · Changes apply when this drive connects.',
                                      'dsk-settings-sheet')
        standby = Switch(big=True, active=minutes == 10)
        standby.set_sensitive(can_standby)
        standby_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        standby_row.append(_text('Standby after 10 minutes', 'body'))
        standby_row.append(standby)
        content.append(standby_row)
        cache = Switch(big=True, active=bool(cache_on))
        cache.set_sensitive(can_cache)
        cache_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cache_row.append(_text('Write cache on', 'body'))
        cache_row.append(cache)
        content.append(cache_row)
        footer = self._sheet_footer()
        footer.append(_button('', 'Cancel', handle.close))

        def save() -> None:
            standby_minutes = 10 if standby.get_active() else 0
            write_cache = cache.get_active()
            handle.close()
            def confirmed(_also) -> None:
                if drive.source is None:
                    Toast.show(self.host, 'Drive settings saved in this fixture.')
                else:
                    self._run_write(lambda: self.client.set_drive_settings(
                        drive.source, standby_minutes=standby_minutes,
                        write_cache=write_cache), 'Drive settings saved.')

            self._confirm(title=f'Change settings for {drive.name}?',
                                  body='These settings affect how the drive powers down and caches writes.',
                                  action='Save settings', icon='settings-2', on_confirm=confirmed)

        footer.append(_button('', 'Save', save, primary=True, name='dsk-settings-save'))
        content.append(footer)

    def _mount_options(self, volume: VolumeView) -> None:
        if volume.source is None:
            self._mount_options_form(volume, (volume.mount or f'/mnt/{volume.name}',
                                              False, False, True, None))
        else:
            self._read(lambda: self.client.mount_configuration(volume.source),
                       lambda values: self._mount_options_form(volume, values))

    def _mount_options_form(self, volume: VolumeView, values) -> None:
        directory, startup, readonly, visible, _item = values
        content, handle = self._open_sheet(f'Mount options for {volume.name}',
                                      'Choose when and where this volume is mounted.',
                                      'dsk-mount-options-sheet')
        path = self._text_field('Mount directory', value=directory or f'/mnt/{volume.name}')
        content.append(path)
        toggles = []
        for label, state in (('Mount at startup', startup), ('Read-only', readonly),
                             ('Show in Filer', visible)):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(_text(label, 'body'))
            switch = Switch(big=True, active=state)
            row.append(switch)
            content.append(row)
            toggles.append(switch)
        footer = self._sheet_footer()
        footer.append(_button('', 'Cancel', handle.close))

        def save() -> None:
            directory = path.text.strip()
            if not directory.startswith('/') or directory == '/':
                Toast.show(self.host, 'Choose a full mount directory.', kind='warning')
                return
            startup_new, readonly_new, visible_new = (switch.get_active() for switch in toggles)
            handle.close()
            def confirmed(_also) -> None:
                if volume.source is None:
                    Toast.show(self.host, 'Mount options saved in this fixture.')
                else:
                    self._run_write(lambda: self.client.set_mount_configuration(
                        volume.source, directory=directory, at_startup=startup_new,
                        read_only=readonly_new, show_in_filer=visible_new),
                        'Mount options saved.')

            self._confirm(title=f'Change mount options for {volume.name}?',
                                  body=(f'This changes where and when {volume.name} is mounted. '
                                        'Its current mount configuration will be backed up first.'),
                                  action='Save options', icon='settings-2', on_confirm=confirmed)

        footer.append(_button('', 'Save', save, primary=True, name='dsk-mount-options-save'))
        content.append(footer)

    def _format(self, volume: VolumeView, drive: DriveView) -> None:
        if volume.protected or volume.read_only:
            Toast.show(self.host, 'This system or read-only volume cannot be erased.', kind='error')
            return
        body, handle = self._open_sheet(f'Format {volume.name}', 'Formatting erases everything on this volume.',
                                   'dsk-format-sheet')
        subtitle = body.get_first_child().get_next_sibling()
        if not self._bar_ready(): subtitle.set_margin_top(6)
        name = self._text_field('Name', value=volume.name)
        name.set_name('dsk-format-name')
        if not self._bar_ready(): name.set_margin_top(6)
        body.append(name)
        choices, selected_format = _format_choices(panel=self._bar_ready())
        if not self._bar_ready(): choices.set_margin_top(14)
        body.append(choices)
        protect = Switch(big=True, active=False)
        protect.set_name('dsk-format-protect')
        protect_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        protect_row.add_css_class('disks-protect-row')
        protect_row.append(_column(_text('Protect with a password', 'body', weight=700),
                                   _text('Encrypted with LUKS. You’ll need it every time you plug this in.',
                                         'caption', wrap=True), spacing=2))
        protect_row.append(protect)
        if not self._bar_ready(): protect_row.set_margin_top(6)
        body.append(protect_row)
        phrase = self._text_field('Passphrase', purpose='password')
        repeat = self._text_field('Confirm passphrase', purpose='password')
        phrase.set_visible(False)
        repeat.set_visible(False)
        protect.connect('notify::active', lambda switch, _pspec: (
            phrase.set_visible(switch.get_active()), repeat.set_visible(switch.get_active())))
        body.append(phrase)
        body.append(repeat)
        erase_fine = _text(_erase_explanation(volume.size, drive.speed_write, 'quick'),
                           'caption', wrap=True)
        erase_mode = ModeSwitch((('quick', 'Quickly'), ('zero', 'Overwrite with zeros')),
                                current='quick', label='Erase', labels_only=True,
                                on_change=lambda mode: erase_fine.label.set_text(
                                    _erase_explanation(volume.size, drive.speed_write, mode)))
        erase_mode.set_name('dsk-erase-mode')
        erase_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        phone = (self.get_width() or self.get_default_size()[0]) <= lumaui_tokens.PHONE_MAX_WIDTH
        erase_row.set_margin_top(12 if phone else 14)
        erase_row.append(_text('Erase', 'meta', weight=600))
        erase_row.append(erase_mode)
        body.append(erase_row)
        erase_fine.set_margin_top(6)
        body.append(erase_fine)
        footer = self._sheet_footer()
        footer.set_margin_top(17)
        footer.append(_button('', 'Cancel', handle.close, name='dsk-format-cancel'))

        def next_step() -> None:
            fmt = selected_format()
            new_name = name.text.strip() or volume.name
            zero = erase_mode.current == 'zero'
            secret = phrase.text if protect.get_active() else None
            if secret is not None and (len(secret) < 8 or secret != repeat.text):
                Toast.show(self.host, 'Enter matching passphrases of at least eight characters.', kind='warning')
                return
            phrase.text = repeat.text = ''
            handle.close()
            consequence = (f'Everything on {volume.name} ({size_text(volume.size)}) on {drive.name} '
                           f'will be gone. {"It will be unmounted first. " if volume.mount else ""}'
                           'This cannot be undone.')
            # TODO(kit-request disks-24-typed-destructive-confirmation): require
            # the exact volume name in the shared confirmation sheet.
            self._confirm(title=f'Erase {volume.name}?', body=consequence,
                                  action='Erase', icon='trash-2',
                                  on_confirm=lambda _also: self._confirmed_format(volume, fmt, new_name, zero,
                                                                                  secret))

        footer.append(_button('', 'Continue…', next_step, danger=True,
                              name='dsk-format-continue'))
        body.append(footer)
        # v70 focuses the name field without selecting its current value.
        def focus_name() -> bool:
            name.grab_focus()
            name.entry.set_position(-1)
            return False

        GLib.timeout_add(80, focus_name)

    def _format_disk(self, drive: DriveView) -> None:
        if drive.group == 'In this computer':
            Toast.show(self.host, 'The running system disk cannot be formatted.', kind='warning')
            return
        body, handle = self._open_sheet(f'Format all of {drive.name}',
                                   'Every volume on this drive will be erased and replaced with one new volume.',
                                   'dsk-format-disk-sheet')
        selected_volume = self._volume()
        name = self._text_field('Name', value=selected_volume.name if selected_volume else drive.name)
        body.append(name)
        choices, selected_format = _format_choices(panel=self._bar_ready())
        body.append(choices)
        protect = Switch(big=True, active=False)
        password = self._text_field('Passphrase', purpose='password')
        repeat = self._text_field('Confirm passphrase', purpose='password')
        password.set_visible(False)
        repeat.set_visible(False)
        protect.connect('notify::active', lambda switch, _pspec: (
            password.set_visible(switch.get_active()), repeat.set_visible(switch.get_active())))
        protect_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        protect_row.add_css_class('disks-protect-row')
        protect_row.append(_column(_text('Protect with a password', 'body', weight=700),
                                   _text('Encrypted with LUKS. You’ll need it every time you plug this in.',
                                         'caption', wrap=True), spacing=2))
        protect_row.append(protect)
        body.append(protect_row)
        body.append(password)
        body.append(repeat)
        erase_fine = _text(_erase_explanation(drive.size, drive.speed_write, 'quick'),
                           'caption', wrap=True)
        erase = ModeSwitch((('quick', 'Quickly'), ('zero', 'Overwrite with zeros')),
                           current='quick', label='Erase', labels_only=True,
                           on_change=lambda mode: erase_fine.label.set_text(
                               _erase_explanation(drive.size, drive.speed_write, mode)))
        erase_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        erase_row.append(_text('Erase', 'meta', weight=600))
        erase_row.append(erase)
        body.append(erase_row)
        body.append(erase_fine)
        footer = self._sheet_footer()
        footer.append(_button('', 'Cancel', handle.close))

        def next_step() -> None:
            label = name.text.strip() or drive.name
            phrase = password.text if protect.get_active() else None
            if phrase is not None and (len(phrase) < 8 or phrase != repeat.text):
                Toast.show(self.host, 'Enter matching passphrases of at least eight characters.',
                           kind='warning')
                return
            password.text = repeat.text = ''
            fmt, zero = selected_format(), erase.current == 'zero'
            handle.close()

            def confirm(backup: str) -> None:
                # TODO(kit-request disks-24-typed-destructive-confirmation):
                # validate the drive name in the shared confirmation sheet.
                self._confirm(
                    title=f'Erase {drive.name}?',
                    body=(f'A complete backup will be saved to {backup} before {drive.name} is erased. '
                          'Unmount every volume first. This cannot be undone.'),
                    action='Erase', icon='trash-2',
                    on_confirm=lambda _also: self._confirmed_format_disk(
                        drive, backup, fmt, label, zero, phrase))

            if self.fixture:
                confirm('fixture backup')
            else:
                self._choose_file('Save a complete backup before formatting',
                                  Gtk.FileChooserAction.SAVE, confirm)

        footer.append(_button('', 'Continue…', next_step, danger=True,
                              name='dsk-format-disk-continue'))
        body.append(footer)

    def _confirmed_format_disk(self, drive: DriveView, backup: str, fmt: str,
                               label: str, zero: bool, phrase: str | None) -> None:
        if drive.source is None:
            new = VolumeView(f'{drive.key}:0', label, 'green', drive.size, 0, drive.size,
                             FORMATS[fmt], None, drive.device, '',
                             'Freshly formatted and empty.', encrypted=phrase is not None)
            self.volume_key = new.key
            self._apply(tuple(replace(d, volumes=(new,)) if d.key == drive.key else d
                              for d in self.drives))
            Toast.show(self.host, f'{drive.name} was formatted.')
        else:
            self.cancel.clear()
            self._run_write(lambda: self.client.format_disk(
                drive.source, backup, drive.name, fmt, label, lambda _fraction: None,
                self.cancel, zero=zero, encrypt_passphrase=phrase),
                f'{drive.name} was formatted. Its backup is in {backup}.')

    def _confirmed_format(self, volume: VolumeView, fmt: str, new_name: str, zero: bool,
                          secret: str | None = None) -> None:
        if volume.source is None:
            self._fixture_replace_volume(volume, name=new_name, format=FORMATS[fmt], used=0,
                                         free=volume.size, mount=None, encrypted=secret is not None,
                                         locked=False)
            Toast.show(self.host, f'{new_name} was formatted.')
            return
        self._run_write(lambda: self.client.erase(volume.source, volume.name, fmt, new_name,
                                                  zero=zero, encrypt_passphrase=secret),
                        f'{volume.name} was formatted.')

    def _fixture_replace_volume(self, original: VolumeView, **changes) -> None:
        drive = self._drive()
        if drive is None:
            return
        volumes = tuple(replace(v, **changes) if v.key == original.key else v for v in drive.volumes)
        self._apply(tuple(replace(d, volumes=volumes) if d.key == drive.key else d for d in self.drives))

    def _open_sheet(self, title: str, subtitle: str, name: str):
        if self._bar_ready():
            return self._open_bar_sheet(title, subtitle, name)
        is_format = name == 'dsk-format-sheet'
        is_create = name == 'dsk-create-sheet'
        content = _column(_text(title, 'section-title'), _text(subtitle, 'body', wrap=True),
                          spacing=0 if is_format or is_create else 12)
        if is_create:
            content.get_first_child().get_next_sibling().set_margin_top(12)
        if is_format:
            # v70's format card has a 20 px sheet inset. Its footer can enter
            # the card's bottom padding, so the scroll viewport reaches there.
            scroll = Gtk.ScrolledWindow(child=content)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_propagate_natural_height(True)
            scroll.set_max_content_height(max(300, self.split.get_height() - 63))
            card = _disks_card(scroll, name, small=True)
            padded = scroll
        else:
            card = _disks_card(content, name, small=True)
            padded = content
        card.add_css_class('disks-sheet')
        padded.set_margin_top(22)
        padded.set_margin_start(22)
        padded.set_margin_end(22)
        padded.set_margin_bottom(0 if is_format else 18)
        def layout_sheet(*_args) -> None:
            phone = self.split.get_collapsed()
            width = self.get_width() or self.get_default_size()[0]
            if phone:
                card.set_margin_start(28)
                card.set_margin_end(28)
                card.set_size_request(max(0, width - 56), -1)
                # The phone format sheet sits 20 px below the content edge.
                card.set_margin_bottom(8 if is_format else 0)
            else:
                card.set_margin_start(204)
                card.set_margin_end(0)
                card.set_size_request(480, -1)
                card.set_margin_bottom(18 if is_format else 0)
            if is_format:
                scroll.set_max_content_height(max(300, self.split.get_height() - 63))

        layout_sheet()
        layout_signal = self.split.connect('notify::collapsed', layout_sheet)

        def disconnected(widget, _pspec) -> None:
            if widget.get_root() is None:
                self.split.disconnect(layout_signal)

        card.connect('notify::root', disconnected)
        handle = self.layer_host.present_modal(card, drawer=False)
        return content, handle

    def _open_bar_sheet(self, title: str, subtitle: str, name: str):
        """v71 `.dskpan`: on a phone a dialog grows the bar instead of floating a card."""
        heading = _text(title, 'form_title')
        heading.set_margin_top(2)
        heading.set_margin_start(4); heading.set_margin_end(4)
        description = _text(subtitle, 'body', wrap=True)
        description.add_css_class('disks-panel-subtitle')
        description.set_margin_start(4); description.set_margin_end(4)
        content = _column(heading, description, spacing=10)
        content.set_name(name)
        content.add_css_class('disks-panel')
        self._sheet_key = name
        window = self

        class BarSheet:
            def close(self, *_args) -> None:
                if window._sheet_key == name:
                    window._close_sheet()

        self.actions.grow(name, content, panel_padding=False, on_fold=lambda: setattr(
            self, '_sheet_key', None if self._sheet_key == name else self._sheet_key))
        return content, BarSheet()

    def _health_sheet(self, drive: DriveView) -> None:
        content, handle = self._open_sheet('SMART data', f'{drive.name} · {drive.health_title}',
                                      'dsk-health-sheet')
        content.set_spacing(0)
        subtitle = content.get_first_child().get_next_sibling()
        if not self._bar_ready(): subtitle.set_margin_top(6)
        subtitle.set_margin_bottom(16)
        rows = drive.health_rows
        if self.fixture and drive.kind != 'usb':
            rows = (
                ('Reallocated sectors', '3' if drive.key == 'ext' else '0', drive.key == 'ext'),
                ('Pending sectors', '0', False),
                ('Power-on hours', '53,420' if drive.key == 'ext' else '20,512', False),
                ('Temperature', '44 °C' if drive.key == 'ext' else '38 °C', False),
                ('Unsafe shutdowns', '12', False),
                ('Media errors', '0', False),
            )
        if rows:
            table = _column(spacing=0)
            heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            heading.add_css_class('disks-smart-heading')
            heading.append(_text('SMART attribute', 'caption', weight=600))
            value_head = _text('Value', 'caption', weight=600)
            value_head.set_size_request(86, -1)
            value_head.set_hexpand(False)
            heading.append(value_head)
            heading.append(Gtk.Box(width_request=102))
            table.append(heading)
            for label, value, warning in rows:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.add_css_class('disks-smart-row')
                row.append(_text(label, 'body'))
                reading = _text(value, 'body')
                reading.set_size_request(86, -1)
                reading.set_hexpand(False)
                row.append(reading)
                mark = _text('Watch' if warning else 'OK', 'caption', weight=650)
                mark.label.add_css_class('disks-smart-watch' if warning else 'disks-smart-ok')
                mark.label.set_halign(Gtk.Align.START)
                mark.set_size_request(102, -1)
                mark.set_hexpand(False)
                row.append(mark)
                table.append(row)
            content.append(table)
        else:
            content.append(_text('This drive does not report SMART data.', 'body'))
        footer = self._sheet_footer()
        footer.set_margin_top(18)
        footer.append(_button('', 'Close', handle.close, name='dsk-health-close'))
        if drive.health_state != 'none':
            def selftest() -> None:
                if drive.source is None:
                    Toast.show(self.host, 'Short self-test passed.')
                else:
                    self._run_write(lambda: self.client.start_selftest(drive.source),
                                    'Short self-test started.')
            footer.append(_button('', 'Run a short self-test', selftest, primary=True,
                                  name='dsk-self-test'))
        content.append(footer)

    def _speed(self) -> None:
        drive, volume = self._drive(), self._volume()
        if drive is None or volume is None or volume.tone == 'free':
            return
        content, handle = self._open_sheet('Speed test',
                                      f'{drive.name} · 100 samples, files are not changed',
                                      'dsk-speed-sheet')
        graph = SpeedGraph()
        content.append(graph)
        figures = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        read_value = _text('— MB/s', 'title-2')
        write_value = _text('— MB/s', 'title-2')
        figures.append(_column(read_value, _text('read', 'caption')))
        figures.append(_column(write_value, _text('write', 'caption')))
        content.append(figures)
        footer = self._sheet_footer()
        footer.append(_button('', 'Close', handle.close, name='dsk-speed-close'))

        def progress(values: tuple[float, ...]) -> bool:
            graph.set_samples(values)
            if values:
                read_value.set_text(f'{sum(values) / len(values):.0f} MB/s')
            return False

        def start() -> None:
            if self.fixture:
                seed = drive.speed_read or 0
                progress(tuple(seed * (0.88 + 0.12 * (i % 7) / 6) for i in range(60)))
                if drive.speed_write:
                    writes = tuple(drive.speed_write * (0.8 + 0.2 * (i % 5) / 4)
                                   for i in range(60))
                    graph.set_samples(graph.read, writes)
                    write_value.set_text(f'{sum(writes) / len(writes):.0f} MB/s')
                return
            if self.client is None or volume.source is None or self._busy:
                return
            self._busy = True
            self.cancel.clear()

            def worker():
                try:
                    rate, _latency = self.client.benchmark(
                        volume.source,
                        lambda samples: GLib.idle_add(progress, tuple(v / 1_000_000 for v in samples)),
                        self.cancel)
                except Exception as error:
                    GLib.idle_add(self._write_done, explain_error(error), '')
                else:
                    GLib.idle_add(self._write_done, None, f'Read speed: {rate / 1_000_000:.0f} MB/s')

            self.executor.submit(worker)

        footer.append(_button('', 'Start', start, primary=True, name='dsk-speed-start'))
        content.append(footer)

    def _on_close(self, *_args) -> bool:
        self._closed = True
        self._remembered_passphrases.clear()
        self.cancel.set()
        if self.client:
            self.client.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        return False


class DisksApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_lumaui()
        add_style_sheet(str(Path(__file__).resolve().parents[1] / 'style/disks.css'))

    def do_activate(self) -> None:
        (self.props.active_window or DisksWindow(self)).present()


def main() -> int:
    return DisksApplication().run([])


if __name__ == '__main__':
    raise SystemExit(main())
