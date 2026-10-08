# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import math
import json
import time
import os
from pathlib import Path
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango
from luma_appkit import (AppWindow, Island, Toolbar, StatusBar, EmptyState,
                        Command, CommandGroup, CommandRegistry, command_menu_model,
                        add_style_sheet, install_appkit)
from .model import Sampler, filesystems
from .integration import DesktopCatalog, battery_info, inhibitors, quit_row
from . import capability
from .preferences import read_preferences, update_preferences

APP_ID = 'io.luma.Monitor.LumaUIPreview' if os.environ.get('LUMA_MONITOR_PREVIEW') else 'io.luma.Monitor'
ICON_NAME = 'io.luma.Monitor'
MACHINE = 'machine'
COLUMNS = {
    'cpu': [('App', 'name'), ('% CPU', 'cpu'), ('Time awake', 'age')],
    'memory': [('App', 'name'), ('Memory', 'memory'), ('Cached', 'cached')],
    'disk': [('App', 'name'), ('Read', 'reading'), ('Written', 'writing')],
    'network': [('App', 'name'), ('Received', 'receiving'), ('Sent', 'sending')],
    'energy': [('App', 'name'), ('Impact', 'impact'), ('Preventing sleep', 'inhibits')],
    'filesystems': [('Volume', 'name'), ('Mounted at', 'mount'), ('Type', 'type'), ('Used', 'used'), ('Free', 'free')],
}
# Tabs in the order they appear. "This machine" is not a table of processes:
# it is one row per capability the machine either has or does not.
TABS = (('cpu', 'CPU'), ('memory', 'Memory'), ('disk', 'Disk'), ('network', 'Network'),
        ('energy', 'Energy'), ('filesystems', 'File systems'), (MACHINE, 'This machine'))


def size(value):
    if value is None:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(value) < 1024 or unit == 'TB':
            return f'{value:.1f} {unit}' if unit in ('GB', 'TB') else f'{value:.0f} {unit}'
        value /= 1024


def rate_size(value):
    if value is None: return '—'
    value = max(0, value)
    if value < 1024 ** 2: return f'{value / 1024:.0f} KB/s'
    value /= 1024 ** 2
    for unit in ('MB/s', 'GB/s', 'TB/s'):
        if value < 1024 or unit == 'TB/s': return f'{value:.1f} {unit}'
        value /= 1024


def duration(value):
    if value is None:
        return '—'
    minutes = max(0, int(value) // 60)
    if minutes >= 1440: return f'{minutes // 1440} d {minutes // 60 % 24} h'
    return f'{minutes // 60} h {minutes % 60} m' if minutes >= 60 else f'{minutes} m'


def cell_value(row, key):
    value = row.get(key)
    if value is None:
        return '—'
    if key in ('memory', 'cached', 'free'):
        return size(value)
    if key in ('reading', 'writing', 'receiving', 'sending'):
        return rate_size(value)
    if key == 'impact': return f'{value:.1f}'
    if key in ('cpu', 'used'):
        return f'{value:.1f}%'
    if key == 'age':
        return duration(value)
    if key == 'inhibits':
        return 'Yes' if value else 'No'
    return str(value)


class Row(GObject.Object):
    payload = GObject.Property(type=object)
    def __init__(self, data):
        super().__init__()
        self.payload = data


class HistoryGraph(Gtk.DrawingArea):
    def __init__(self):
        super().__init__(content_width=232, content_height=58)
        self.values, self.scale = [], 100
        self.caption = '100%'
        self.add_css_class('monitor-graph')
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Resource use over the last 60 seconds'])
        self.set_draw_func(self._draw)

    def _color(self, name, fallback):
        found, color = self.get_style_context().lookup_color(name)
        return (color.red, color.green, color.blue) if found else fallback

    def _draw(self, _area, cr, width, height):
        ink = self._color('luma_ink', (.8, .8, .8))
        muted = self._color('luma_muted', (.5, .5, .5))
        cr.set_source_rgba(*ink, .10)
        cr.set_line_width(1)
        cr.move_to(0, height / 2); cr.line_to(width, height / 2); cr.stroke()
        valid = [(i, v) for i, v in enumerate(self.values) if v is not None]
        # Missing samples break the line instead of manufacturing a zero dip.
        runs, current = [], []
        for index, value in enumerate(self.values):
            if value is None:
                if current: runs.append(current)
                current = []
            else:
                current.append(((60 - len(self.values) + index) * width / 59,
                                height - min(1, max(0, value / max(self.scale, 1))) * height))
        if current: runs.append(current)
        for points in runs:
            cr.move_to(points[0][0], height)
            for x, y in points: cr.line_to(x, y)
            cr.line_to(points[-1][0], height); cr.close_path()
            cr.set_source_rgba(*ink, .10); cr.fill()
            cr.move_to(*points[0])
            for point in points[1:]: cr.line_to(*point)
            cr.set_source_rgb(*ink); cr.set_line_width(1.5); cr.stroke()
        cr.set_source_rgb(*muted)
        cr.select_font_face('Figtree'); cr.set_font_size(9)
        extent = cr.text_extents(self.caption)
        cr.move_to(max(4, width - extent[2] - 6), 12); cr.show_text(self.caption)
        cr.move_to(6, height - 5); cr.show_text('60 s')


class MachineView(Gtk.Stack):
    """What this machine can do, one row per capability (ADR-048).

    Every row is the full list, always, even when nothing is wrong: the point
    of the view is seeing what your computer can do, not only what is broken.
    Monitor owns none of the words here — title, promise and remedy are read
    from the report so that this window and `luma-capability` in a terminal
    cannot drift apart.
    """

    def __init__(self, on_check=None):
        super().__init__(vexpand=True)
        self.report = None
        self.on_check = on_check
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.rows.add_css_class('monitor-capabilities')
        self.rows.update_property([Gtk.AccessibleProperty.LABEL], ['What this machine can do'])
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.rows)
        self.add_named(scroll, 'list')
        primary = ('Check now', self._check) if capability.can_check() else None
        self.unchecked = EmptyState(capability.NOT_CHECKED_YET,
                                    f'{capability.CHECK_COMMAND} does the same in a terminal.',
                                    'view-refresh-symbolic', primary=primary)
        self.add_named(self.unchecked, 'unchecked')
        self.set_visible_child_name('unchecked')

    def _check(self):
        if self.on_check:
            self.on_check()

    def load(self, path=None):
        self.show(capability.Report(path))

    def show(self, report):
        self.report = report
        while self.rows.get_first_child():
            self.rows.remove(self.rows.get_first_child())
        if not report.present or not report.capabilities:
            self.set_visible_child_name('unchecked')
            return
        for row in report.capabilities:
            self.rows.append(self._row(row))
        self.set_visible_child_name('list')

    @staticmethod
    def _row(row):
        box = Gtk.Box(spacing=12)
        box.add_css_class('monitor-capability')
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                       valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=row.title, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        title.add_css_class('monitor-capability-title')
        text.append(title)
        if row.promise:
            promise = Gtk.Label(label=row.promise, xalign=0, wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR)
            promise.add_css_class('monitor-capability-promise')
            text.append(promise)
        note = row.note
        if note:
            # The remedy is the row's value: it is what a person acts on, or
            # pastes into a support conversation. It is selectable for exactly
            # that reason.
            remedy = Gtk.Label(label=note, xalign=0, wrap=True, selectable=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
            remedy.add_css_class('monitor-capability-note')
            remedy.add_css_class(f'monitor-capability-{row.tone}')
            text.append(remedy)
        box.append(text)
        state = Gtk.Label(label=row.state, xalign=1, valign=Gtk.Align.CENTER)
        state.add_css_class('monitor-state')
        state.add_css_class(f'monitor-state-{row.tone}')
        box.append(state)
        box.update_property([Gtk.AccessibleProperty.LABEL],
                            [f'{row.title}. {row.state}.' + (f' {note}' if note else '')])
        return box


class MonitorWindow(AppWindow):
    def __init__(self, application):
        self.tab, self.advanced, self.show_background, self.background_open = 'cpu', False, False, True
        preferences_folder='luma-monitor-preview' if APP_ID=='io.luma.Monitor.LumaUIPreview' else 'luma-monitor'
        self.preferences = Path(GLib.get_user_config_dir()) / preferences_folder / 'preferences.json'
        try:
            saved = read_preferences(self.preferences)
            if not isinstance(saved, dict): saved = {}
            self.advanced = saved.get('advanced') is True
            self.show_background = saved.get('background') is True
            self.tab = saved.get('tab') if saved.get('tab') in dict(TABS) else 'cpu'
            if self.tab == 'filesystems' and not self.advanced: self.tab = 'cpu'
        except (OSError, ValueError, TypeError):
            saved = {}
        self.pointer_inside, self.unfreeze_at = False, 0
        self.selected_id = None
        self.snapshot = None
        self.closed = False
        self.timer = 0
        self.working = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='monitor-sample')
        if Path('/.flatpak-info').exists():
            from .host_sampler import HostCatalog, HostSampler
            self.catalog, self.sampler = HostCatalog(), HostSampler()
        else:
            self.catalog = DesktopCatalog()
            self.sampler = Sampler(resolve=self.catalog.resolve)
        self.histories = {name: deque(maxlen=60) for name in ('cpu', 'memory', 'disk', 'network')}
        self.objects = {}
        self.sort_key, self.sort_desc = 'cpu', True
        commands = CommandRegistry((
            CommandGroup(None, (Command('process.quit', 'Quit selected', self.quit_selected, 'window-close-symbolic', enabled=lambda: self.tab not in ('filesystems', MACHINE) and self.selected_row() is not None and not self.selected_row().get('background')),), quick_actions=True),
            CommandGroup('VIEW', (
                Command('view.advanced', 'Show advanced', self.toggle_advanced, 'sliders-horizontal', shortcut=('Ctrl', 'Shift', 'A'), checked=lambda: self.advanced),
                Command('view.background', 'Show background processes', self.toggle_background, 'layers', checked=lambda: self.advanced or self.show_background),
            )),
            CommandGroup('MONITOR', (
                Command('monitor.about', 'About Monitor', self.about, 'info'),
                Command('monitor.quit', 'Quit Monitor', self.close, 'log-out', shortcut=('Ctrl', 'Q')),
            )),
        ))
        super().__init__(application=application, app_id=APP_ID, title='Monitor', subtitle='This computer',
                         icon_name=ICON_NAME, commands=commands,
                         default_width=max(360, min(4096, saved.get('width', 840))) if isinstance(saved.get('width', 840), int) else 840,
                         default_height=max(420, min(2160, saved.get('height', 580))) if isinstance(saved.get('height', 580), int) else 580,
                         minimum_width=360, minimum_height=420)
        self.add_css_class('luma-monitor')
        self.island = Island(); self.set_body(self.island)
        self.toolbar = Toolbar()
        self.segment = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.segment.add_css_class('luma-segment')
        self.segment.set_halign(Gtk.Align.CENTER)
        self.segment.set_hexpand(True)
        self.buttons = {}
        first = None
        for key, title in TABS:
            button = Gtk.ToggleButton(label=title)
            if first: button.set_group(first)
            else: first = button
            button.set_active(key == self.tab)
            button.connect('toggled', self._tab_changed, key)
            self.buttons[key] = button
            self.segment.append(button)
        self.buttons['filesystems'].set_visible(self.advanced)
        self.search = Gtk.SearchEntry(placeholder_text='Search', width_chars=14, max_width_chars=18)
        self.search.set_size_request(178, 28)
        self.search.update_property([Gtk.AccessibleProperty.LABEL], ['Search processes'])
        self.search.connect('search-changed', lambda *_: self.refresh_rows())
        self.search.connect('stop-search', lambda *_: self.search.set_text(''))
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._key_pressed)
        self.add_controller(keys)
        self.segment_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                                vscrollbar_policy=Gtk.PolicyType.NEVER,
                                                propagate_natural_height=True, hexpand=True)
        self.segment_scroll.set_child(self.segment)
        self.toolbar.append(self.segment_scroll); self.toolbar.append(self.search)
        # Checking again is the only thing this window can ask the machine to
        # do. It is a button somebody presses, never something Monitor does
        # on its own: nothing here nags, and nothing here leaves the machine.
        self.recheck = Gtk.Button(label='Check again')
        self.recheck.set_tooltip_text(f'The same as {capability.CHECK_COMMAND} in a terminal')
        self.recheck.connect('clicked', lambda *_: self.check_machine())
        self.recheck.set_visible(False)
        self.toolbar.append(self.recheck)
        self.advanced_button = Gtk.ToggleButton(label='Advanced', active=self.advanced)
        self.advanced_button.set_tooltip_text('Show system processes and advanced details')
        self.advanced_button.connect('toggled', lambda button: self.toggle_advanced()
                                     if button.get_active() != self.advanced else None)
        self.toolbar.append(self.advanced_button)
        self.island.append(self.toolbar)
        self.store = Gio.ListStore.new(Row)
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=False, can_unselect=True)
        self.selection.connect('notify::selected-item', self._selection_changed)
        self.table = Gtk.ColumnView(model=self.selection, show_row_separators=False, show_column_separators=False)
        self.table.add_css_class('monitor-table')
        self.table.update_property([Gtk.AccessibleProperty.LABEL], ['Applications and system processes'])
        click = Gtk.GestureClick(button=1)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect('pressed', self._table_click)
        self.table.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect('enter', lambda *_: self._pointer(True))
        motion.connect('leave', lambda *_: self._pointer(False))
        self.table.add_controller(motion)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_child(self.table)
        self.table_stack = Gtk.Stack(vexpand=True)
        self.table_stack.add_named(self.scroll, 'table')
        self.empty = EmptyState('', 'Try part of the app’s name.', 'system-search-symbolic', primary=('Clear', lambda: self.search.set_text('')))
        self.table_stack.add_named(self.empty, 'empty')
        self.machine = MachineView(on_check=self.check_machine)
        self.table_stack.add_named(self.machine, MACHINE)
        self.island.append(self.table_stack)
        self._columns()
        self.footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.footer.add_css_class('monitor-footer')
        self.figure_row = Gtk.Box(spacing=0)
        self.graph = HistoryGraph(); self.figure_row.append(self.graph)
        self.figures = Gtk.Box(homogeneous=True, hexpand=True)
        self.figure_labels = []
        for _ in range(3):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER, hexpand=True)
            box.add_css_class('monitor-figure')
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END); label.add_css_class('monitor-figure-label')
            value = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END); value.add_css_class('monitor-figure-value')
            box.append(label); box.append(value); self.figures.append(box)
            self.figure_labels.append((label, value))
        self.figure_row.append(self.figures); self.footer.append(self.figure_row)
        self.cores = Gtk.Box(spacing=12); self.cores.add_css_class('monitor-cores'); self.cores.set_visible(self.advanced)
        self.footer.append(self.cores)
        self.island.append(self.footer)
        self.status = StatusBar()
        self.status_left = Gtk.Label(label='—', xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.status_right = Gtk.Label()
        self.status.append(self.status_left); self.status.append(self.status_right)
        self.island.append(self.status)
        self.connect('map', self._mapped)
        self.connect('unmap', self._visibility_changed)
        self.connect('close-request', self._closing)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 639px'))
        compact.connect('apply', lambda *_: self._set_compact(True))
        compact.connect('unapply', lambda *_: self._set_compact(False))
        self.add_breakpoint(compact)
        self.catalog_monitor = Gio.AppInfoMonitor.get()
        self.catalog_handler = self.catalog_monitor.connect('changed', lambda *_: self.catalog.refresh())
        self.status_right.set_label('Advanced' if self.advanced else '')
        self.report_monitor = self.report_handler = None
        self._show_tab()
        self._adapt()

    def _key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_f, Gdk.KEY_F) and state & Gdk.ModifierType.CONTROL_MASK:
            if self.tab == MACHINE: return False
            self.search.grab_focus()
            return True
        if keyval == Gdk.KEY_Escape and self.search.get_text():
            self.search.set_text('')
            return True
        return False

    def _pointer(self, inside):
        self.pointer_inside = inside
        if not inside: self.unfreeze_at = time.monotonic() + 1

    def _adapt(self, *_):
        width = self.get_width() or self.get_default_size()[0]
        self._set_compact(width < 640)

    def _set_compact(self, compact):
        self.toolbar.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        self.figure_row.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        self.graph.set_hexpand(compact)
        self.cores.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        self.search.set_hexpand(compact)
        self.segment.set_halign(Gtk.Align.FILL if compact else Gtk.Align.CENTER)
        if compact: self.add_css_class('compact')
        else: self.remove_css_class('compact')

    def _show_tab(self):
        """Everything that differs between the process tables and the machine."""
        machine = self.tab == MACHINE
        self.footer.set_visible(not machine and self.tab != 'filesystems')
        self.search.set_visible(not machine)
        self.recheck.set_visible(machine and capability.can_check())
        if machine:
            self.refresh_machine()
        else:
            self.table_stack.set_visible_child_name('table')
        self._watch_report(machine)

    def _watch_report(self, watching):
        """Follow the report while the view is on screen, and not otherwise.

        The report changes after a deployment, not second by second, so it is
        read when it changes rather than polled. There is no service here and
        nothing is sent anywhere: this is one file on this machine.
        """
        if watching and self.report_monitor is None:
            try:
                file = Gio.File.new_for_path(capability.REPORT)
                self.report_monitor = file.monitor_file(Gio.FileMonitorFlags.NONE, None)
                self.report_handler = self.report_monitor.connect(
                    'changed', lambda *_: self.refresh_machine())
            except GLib.Error:
                self.report_monitor = self.report_handler = None
        elif not watching and self.report_monitor is not None:
            self.report_monitor.disconnect(self.report_handler)
            self.report_monitor.cancel()
            self.report_monitor = self.report_handler = None

    def refresh_machine(self):
        self.machine.load()
        self.table_stack.set_visible_child_name(MACHINE)
        # A machine with no report already offers the check in the middle of
        # the window; a second button in the toolbar would only be a second
        # way to press the same thing.
        self.recheck.set_visible(self.tab == MACHINE and capability.can_check()
                                 and self.machine.report.present)
        if self.tab == MACHINE:
            self.status_left.set_label(self.machine.report.summary())

    def check_machine(self):
        """Check again now — the same thing `sudo luma-capability --check` does."""
        if not capability.can_check():
            return
        self.recheck.set_sensitive(False)
        self.status_left.set_label('Checking this machine…')
        try:
            process = Gio.Subprocess.new(['pkexec', capability.CHECKER, '--force'],
                                         Gio.SubprocessFlags.STDOUT_SILENCE |
                                         Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self.recheck.set_sensitive(True)
            self.refresh_machine()
            return

        def finished(source, result):
            try:
                source.wait_finish(result)
            except GLib.Error:
                pass
            self.recheck.set_sensitive(True)
            self.refresh_machine()
        process.wait_async(None, finished)

    def _columns(self):
        if self.tab == MACHINE:
            return
        model = self.table.get_columns()
        while model.get_n_items(): self.table.remove_column(model.get_item(0))
        columns = list(COLUMNS[self.tab])
        if self.advanced and self.tab != 'filesystems':
            columns += [('PID', 'pid'), ('User', 'user'), ('Threads', 'threads')]
        for title, key in columns:
            factory = Gtk.SignalListItemFactory()
            factory.connect('setup', self._setup_cell, key)
            factory.connect('bind', self._bind_cell, key)
            factory.connect('unbind', self._unbind_cell)
            column = Gtk.ColumnViewColumn(title=title.upper(), factory=factory, expand=key == 'name', resizable=True)
            if key != 'name': column.set_fixed_width(125 if key not in ('pid', 'threads') else 70)
            sorter = Gtk.CustomSorter.new(lambda a, b, _data, k=key: self._compare_values(a.payload, b.payload, k))
            column.set_sorter(sorter)
            self.table.append_column(column)
        # Header interactions expose native sorting; the stable row application
        # below consults the same ColumnView sorter when its choice changes.
        sort = self.table.get_sorter()
        if not hasattr(self, '_sort_connected'):
            sort.connect('changed', self._sort_changed); self._sort_connected = True
        primary = 0 if self.tab == 'filesystems' else 1
        self.table.sort_by_column(self.table.get_columns().get_item(primary), Gtk.SortType.DESCENDING)

    def _compare_values(self, a, b, key):
        x, y = a.get(key), b.get(key)
        if x is None: return 0 if y is None else -1
        if y is None: return 1
        if isinstance(x, str): x, y = x.casefold(), str(y).casefold()
        return (x > y) - (x < y)

    def _sort_changed(self, *_):
        if self.snapshot: self.refresh_rows()

    def _setup_cell(self, _factory, item, key):
        box = Gtk.Box(spacing=6)
        label = Gtk.Label(xalign=0 if key in ('name', 'user', 'mount', 'type') else 1, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        if key == 'name':
            icon = Gtk.Image(pixel_size=16); box.append(icon); box.icon = icon
        elif key == 'used':
            bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER); bar.set_size_request(60, -1)
            box.append(bar); box.bar = bar
        box.append(label); box.label = label
        item.set_child(box)

    def _bind_cell(self, _factory, item, key):
        row, box = item.get_item(), item.get_child()
        def update(*_):
            data = row.payload
            box.monitor_id = data['id']
            box.label.set_label(cell_value(data, key))
            box.set_tooltip_text(data.get('name', ''))
            if data.get('background') or data.get('group'): box.add_css_class('dim-label')
            else: box.remove_css_class('dim-label')
            if hasattr(box, 'icon'):
                try: box.icon.set_from_gicon(Gio.Icon.new_for_string(data.get('icon', 'drive-harddisk-symbolic')))
                except GLib.Error: box.icon.set_from_icon_name('application-x-executable-symbolic')
            if hasattr(box, 'bar'): box.bar.set_fraction(data.get('used', 0) / 100)
        item.handler = row.connect('notify::payload', update)
        update()

    def _unbind_cell(self, _factory, item):
        if getattr(item, 'handler', None):
            item.get_item().disconnect(item.handler); item.handler = None

    def _tab_changed(self, button, key):
        if not button.get_active() or key == self.tab: return
        self.selected_id = None
        self.tab = key
        self._columns()
        self._show_tab()
        self.refresh_rows(); self.refresh_footer()

    def _selection_changed(self, *_):
        if getattr(self, '_updating', False): return
        item = self.selection.get_selected_item()
        self.selected_id = item.payload['id'] if item and not item.payload.get('group') else None
        if self.selected_id is None: self.unfreeze_at = time.monotonic() + 1
        self._menu()

    def _menu(self):
        self.get_application().set_menubar(command_menu_model(self.commands, self.get_application()))

    def selected_row(self):
        obj = self.objects.get(self.selected_id)
        return obj.payload if obj and obj.payload.get('members') else None

    def refresh_rows(self):
        if self.tab == MACHINE: return
        if not self.snapshot: return
        import functools
        needle = self.search.get_text().casefold()
        rows = self.snapshot.get('filesystems', []) if self.tab == 'filesystems' else self.snapshot['rows']
        rows = [r for r in rows if needle in (r['name'] + ' ' + r.get('search', '')).casefold()]
        sorter = self.table.get_sorter()
        def compare(a, b):
            return int(sorter.compare(Row(a), Row(b)))
        apps = sorted((r for r in rows if not r.get('background')), key=functools.cmp_to_key(compare))
        background = sorted((r for r in rows if r.get('background')), key=functools.cmp_to_key(compare))
        if self.pointer_inside or self.selected_id or time.monotonic() < self.unfreeze_at:
            positions = {self.store.get_item(i).payload['id']: i for i in range(self.store.get_n_items())}
            apps.sort(key=lambda r: positions.get(r['id'], len(positions)))
            background.sort(key=lambda r: positions.get(r['id'], len(positions)))
        visible_matches = len(apps) + (len(background) if self.advanced or self.show_background else 0)
        self.empty.set_text(f'No process called “{self.search.get_text()}”', 'Try part of the app’s name.')
        self.table_stack.set_visible_child_name('empty' if needle and not visible_matches else 'table')
        ordered = apps
        if (self.advanced or self.show_background) and self.tab != 'filesystems':
            total = self.snapshot.get('background_count', sum(r['background'] for r in self.snapshot['rows']))
            ordered.append({'id': '__background', 'name': f'BACKGROUND · {len(background) if self.background_open else 0} shown of {total}', 'group': True,
                            'icon': 'pan-down-symbolic' if self.background_open else 'pan-end-symbolic'})
            if self.background_open: ordered += background
        self._updating = True
        selected = self.selected_id
        wanted = {row['id'] for row in ordered}
        for key in list(self.objects):
            if key not in wanted: del self.objects[key]
        for index, data in enumerate(ordered):
            key = data['id']
            obj = self.objects.get(key)
            if obj is None: obj = self.objects[key] = Row(data)
            else: obj.payload = data
            if index < self.store.get_n_items() and self.store.get_item(index) is obj: continue
            found, position = self.store.find(obj)
            if found: self.store.remove(position)
            self.store.insert(index, obj)
        while self.store.get_n_items() > len(ordered): self.store.remove(self.store.get_n_items() - 1)
        self.selection.unselect_all()
        for index, row in enumerate(ordered):
            if row['id'] == selected: self.selection.set_selected(index)
        if selected not in wanted: self.selected_id = None
        self._updating = False
        if selected != self.selected_id: self._menu()

    def _table_click(self, gesture, _count, x, y):
        widget = self.table.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and not hasattr(widget, 'monitor_id'): widget = widget.get_parent()
        if widget is None: return
        key = widget.monitor_id
        if key == '__background':
            self.background_open = not self.background_open; self.refresh_rows()
        else:
            self.selected_id = None if self.selected_id == key and gesture.get_current_button() == 1 else key
            self.refresh_rows(); self._menu()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def toggle_advanced(self):
        self.advanced = not self.advanced
        self.advanced_button.set_active(self.advanced)
        self.buttons['filesystems'].set_visible(self.advanced)
        if not self.advanced and self.tab == 'filesystems': self.buttons['cpu'].set_active(True)
        self.background_open = self.advanced or self.background_open
        self.cores.set_visible(self.advanced)
        self.status_right.set_label('Advanced' if self.advanced else '')
        self._columns(); self.refresh_rows(); self._menu()

    def toggle_background(self):
        self.show_background = not self.show_background
        self.background_open = True; self.refresh_rows(); self._menu()

    def _mapped(self, *_):
        surface = self.get_surface()
        if surface and not hasattr(self, '_surface_handler'):
            self._surface_handler = surface.connect('notify::state', self._visibility_changed)
        self._visibility_changed()

    def _visible(self):
        surface = self.get_surface()
        return not self.closed

    def _visibility_changed(self, *_):
        if self._visible():
            if not self.timer:
                self.sampler.reset(); self.tick()
                self.timer = GLib.timeout_add_seconds(1, self.tick)
        elif self.timer:
            GLib.source_remove(self.timer); self.timer = 0

    def tick(self):
        if not self._visible(): return GLib.SOURCE_CONTINUE
        if self.working: return GLib.SOURCE_CONTINUE
        self.working = True
        memory = self.tab == 'memory'
        def sample():
            self.catalog.refresh_running()
            snapshot = self.sampler.sample(memory=memory)
            if not getattr(self.sampler, 'host', False):
                snapshot['battery'] = battery_info()
                snapshot['filesystems'] = filesystems()
                held = inhibitors()
                for row in snapshot['rows']:
                    if held is not None and not row['background']:
                        row['inhibits'] = any(p.pid in held for p in row['members'])
            return snapshot
        future = self.pool.submit(sample)
        future.add_done_callback(lambda done: GLib.idle_add(self._sampled, done))
        return GLib.SOURCE_CONTINUE

    def _sampled(self, future):
        self.working = False
        if self.closed: return GLib.SOURCE_REMOVE
        try: snapshot = future.result()
        except Exception as error:
            self.status_left.set_label('—')
            print('Monitor: activity sample unavailable', file=sys.stderr)
            return GLib.SOURCE_REMOVE
        if not self._visible(): return GLib.SOURCE_REMOVE
        self.snapshot = snapshot
        mem = snapshot['memory']
        self.histories['cpu'].append(snapshot['cpu'])
        self.histories['memory'].append(mem.get('MemTotal', 0) - mem.get('MemAvailable', 0))
        from .model import complete_sum
        self.histories['disk'].append(complete_sum(snapshot['disk_rates']))
        self.histories['network'].append(complete_sum(snapshot['network_rates']))
        busy = sum(1 for row in snapshot['rows'] if not row['background'] and (row['cpu'] or 0) > .5)
        if self.tab == MACHINE:
            # The machine view's status line is the report's age, kept current
            # so "checked 3 hours ago" is never stale while somebody reads it.
            if self.machine.report is not None and self.recheck.get_sensitive():
                self.status_left.set_label(self.machine.report.summary())
        else:
            self.status_left.set_label(f"{len(snapshot['processes'])} processes · {busy} apps busy · Up {duration(snapshot['uptime'])}")
        self.refresh_rows(); self.refresh_footer()
        return GLib.SOURCE_REMOVE

    def refresh_footer(self):
        if self.tab == MACHINE: return
        if not self.snapshot: return
        s, tab = self.snapshot, self.tab
        mem = s['memory']
        pct = lambda v: '—' if v is None else f'{v:.0f}%'
        rr = rate_size
        try:
            st = os.statvfs('/'); free = size(st.f_bavail * st.f_frsize) + ' of ' + size(st.f_blocks * st.f_frsize)
        except OSError: free = '—'
        battery = s['battery'] or {}
        cpu_values = [s['apps_cpu'], s['system_cpu'], s['idle_cpu']]
        if all(v is not None for v in cpu_values):
            rounded_apps, rounded_system = round(cpu_values[0]), round(cpu_values[1])
            cpu_text = [f'{rounded_apps}%', f'{rounded_system}%', f'{100 - rounded_apps - rounded_system}%']
        else:
            cpu_text = [pct(v) for v in cpu_values]
        figures = {
            'cpu': [('Apps', cpu_text[0]), ('System', cpu_text[1]), ('Idle', cpu_text[2])],
            'memory': [('Used', size(mem.get('MemTotal', 0) - mem.get('MemAvailable', 0)) + ' of ' + size(mem.get('MemTotal'))),
                       ('Cached', size(max(0, mem.get('Cached', 0) + mem.get('SReclaimable', 0) - mem.get('Shmem', 0)))), ('Swap used', (size(mem.get('SwapTotal', 0) - mem.get('SwapFree', 0)) + ' of ' + size(mem.get('SwapTotal'))) if mem.get('SwapTotal') else 'None')],
            'disk': [('Reading', rr(s['disk_rates'][0])), ('Writing', rr(s['disk_rates'][1])), ('Free', free)],
            'network': [('Receiving', rr(s['network_rates'][0])), ('Sending', rr(s['network_rates'][1])), ('Received since start-up', size(s.get('network_received')))],
            'energy': [('Battery', pct(battery.get('percentage')) if battery else 'None'), ('Remaining', 'Charging' if battery.get('charging') else 'Plugged in' if battery.get('plugged') else duration(battery.get('remaining'))), ('Impact now', self._impact_label(s['rows']))],
        }.get(tab)
        if figures:
            for (label, value), (title, text) in zip(self.figure_labels, figures):
                label.set_label(title.upper()); value.set_label(text); value.set_tooltip_text(text)
        key = 'cpu' if tab == 'energy' else tab
        if key in self.histories:
            self.graph.values = list(self.histories[key])
            self.graph.scale = 100 if key == 'cpu' else mem.get('MemTotal', 1) if key == 'memory' else 1.15 * max([1] + [v for v in self.graph.values if v is not None])
            self.graph.caption = '100%' if key == 'cpu' else rate_size(self.graph.scale) if key in ('disk', 'network') else size(self.graph.scale)
            self.graph.queue_draw()
        cores = s['cores']
        if getattr(self, '_core_count', None) != len(cores):
            while self.cores.get_first_child(): self.cores.remove(self.cores.get_first_child())
            name = Gtk.Label(label=f'{len(cores)} CORES', xalign=0); name.set_size_request(220, -1); self.cores.append(name)
            self.core_bars = []
            box = Gtk.FlowBox(homogeneous=True, hexpand=True, column_spacing=6,
                              row_spacing=6, selection_mode=Gtk.SelectionMode.NONE,
                              min_children_per_line=1, max_children_per_line=8)
            self.cores.append(box)
            for index, _ in enumerate(cores):
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                bar = Gtk.ProgressBar(); label = Gtk.Label(); label.add_css_class('monitor-core-number')
                bar.update_property([Gtk.AccessibleProperty.LABEL], [f'CPU core {index + 1} use'])
                cell.append(bar); cell.append(label); box.insert(cell, -1); self.core_bars.append((bar, label))
            self._core_count = len(cores)
        for (bar, label), value in zip(self.core_bars, cores):
            bar.set_fraction((value or 0) / 100); label.set_label(pct(value))

    def _impact_label(self, rows):
        values = [r['impact'] for r in rows if not r['background']]
        if not values or any(v is None for v in values): return '—'
        total = sum(values)
        return 'Low' if total < 20 else 'Moderate' if total <= 50 else 'High'

    def quit_selected(self):
        row = self.selected_row()
        if not row: return
        if row.get("background") or self.tab in ("filesystems", MACHINE): return
        future = self.pool.submit(quit_row, row)
        def finished(done):
            def show():
                try: done.result()
                except Exception:
                    print('Monitor: quit request unavailable', file=sys.stderr)
                self.tick()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(show)
        future.add_done_callback(finished)

    def about(self):
        Adw.AboutDialog(application_name='Monitor', application_icon=ICON_NAME, developer_name='Project Luma', version='0.1.0',
                        comments='System activity from Linux procfs, sysfs, systemd and UPower. Built with the Luma App Kit.').present(self)

    def show_machine(self):
        """Open on This machine. Settings > About is how a person gets here."""
        self.buttons[MACHINE].set_active(True)
        if self.tab != MACHINE:
            self.tab = MACHINE
            self._show_tab()

    def _closing(self, *_):
        self._watch_report(False)
        try:
            update_preferences(self.preferences,{'tab': self.tab, 'advanced': self.advanced, 'background': self.show_background, 'width': self.get_width(), 'height': self.get_height()})
        except (OSError, ValueError, TypeError):
            pass
        self.closed = True
        if self.timer: GLib.source_remove(self.timer); self.timer = 0
        self.catalog_monitor.disconnect(self.catalog_handler)
        self.catalog.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
        return False


class MonitorApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    def do_startup(self):
        Adw.Application.do_startup(self)
        install_appkit()
        # Through the kit, not a private provider. A sheet that names a kit
        # colour takes the value current when it is parsed, so a private
        # provider keeps Light's palette after the desktop moves to Dark,
        # Frost or Glass. add_style_sheet() parses it again on every change,
        # which is what the state colours in This machine depend on.
        add_style_sheet(os.environ.get('LUMA_MONITOR_STYLE_PATH', '/usr/share/luma-monitor/monitor.css'))
        # Settings > About sends somebody here, either by activating this
        # action over D-Bus or by launching the desktop action that carries
        # the same flag.
        action = Gio.SimpleAction.new('this-machine', None)
        action.connect('activate', lambda *_: self.show_machine())
        self.add_action(action)
    def do_command_line(self, command_line):
        if '--this-machine' in command_line.get_arguments()[1:]:
            self.show_machine()
        else:
            self.activate()
        return 0
    def do_activate(self):
        window = self.get_active_window() or MonitorWindow(self)
        window.present()
    def show_machine(self):
        self.activate()
        window = self.get_active_window()
        if isinstance(window, MonitorWindow):
            window.show_machine()


def main():
    return MonitorApplication().run(sys.argv)
