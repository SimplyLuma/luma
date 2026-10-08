from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from dataclasses import asdict, replace
from pathlib import Path
import subprocess
from datetime import datetime

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from luma_appkit import (AppWindow, Command, CommandGroup, CommandRegistry,
                        Menu, Toast, apply_type, icons)
from luma_appkit.lumaui import mobile_form_factor
from .errors import InstallerError
from .inspectors import architecture_is_compatible, architecture_mismatch_message, inspect_package
from .progress import Transaction, Progress, Cancelled, current
from .safety import cleanup_abandoned_user_transactions
from . import workflow
from .removal import apply_view, display_name, removal_view, unavailable_view
from .valet_permissions import network_declared
from .valet_ticket import ValetTicket
from .valet_fixture import ValetFixture
from .desktop import iter_records


def human_size(size):
    value = float(size)
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'bytes' else f'{value:.1f} {unit}'
        value /= 1024


def valet_menu(window, extra=()):
    """The ticket's application menu, using installed owner records."""
    records = [r for r in iter_records() if r.get('application_id')]
    remove = tuple(Command('valet.remove.' + str(i),
                           str(r.get('name') or display_name(str(r['application_id']))) + '…',
                           lambda app_id=str(r['application_id']): window.open_removal(app_id),
                           icon=icons.icon_name('trash-2')) for i, r in enumerate(records))
    return CommandRegistry((
        CommandGroup('', (Command('valet.new', 'Install an app…', window.open_chooser,
                                  icon=icons.icon_name('download')),)),
        CommandGroup('Remove', remove),
        *extra,
    ))


class InstallerWindow(AppWindow):
    def __init__(self, application, path=None, remove_id=None, error=None):
        self.path = path
        self.report = None
        self.eligible = False
        self.trust = None
        self.record = None
        self.removing = remove_id is not None
        self.remove_id = remove_id
        self.closes_only = False
        self.busy = False
        self.inspecting = True
        self.done = False
        self.transaction = None
        self.open_process = None
        self._advanced_open = False
        self._inspect_generation = 0
        self.permission_changes: dict[str, bool] = {}
        package_commands = (
            CommandGroup('Package', (
                Command('package.reveal', 'Reveal in Filer', self.reveal, icon='folder-open-symbolic',
                        enabled=lambda: self.path is not None),
                Command('applications.open', 'Applications', self.open_applications, icon='view-grid-symbolic'),
                Command('advanced.toggle', 'Advanced', self.toggle_advanced, shortcut=('<Primary>i',),
                        enabled=lambda: not self.busy and not self.inspecting and not self.done),
            )),
        )
        commands = valet_menu(self, package_commands)
        super().__init__(application=application, app_id='io.luma.Valet', title='Valet',
                         icon_name='io.luma.Valet', commands=commands,
                         geometry_scope='removal' if self.removing else 'install',
                         default_width=820, default_height=410,
                         minimum_width=340 if mobile_form_factor() else 720,
                         minimum_height=300)
        self.title_bar.set_name('vl-top')
        self.identity.set_tooltip_text('Valet menu')
        self.card = ValetTicket()
        for name, handler in (('transaction-confirm', self.primary_clicked),
                              ('transaction-secondary', self.secondary_clicked)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', handler)
            self.add_action(action)
        # Keep the named actions for shortcuts and automation, while routing a
        # pointer click explicitly through the same action on this overlay.
        self.card.primary.connect('clicked', lambda *_: self.activate_action('win.transaction-confirm', None))
        self.card.secondary.connect('clicked', lambda *_: self.activate_action('win.transaction-secondary', None))
        self.set_default_widget(self.card.primary)
        self.card.advanced.connect('toggled', self.advanced_changed)
        self.card.keep.connect('toggled', self.keep_changed)
        # Long sentences wrap in full; a truncated refusal reads as a threat.
        phase = self.card.phase_label
        phase.set_single_line_mode(False); phase.set_ellipsize(Pango.EllipsizeMode.NONE)
        phase.set_wrap(True); phase.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        phase.set_justify(Gtk.Justification.CENTER); phase.set_max_width_chars(44)
        phase.set_size_request(-1, -1)
        self.drawer_contents = self.card.advanced_contents
        self.set_body(self.card)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 390px'))
        narrow.add_setter(self.card.panels, 'orientation', Gtk.Orientation.VERTICAL)
        narrow.add_setter(self.card.stub_slot, 'width-request', -1)
        self.add_breakpoint(narrow)
        self.connect('close-request', self.close_requested)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self.key_pressed)
        self.add_controller(keys)
        self.card.name.set_label('Reading application…')
        if remove_id: self.show_identity(remove_id)
        self.card.file_label.set_label(path.name if path else '')
        self.card.set_phase('ready', 'Uninstall' if self.removing else 'Install', 'Reading application…')
        self.card.primary.set_sensitive(False)
        self.card.advanced.set_sensitive(False)
        if error:
            self.failed(error)
        else:
            self._start_inspection(remove_id)

    def _start_inspection(self, remove_id):
        self._inspect_generation += 1
        generation = self._inspect_generation
        threading.Thread(target=self.inspect_worker, args=(remove_id, generation), daemon=True).start()

    def show_identity(self, remove_id):
        """Name and icon from the launcher, before ownership is known or if it never is."""
        name, icon = display_name(remove_id), None
        try:
            desktop = Gio.DesktopAppInfo.new(remove_id) if remove_id.endswith('.desktop') else None
            if desktop is not None:
                name = desktop.get_display_name() or desktop.get_name() or name
                icon = desktop.get_icon()
        except (TypeError, GLib.Error):
            pass
        self.card.name.set_label(name)
        self.card.name.set_tooltip_text(name)
        if icon is not None: self.card.set_icon(icon)
        self.set_title('Uninstall · ' + name)

    def removal_view(self, reset_keep=False):
        keep = True if reset_keep else self.card.keep.get_active()
        return removal_view(self.record, self.report.title, keep)

    def keep_changed(self, *_args):
        if self.removing and self.report is not None and not (self.busy or self.inspecting or self.done):
            apply_view(self.card, self.removal_view())

    def key_pressed(self, _controller, key, _code, _state):
        if key == Gdk.KEY_Escape:
            if self.busy: self.secondary_clicked(None)
            elif self._advanced_open: self.card.advanced.set_active(False)
            else: self.close()
            return True
        return False

    def close_requested(self, *_args):
        if self.busy:
            self.secondary_clicked(None)
            return True
        return False

    def inspect_worker(self, remove_id, generation):
        try:
            if remove_id:
                report, record = workflow.installed_report(remove_id)
            else:
                report, record = inspect_package(self.path), None
            GLib.idle_add(self.inspected, report, record, generation)
        except Exception as error:
            GLib.idle_add(self.failed_inspection, str(error), generation)

    def failed_inspection(self, message, generation):
        if generation == self._inspect_generation:
            self.failed(message)
        return False

    def inspected(self, report, record=None, generation=None):
        if generation is not None and generation != self._inspect_generation:
            return False
        self.inspecting = False
        self.report, self.record = report, record
        self.eligible = architecture_is_compatible(report) or self.removing
        self.card.name.set_label(report.title)
        self.card.name.set_tooltip_text(report.title)
        # Missing metadata is not replaced with invented publisher/version facts.
        self.card.set_subtitle(report.summary, report.publisher if report.publisher != 'Not declared' else '',
                               verified=report.verified)
        self.card.set_icon(report.icon)
        self.card.set_lit_name(report.title)
        self.card.advanced_file.set_label(report.path.name if report.path else '')
        self.permission_changes.clear()
        declared_network = network_declared(report)
        self._declared_network = declared_network
        self.card.set_permissions([
            ('Network', 'globe', declared_network, True, self._network_changed)
        ] if declared_network is not None else [])
        self.card.set_facts([
            ('Version', report.version or 'Not declared'),
            ('Package size', human_size(report.byte_size)),
            ('Format', report.kind.upper()),
            ('From', report.details.get('Repository', report.path.name if report.path else 'Not declared')),
            ('Signed by', report.publisher or 'Not declared'),
        ])
        self.card.keep.set_sensitive(report.kind != 'snap')
        # Keeping data is the default every time; deleting it takes an explicit uncheck.
        self.card.keep.set_active(True)
        if report.kind == 'snap':
            self.card.keep.set_tooltip_text('snapd retains an automatic snapshot according to system policy.')
        else: self.card.keep.set_tooltip_text(None)
        protected = self.removing and record is not None and not record.get('removable', True)
        self.set_title(report.title if protected else ('Uninstall' if self.removing else 'Install') + ' · ' + report.title)
        self.populate_facts()
        if not self.eligible:
            return self.failed(architecture_mismatch_message(report))
        self.ready()
        return False

    def _network_changed(self, allowed):
        if allowed == self._declared_network:
            self.permission_changes.pop('network', None)
        else:
            self.permission_changes['network'] = allowed

    def ready(self, message=''):
        self.busy = self.done = False
        self.closes_only = False
        self.card.phase_label.remove_css_class("error")
        if self.removing and self.report is not None and self.record is not None:
            view = self.removal_view()
            if message and not view.closes: view = replace(view, message=message)
            apply_view(self.card, view)
            self.closes_only = view.closes
            self.card.primary.set_sensitive(True)
        elif self.removing:
            # Nothing to review: say why, offer Close, and never a red button or a data choice.
            apply_view(self.card, unavailable_view(message))
            self.closes_only = True
            self.card.primary.set_sensitive(True)
        else:
            self.card.set_phase('ready', 'Install', message)
            self.card.primary.set_sensitive(self.report is not None and self.eligible)
        self.update_foot()

    def update_foot(self):
        if self.report is None: return
        label = self.report.title if self.removing else self.report.path.name
        # Source file size is not installed footprint. Do not relabel it as one.
        suffix = (' · uninstalled' if self.done else ' · installed') if self.removing else ' · ' + human_size(self.report.byte_size)
        if self.done and self.report.requires_restart: suffix = ' · restart required'
        self.card.file_label.set_markup('<b>' + GLib.markup_escape_text(label) + '</b>' + GLib.markup_escape_text(suffix))
        self.card.file_label.set_tooltip_text(label + suffix)

    def populate_facts(self):
        child = self.drawer_contents.get_first_child()
        while child:
            next_child = child.get_next_sibling(); self.drawer_contents.remove(child); child = next_child
        self.card.advanced_file.set_label(self.report.path.name if self.report.path else self.report.title)

        def section(title):
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            panel.add_css_class('valet-advanced-section')
            panel.append(apply_type(Gtk.Label(label=title, xalign=0), 'label', weight=650))
            self.drawer_contents.append(panel)
            return panel

        def pair(panel, key, value):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            line.add_css_class('valet-advanced-row')
            line.add_css_class('valet-advanced-fact')
            line.append(apply_type(Gtk.Label(label=key, xalign=0), 'meta'))
            if key == 'SHA-256':
                copy = Gtk.Button(halign=Gtk.Align.END, hexpand=True)
                copy.add_css_class('valet-sha')
                contents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                sha_value = apply_type(Gtk.Label(label=str(value), xalign=1), 'small', weight=500)
                sha_value.add_css_class('valet-sha-value')
                contents.append(sha_value)
                contents.append(icons.image('copy', pixel_size=12))
                copy.set_child(contents)
                def copy_checksum(*_args):
                    Gdk.Display.get_default().get_clipboard().set(str(value))
                    Toast.show(self.card, 'Copied the checksum')
                copy.connect('clicked', copy_checksum)
                line.append(copy)
            else:
                value_label = apply_type(Gtk.Label(label=str(value), xalign=1, hexpand=True, wrap=True),
                                         'meta', weight=550)
                value_label.add_css_class('valet-advanced-value')
                line.append(value_label)
            panel.append(line)

        run = section('How it runs')
        source = self.report.details.get('Metadata source', self.report.summary)
        run_description = apply_type(Gtk.Label(label=source, xalign=0, wrap=True), 'body')
        run_description.add_css_class('valet-subtitle')
        run.append(run_description)
        if self.report.details.get('Runtime'):
            pair(run, 'Runtime', self.report.details['Runtime'])
        if self.report.warnings:
            for warning in self.report.warnings:
                run.append(apply_type(Gtk.Label(label=warning, xalign=0, wrap=True), 'caption'))

        permissions = section('Permissions')
        if self.report.permissions:
            permissions.set_spacing(6)
            for index, permission in enumerate(self.report.permissions):
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                line.add_css_class('valet-advanced-row')
                line.add_css_class('valet-advanced-permission')
                if index == 0:
                    line.set_margin_top(7)
                permission_icon = icons.image('shield')
                permission_icon.add_css_class('valet-advanced-icon')
                line.append(permission_icon)
                line.append(apply_type(Gtk.Label(label=permission, xalign=0, wrap=True), 'body', weight=550))
                permissions.append(line)
        else:
            permissions.append(apply_type(Gtk.Label(label='Not declared by this package', xalign=0), 'caption'))

        if not self.removing:
            adds = section('What it adds to Luma')
            adds.add_css_class('valet-advanced-adds')
            pair(adds, 'Applications', 'Registers the app in Applications')
            pair(adds, 'Storage', self.report.destination)

        network = network_declared(self.report)
        if network is not None:
            pair(section('Network'), 'Network access', 'Allowed' if network else 'Not allowed')

        package = section('Package')
        package_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        package.append(package_rows)
        for key, value in (('Architecture', self.report.architecture),
                           ('Download', human_size(self.report.byte_size)),
                           ('Version', self.report.version or 'Not declared'),
                           ('Publisher', self.report.publisher or 'Not declared'),
                           ('SHA-256', self.report.sha256)):
            pair(package_rows, key, value)
        extra = {k: v for k, v in self.report.details.items()
                 if k not in {'Metadata source', 'Runtime'}}
        if extra:
            details = section('More details')
            for key, value in extra.items():
                pair(details, key, value)
        if not self.removing and self.report.requires_system_change:
            self.trust = Gtk.CheckButton()
            self.trust.set_child(Gtk.Label(label='I trust this source and approve the changes shown here', wrap=True, xalign=0))
            self.trust.connect('toggled', lambda *_: self.card.primary.set_sensitive(True))
            self.drawer_contents.append(self.trust)
        else: self.trust = None

    def toggle_advanced(self):
        self.card.advanced.set_active(not self.card.advanced.get_active())

    def advanced_changed(self, toggle):
        opened = toggle.get_active()
        self._advanced_open = opened
        self.card.advanced.set_label('Advanced' if not opened else 'Close advanced')

    def primary_clicked(self, *_args):
        if self.busy or self.inspecting: return
        if self.closes_only:
            self.close(); return
        if self.done:
            if self.removing or self.report.requires_restart or self.report.kind == 'flatpakrepo': self.return_home()
            elif self.record:
                self.open_record()
            return
        if self.report is None or not self.eligible: return
        if self.removing and not self.record.get('removable', True): return
        if self.trust and not self.trust.get_active():
            self.card.advanced.set_active(True)
            self.card.phase_label.set_label('Review and approve this source in Advanced.')
            self.trust.grab_focus()
            return
        self.busy = True
        self.card.advanced.set_active(False)
        # One package is one job: the card names this application, never the
        # dependencies a backend happens to fetch on the way.
        self.transaction = Transaction(lambda event: GLib.idle_add(self.progress, event),
                                       title=('Removing ' if self.removing else 'Installing ') + self.report.title)
        self.progress(Progress('Prepare removal' if self.removing else 'Check the file', 0, True))
        # Only a visible, unchecked box means delete; anything else keeps data.
        keep = not (self.removing and self.card.keep.get_visible() and not self.card.keep.get_active())
        threading.Thread(target=self.transaction_worker, args=(keep,), daemon=True).start()

    def transaction_worker(self, keep):
        token = current.set(self.transaction)
        try:
            if self.removing:
                result = workflow.remove(self.record, keep)
            else:
                result = workflow.install(self.report, dict(self.permission_changes))
            GLib.idle_add(self.completed, result)
        except Cancelled:
            GLib.idle_add(self.ready, 'Cancelled')
        except Exception as error:
            GLib.idle_add(self.failed, str(error))
        finally: current.reset(token)

    def progress(self, event):
        # Only measured work fills the line; a stage marker does not pretend to.
        self.card.set_progress_label(event.label)
        self.card.set_phase('working', 'Removing…' if self.removing else 'Installing…', '',
                            event.fraction if event.measured else None,
                            removing=self.removing, cancellable=event.cancellable)
        return False

    def completed(self, result):
        self.busy = False; self.done = True
        if not self.removing: self.record = result
        if not self.removing and self.report.kind in {'rpm', 'snap'} and self.report.requires_system_change:
            from dataclasses import replace
            from .backends import system_restart_required
            self.report = replace(self.report, requires_restart=system_restart_required(
                self.report.sha256, self.report.requires_restart))
        if self.removing:
            self.card.name.set_label(self.report.title + ' is gone')
            self.card.set_gone(True)
        pending = bool(self.report.requires_restart)
        label = 'Done' if self.removing or pending or self.report.kind == 'flatpakrepo' else 'Open ' + self.report.title
        line = '' if self.removing else 'Ready in Applications'
        if self.report.kind == 'flatpakrepo' and not self.removing: line = 'Application source added'
        if pending:
            line = 'Restart to finish this system change.'
            self.card.name.set_label(self.report.title)
        self.card.set_phase('done', label, line, 1, removing=self.removing)
        self.update_foot()
        if not self.removing:
            self.card.set_stamp('Installed today, ' + datetime.now().strftime('%H:%M'))
        return False

    #: How long a start may take before Valet says no window has appeared.
    OPEN_PATIENCE_SECONDS = 20

    def open_record(self):
        """Answer the click now, and say honestly how the start went.

        The launch carries an activation token, so the application's first
        window is allowed to take focus from Valet. Valet losing focus is
        therefore the sign that the application opened; a launcher that exits
        with an error, or a start that shows nothing for a while, is reported
        instead of leaving the card saying it is starting.
        """
        import tempfile
        environment = self._activation_environment()
        errors = tempfile.TemporaryFile(prefix='valet-open-')
        try:
            process = workflow.open_application(self.record, environment=environment, errors=errors)
        except Exception as error:
            errors.close()
            self.card.phase_label.add_css_class('error')
            self.card.phase_label.set_label(str(error))
            return
        self.card.phase_label.remove_css_class('error')
        self.card.phase_label.set_tooltip_text(None)
        self.card.phase_label.set_label('Opening ' + self.report.title + '…')
        if process is None:
            errors.close()
            return
        # Held so Python never reaps the child behind the watch's back.
        self.open_process = process
        self.open_errors = errors
        self.card.primary.set_sensitive(False)
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, process.pid, self.open_finished)
        self._open_focus = self.connect('notify::is-active', self._open_focus_changed)
        self._open_timeout = GLib.timeout_add_seconds(self.OPEN_PATIENCE_SECONDS, self._open_waited_too_long)

    def _activation_environment(self):
        environment = {}
        try:
            context = self.get_display().get_app_launch_context()
            info = None
            try:
                info = Gio.DesktopAppInfo.new(self.record_desktop_id())
            except (TypeError, GLib.Error):
                info = None
            info = info or Gio.AppInfo.create_from_commandline('true', self.report.title, Gio.AppInfoCreateFlags.NONE)
            token = context.get_startup_notify_id(info, [])
        except Exception:
            token = None
        if token:
            environment['XDG_ACTIVATION_TOKEN'] = token
            environment['DESKTOP_STARTUP_ID'] = token
        return environment

    def record_desktop_id(self):
        from .desktop import safe_id
        return f"org.projectluma.Installed.{safe_id(str(self.record.get('application_id', '')))}.desktop"

    def _open_settled(self, label, *, error=False, detail=None):
        focus = getattr(self, '_open_focus', 0)
        if focus:
            self.disconnect(focus)
            self._open_focus = 0
        timeout = getattr(self, '_open_timeout', 0)
        if timeout:
            GLib.source_remove(timeout)
            self._open_timeout = 0
        self.card.primary.set_sensitive(True)
        if error:
            self.card.phase_label.add_css_class('error')
        else:
            self.card.phase_label.remove_css_class('error')
        self.card.phase_label.set_label(label)
        self.card.phase_label.set_tooltip_text(detail)

    def _open_focus_changed(self, *_args):
        if not self.is_active():
            self._open_settled('Ready in Applications')

    def _open_waited_too_long(self):
        # Say so, but keep watching: a slow window or a late failure still
        # replaces this line.
        self._open_timeout = 0
        self.card.primary.set_sensitive(True)
        self.card.phase_label.set_label(self.report.title + ' hasn’t shown a window yet. Open it again, or check Applications.')
        return GLib.SOURCE_REMOVE

    def _last_error_line(self):
        errors = getattr(self, 'open_errors', None)
        if errors is None:
            return None
        try:
            errors.seek(max(0, errors.seek(0, 2) - 4096))
            lines = [line.strip() for line in errors.read().decode('utf-8', 'replace').splitlines() if line.strip()]
        except OSError:
            lines = []
        finally:
            errors.close()
            self.open_errors = None
        return lines[-1][:300] if lines else None

    def open_finished(self, pid, status, *_args):
        GLib.spawn_close_pid(pid)
        self.open_process = None
        detail = self._last_error_line()
        still_opening = bool(getattr(self, '_open_focus', 0))
        if not still_opening:
            return False  # It opened; closing it later is not news.
        if status != 0:
            self._open_settled(self.report.title + ' stopped before it opened.', error=True, detail=detail)
        else:
            # A launcher that hands off to an already-running copy exits at once.
            self._open_settled('Ready in Applications')
        return False

    def secondary_clicked(self, *_args):
        if self.busy:
            if self.transaction and self.transaction.cancel():
                self.card.phase_label.set_label('Cancelling…')
                self.card.secondary.set_sensitive(False)
        elif not self.busy: self.return_home()

    def return_home(self):
        ChooserWindow(self.get_application()).present()
        self.close()

    def change_flow(self, removing):
        if self.busy or self.inspecting: return
        if removing:
            if not self.record: return
            self.inspecting = True
            self.done = False
            self.card.primary.set_sensitive(False)
            self.eligible = False
            self.removing = True
            self._start_inspection(str(self.record['application_id']))
        elif self.path is not None:
            self.inspecting = True
            self.done = False
            self.card.primary.set_sensitive(False)
            self.eligible = False
            self.removing = False
            self._start_inspection(None)

    def failed(self, message):
        self.inspecting = False
        self.ready(message)
        self.card.phase_label.add_css_class('error')
        return False

    def reveal(self):
        if self.path is None: return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call('org.freedesktop.FileManager1', '/org/freedesktop/FileManager1',
                     'org.freedesktop.FileManager1', 'ShowItems',
                     GLib.Variant('(ass)', ([self.path.as_uri()], '')), None,
                     Gio.DBusCallFlags.NONE, 5000, None, None, None)
        except GLib.Error as error: self.card.phase_label.set_label(str(error))

    def open_applications(self):
        Gio.AppInfo.launch_default_for_uri('applications:///', None)

    def open_chooser(self):
        ChooserWindow(self.get_application()).present()

    def open_removal(self, app_id):
        InstallerWindow(self.get_application(), remove_id=app_id).present()


class ChooserWindow(AppWindow):
    """Valet opened from Applications, with nothing to review yet."""

    def __init__(self, application):
        commands = valet_menu(self)
        super().__init__(application=application, app_id='io.luma.Valet', title='Valet',
                         icon_name='io.luma.Valet', commands=commands, geometry_scope='chooser',
                         default_width=720, default_height=340,
                         minimum_width=340, minimum_height=300)
        self.title_bar.set_name('vl-top')
        self.identity.set_tooltip_text('Valet menu')
        self.ticket = ValetTicket()
        self.ticket.set_home(True)
        self.ticket.choose_button.connect('clicked', lambda *_: self.choose(folder=False))
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect('drop', self.dropped)
        self.ticket.home.add_controller(drop)
        self.set_body(self.ticket)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 390px'))
        phone.add_setter(self.ticket.panels, 'orientation', Gtk.Orientation.VERTICAL)
        phone.add_setter(self.ticket.stub_slot, 'width-request', -1)
        self.add_breakpoint(phone)
        self.set_default_widget(self.ticket.choose_button)

    def open_chooser(self):
        self.choose(folder=False)

    def open_removal(self, app_id):
        InstallerWindow(self.get_application(), remove_id=app_id).present()

    def choose(self, folder):
        dialog = Gtk.FileDialog(title='Choose an Application Folder' if folder else 'Choose an Application')
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads:
            dialog.set_initial_folder(Gio.File.new_for_path(downloads))
        if folder:
            dialog.select_folder(self, None, self.chosen, dialog.select_folder_finish)
        else:
            dialog.open(self, None, self.chosen, dialog.open_finish)

    def chosen(self, dialog, result, finish):
        try:
            file = finish(result)
        except GLib.Error:
            return
        if file is not None and file.get_path():
            self._open_path(Path(file.get_path()))

    def dropped(self, _target, file, _x, _y):
        path = file.get_path() if isinstance(file, Gio.File) else None
        if not path:
            return False
        self._open_path(Path(path))
        return True

    def _open_path(self, path):
        InstallerWindow(self.get_application(), path).present()
        self.close()


class FixtureWindow(AppWindow):
    """v70 states in memory. Nothing here calls the installer or its stores."""

    FORMATS = {'flatpak': 'Flatpak', 'appimage': 'AppImage', 'deb': 'Debian package',
               'rpm': 'RPM package', 'apk': 'Android app', 'exe': 'Windows app'}

    def __init__(self, application, path):
        self.fixture = ValetFixture(path)
        # Fixture artwork is the exact v70 title icon; the installed app keeps
        # using its normal system icon theme.
        fixture_icons = Path(path).with_suffix('')
        if fixture_icons.is_dir():
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.set_search_path([str(fixture_icons), *theme.get_search_path()])
            Gtk.Window.set_default_icon_name('valet-v70-spec')
        self._fixture_state = os.environ.get('LUMA_VALET_STATE', 'install')
        commands = CommandRegistry((CommandGroup('', (
            Command('valet.new', 'Install an app…', self._home, icons.icon_name('download')),
            Command('valet.remove-harbor', 'Harbor…', lambda: self._remove('harbor'), icons.icon_name('trash-2')),
            Command('valet.remove-driftwood', 'Driftwood…', lambda: self._remove('driftwood'), icons.icon_name('trash-2')),
        )),))
        super().__init__(application=application, app_id='io.luma.Valet', title='Valet',
                         icon_name='io.luma.Valet', commands=commands,
                         default_width=720, default_height=340, minimum_width=340, minimum_height=300)
        self.title_bar.set_name('vl-top')
        self.identity.set_tooltip_text('Valet menu')
        self.ticket = ValetTicket()
        self.ticket.primary.connect('clicked', self._primary)
        self.ticket.secondary.connect('clicked', lambda *_: self._home())
        self.ticket.choose_button.connect('clicked', lambda *_: self._choose())
        self.ticket.keep.connect('toggled', self._keep_changed)
        self.ticket.advanced.connect('toggled', lambda *_: None)
        self.set_body(self.ticket)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 390px'))
        phone.add_setter(self.ticket.panels, 'orientation', Gtk.Orientation.VERTICAL)
        phone.add_setter(self.ticket.stub_slot, 'width-request', -1)
        self.add_breakpoint(phone)
        if self._fixture_state in {'home', 'choose'}:
            self.fixture.view = 'home'
        elif self._fixture_state == 'update':
            self.fixture.open_app('driftwood')
        elif self._fixture_state == 'warning':
            self.fixture.open_app('labelpress')
        elif self._fixture_state == 'permission':
            self.fixture.toggle_permission(0)
        elif self._fixture_state in {'remove', 'remove_all', 'removed'}:
            self.fixture.open_removal('harbor')
            self.fixture.keep = self._fixture_state != 'remove_all'
            self.fixture.removed = self._fixture_state == 'removed'
        elif self._fixture_state == 'installed':
            self.fixture.done = True
        elif self._fixture_state == 'installing':
            self.fixture.progress = 30
            self.fixture.step = 'Downloading from Flathub'
        elif self._fixture_state == 'advanced':
            self.fixture.advanced = True
        self._render()
        if self.fixture.advanced:
            self.ticket.advanced.set_active(True)
        if self._fixture_state == 'choose':
            GLib.timeout_add(250, self._choose)

    def _choose(self):
        buttons = tuple(Command('valet.pick.' + app_id, app['file'],
                                lambda app_id=app_id: self._open_fixture_app(app_id),
                                icons.icon_name('file-text'))
                        for app_id, app in self.fixture.apps.items())
        menu = Menu(CommandRegistry((CommandGroup('Downloads', buttons),)))
        menu.set_size_request(300, -1)
        menu.set_parent(self.ticket.choose_button)
        menu.set_offset(0, 9)
        menu.connect('closed', lambda popover: GLib.idle_add(popover.unparent))
        menu.popup()
        return False

    def _open_fixture_app(self, app_id):
        self.fixture.open_app(app_id)
        self._render()

    def _home(self):
        self.fixture.view = 'home'
        self.fixture.advanced = False
        self._render()

    def _remove(self, app_id):
        self.fixture.open_removal(app_id)
        self._render()

    def _keep_changed(self, button):
        if self.fixture.view == 'remove' and button.get_active() != self.fixture.keep:
            self.fixture.keep = button.get_active()
            self._render()

    def _primary(self, *_args):
        if self.fixture.view == 'home':
            self.fixture.open_app('kiln')
        elif self.fixture.view == 'remove':
            self.fixture.finish_removal()
        else:
            self.fixture.done = True
        self._render()

    def _show_advanced(self, app):
        box = self.ticket.advanced_contents
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        self.ticket.advanced_file.set_label(app['file'])

        def section(title):
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            panel.add_css_class('valet-advanced-section')
            panel.append(apply_type(Gtk.Label(label=title, xalign=0), 'label', weight=650))
            box.append(panel)
            return panel

        def pair(panel, key, value):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            line.add_css_class('valet-advanced-row')
            line.add_css_class('valet-advanced-fact')
            line.append(apply_type(Gtk.Label(label=key, xalign=0), 'meta'))
            if key == 'SHA-256':
                copy = Gtk.Button(halign=Gtk.Align.END, hexpand=True)
                copy.add_css_class('valet-sha')
                contents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                sha_value = apply_type(Gtk.Label(label=value, xalign=1), 'small', weight=500)
                sha_value.add_css_class('valet-sha-value')
                contents.append(sha_value)
                contents.append(icons.image('copy', pixel_size=12))
                copy.set_child(contents)
                copy.connect('clicked', lambda *_: Toast.show(self.ticket, 'Copied the checksum'))
                line.append(copy)
            else:
                value_label = apply_type(Gtk.Label(label=value, xalign=1, hexpand=True, wrap=True),
                                         'meta', weight=550)
                value_label.add_css_class('valet-advanced-value')
                line.append(value_label)
            panel.append(line)

        run = section('How it runs')
        run_description = apply_type(Gtk.Label(label=app['runs'], xalign=0, wrap=True), 'body')
        run_description.add_css_class('valet-subtitle')
        run.append(run_description)
        if app.get('rt'):
            pair(run, 'Runtime', app['rt'])
        if app.get('scripts'):
            run.append(apply_type(Gtk.Label(label=app['scripts'], xalign=0, wrap=True), 'small'))

        permissions = section('Permissions')
        permissions.set_spacing(6)
        for i, (name, glyph, _allowed, reason) in enumerate(app['perms']):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            line.add_css_class('valet-advanced-row')
            line.add_css_class('valet-advanced-permission')
            if i == 0:
                line.set_margin_top(7)
            permission_icon = icons.image(glyph)
            permission_icon.add_css_class('valet-advanced-icon')
            line.append(permission_icon)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            words.append(apply_type(Gtk.Label(label=name, xalign=0), 'body', weight=550))
            if reason:
                words.append(apply_type(Gtk.Label(label=reason, xalign=0), 'caption', weight=400))
            line.append(words)
            line.append(apply_type(Gtk.Label(label='Allowed' if self.fixture.permission_allowed(i)
                                             else 'Not allowed', xalign=1), 'small'))
            permissions.append(line)

        if app['adds']:
            adds = section('What it adds to Luma')
            adds.add_css_class('valet-advanced-adds')
            for glyph, name in app['adds']:
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                added_icon = icons.image(glyph)
                added_icon.add_css_class('valet-advanced-icon')
                added_icon.add_css_class('valet-advanced-muted')
                line.append(added_icon)
                added_label = apply_type(Gtk.Label(label=name, xalign=0), 'body')
                added_label.add_css_class('valet-subtitle')
                line.append(added_label)
                adds.append(line)

        network = section('Network')
        for address in app['net'] or ['Doesn’t use the network']:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            network_icon = icons.image('globe')
            network_icon.add_css_class('valet-advanced-icon')
            network_icon.add_css_class('valet-advanced-muted')
            line.append(network_icon)
            address_label = apply_type(Gtk.Label(label=address, xalign=0), 'body')
            address_label.add_css_class('valet-subtitle')
            line.append(address_label)
            network.append(line)

        package = section('Package')
        package_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        package.append(package_rows)
        for key, value in (('Architecture', 'arm64, x86_64' if app['fmt'] == 'apk' else 'x86_64'),
                           ('Download', app['sz']), ('Installed size', app['inst']),
                           ('SHA-256', '9f2c 41ab 7e08 … c3d1')):
            pair(package_rows, key, value)

    def _render(self):
        f, ticket = self.fixture, self.ticket
        ticket.advanced.set_active(False)
        if f.view == 'home':
            ticket.set_home(True)
            ticket.stub_total.set_visible(False)
            ticket.name.set_label('Drop an app here')
            ticket.set_subtitle('')
            ticket.set_permissions([])
            ticket.file_label.set_label('')
            ticket.set_stamp(None)
            ticket.set_facts([])
            ticket.set_phase('ready', 'Choose a file…')
            ticket.secondary.set_visible(False)
            ticket.advanced.set_visible(False)
            return
        ticket.set_home(False)
        ticket.advanced.set_visible(f.view == 'install')
        if f.view == 'remove':
            app = f.removal
            ticket.set_fixture_icon(app, gone=f.removed)
            ticket.set_lit_name(app['n'])
            ticket.name.set_label(app['n'] + ' removed' if f.removed else 'Remove ' + app['n'] + '?')
            ticket.set_subtitle(self.FORMATS[app['fmt']] + ' · installed ' + app['when'])
            ticket.set_permissions([])
            ticket.keep.set_active(f.keep)
            ticket.file_label.set_label('Stored on this computer')
            ticket.set_stamp(None)
            facts = [(name, str(size) + ' MB',
                      ('gone' if goes or not f.keep else 'retained') if f.removed
                      else ('removed' if goes or not f.keep else 'kept'))
                     for name, size, goes in app['parts']]
            ticket.set_facts(facts, columns=1)
            freed = ('%.2f GB' % (f.removed_bytes_mb() / 1000)
                     if f.removed_bytes_mb() >= 1000 else str(f.removed_bytes_mb()) + ' MB')
            ticket.set_total('Freed' if f.removed else 'Frees',
                             freed)
            ticket.set_phase('done' if f.removed else 'ready',
                             'Done' if f.removed else 'Remove ' + app['n'],
                             freed + ' freed' if f.removed else
                             'Files in Documents and Pictures aren’t touched.', removing=True)
            ticket.secondary.set_visible(not f.removed)
            return
        app = f.app
        ticket.set_fixture_icon(app)
        ticket.set_lit_name(app['n'])
        ticket.name.set_label(app['n'])
        ticket.set_subtitle(app['desc'], app['dev'], verified=app['ok'])
        editable = app['fmt'] in {'flatpak', 'apk', 'exe'} and not f.done and not f.progress
        ticket.set_permissions([(row[0], row[1], f.permission_allowed(i), editable,
                                 (lambda _allowed, index=i: f.toggle_permission(index)))
                                for i, row in enumerate(app['perms'])])
        if app.get('sandbox'):
            ticket.set_permissions([('Sandboxed' if f.sandbox else 'Not sandboxed', 'shield', f.sandbox,
                                     not f.done, lambda active: setattr(f, 'sandbox', active)),
                                    *[(row[0], row[1], f.permission_allowed(i), editable,
                                       (lambda _allowed, index=i: f.toggle_permission(index)))
                                      for i, row in enumerate(app['perms'])]])
        ticket.file_label.set_label(app['file'])
        ticket.set_stamp('Installed today, 10:40' if f.done else None)
        installed = f.installed.get(app['id'])
        update = installed is not None and installed.get('v') != app['v']
        ticket.set_facts([('Version', installed['v'] + ' → ' + app['v'] if update else app['v']), ('Size', app['inst']),
                          ('Format', self.FORMATS[app['fmt']]), ('From', app['src']),
                          ('Signed by', app['sig'] or 'Not signed')])
        action = 'Open ' + app['n'] if f.done else 'Update' if update else 'Reinstall' if installed else 'Install'
        message = ('In Apps' if f.done else
                   'Update from ' + installed['v'] + '. Settings and data stay.' if update else
                   'Already installed. Reinstalling keeps its settings.' if installed else app.get('warn', ''))
        ticket.set_phase('done' if f.done else 'working' if f.progress else 'ready',
                         action, message, f.progress / 100 if f.progress else 0)
        if f.progress:
            ticket.set_progress_label(f.step)
        ticket.secondary.set_label('Done' if f.done else 'Cancel')
        self._show_advanced(app)


class InstallerApplication(Adw.Application):
    def __init__(self, remove_id=None):
        app_id = 'io.luma.Valet.LumaUIPreview' if os.environ.get('LUMA_VALET_PREVIEW') else 'io.luma.Valet'
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.remove_id = remove_id

    def do_command_line(self, command):
        if os.environ.get('LUMA_VALET_FIXTURE'):
            self.do_activate()
            return 0
        args = command.get_arguments()[1:]
        if len(args) == 2 and args[0] == "--remove":
            InstallerWindow(self, remove_id=args[1]).present()
        elif len(args) == 1:
            file = command.create_file_for_arg(args[0])
            self.do_open([file], 1, "")
        else:
            self.do_activate()
        return 0

    def do_startup(self):
        GLib.set_application_name('Valet')
        if os.environ.get('LUMA_VALET_FIXTURE'):
            self._fixture_state_dir = tempfile.TemporaryDirectory(prefix='valet-fixture-state-')
            os.environ['XDG_STATE_HOME'] = self._fixture_state_dir.name
        Adw.Application.do_startup(self)
        if not os.environ.get('LUMA_VALET_FIXTURE') and not os.environ.get('LUMA_VALET_PREVIEW'):
            cleanup_abandoned_user_transactions()

    def do_shutdown(self):
        Adw.Application.do_shutdown(self)
        if hasattr(self, '_fixture_state_dir'):
            self._fixture_state_dir.cleanup()

    def do_activate(self):
        fixture = os.environ.get('LUMA_VALET_FIXTURE')
        if fixture:
            (self.get_active_window() or FixtureWindow(self, fixture)).present()
        elif self.remove_id:
            InstallerWindow(self, remove_id=self.remove_id).present()
        elif self.get_active_window(): self.get_active_window().present()
        else: ChooserWindow(self).present()

    def do_open(self, files, _count, _hint):
        if os.environ.get('LUMA_VALET_FIXTURE'):
            self.do_activate()
            return
        for source in files:
            path = source.get_path()
            if path: InstallerWindow(self, Path(path)).present()


def inspect_json(path):
    try:
        payload = asdict(inspect_package(path)); payload['path'] = str(payload['path'])
        print(json.dumps(payload, indent=2, sort_keys=True)); return 0
    except Exception as error:
        print(f'error: {error}', file=sys.stderr); return 1


def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get('LUMA_VALET_FIXTURE'):
        return InstallerApplication().run([sys.argv[0]])
    if len(values) == 2 and values[0] == '--inspect-json': return inspect_json(Path(values[1]))
    if len(values) == 2 and values[0] == '--remove': return InstallerApplication().run([sys.argv[0], *values])
    if values in ([], ['--choose']): return InstallerApplication().run([sys.argv[0]])
    if len(values) != 1 or values[0].startswith('-'):
        print('usage: luma-install [FILE | FOLDER] | --remove APP_ID', file=sys.stderr); return 2
    return InstallerApplication().run([sys.argv[0], str(Path(values[0]).expanduser().absolute())])
