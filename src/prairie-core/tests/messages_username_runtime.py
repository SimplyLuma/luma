#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual shared native username form; private fixture service, no account/network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
# RPM Source3 and source runs use the same production appearance contract.
schemas = tempfile.TemporaryDirectory(prefix='username-appearance-')
schema = Path(os.environ.get('LUMA_CREATOR_SCHEMA_FILE',
    str(Path(__file__).resolve().parents[3] / 'src/luma-shell-state/org.project_luma.shell-state.gschema.xml')))
assert schema.is_file(), 'Canonical appearance schema is required'
shutil.copyfile(schema, Path(schemas.name) / schema.name)
subprocess.run(['glib-compile-schemas', '--strict', schemas.name], check=True)
os.environ['GSETTINGS_SCHEMA_DIR'] = schemas.name
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import AppWindow, Card, CommandRegistry, TextButton
from prairie_apps.messages_luma_ui import LumaProfileDialog
from prairie_apps.messages import MessagesWindow
from prairie_apps.messages_accounts import Accounts
from prairie_apps.messages_luma import connect_device_file
from native_input import outside_click


def pump(seconds=.15):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(.003)


def inside(widget, ancestor):
    ok, rect = widget.compute_bounds(ancestor)
    assert ok and widget.get_mapped() and widget.get_width() > 0 and widget.get_height() > 0
    assert rect.get_x() >= 0 and rect.get_y() >= 0, (widget.get_name(), rect)
    assert rect.get_x() + rect.get_width() <= ancestor.get_width() + 1, (widget.get_name(), rect, ancestor.get_width())
    assert rect.get_y() + rect.get_height() <= ancestor.get_height() + 1, (widget.get_name(), rect, ancestor.get_height())
    return [rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()]


class Service:
    def __init__(self):
        self.checks = []
        self.claims = []

    def call(self, _service, command, args, done):
        if command == 'luma.identity':
            GLib.idle_add(lambda: (done({'rules': {'min': 3, 'max': 30},
                                       'suggestions': ['alex.rivera', 'alexrivera', 'alex.r', 'rivera.alex', 'alex_rivera']}, None), False)[1])
        elif command == 'luma.handle.check':
            self.checks.append((dict(args), done))
        elif command == 'luma.handle.claim':
            self.claims.append((dict(args), done))
        else:
            raise AssertionError(command)


app = Adw.Application(application_id='org.projectluma.Messages.UsernameQualification', flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
appearance = Gio.Settings.new('org.project_luma.shell-state')
results = []
for dark in (False, True):
    appearance.set_string('surface-treatment', 'dark' if dark else 'light')
    Gio.Settings.sync(); pump()
    assert Adw.StyleManager.get_default().get_dark() == dark
    for width in (360, 500, 1024, 1440):
        window = AppWindow(application=app, app_id='org.projectluma.Messages.UsernameQualification',
                           title='Messages', icon_name='org.projectluma.Messages', commands=CommandRegistry(()),
                           default_width=width, default_height=740, minimum_width=360, minimum_height=440)
        window.set_body(TextButton('Messages'))
        service = Service(); changed = []
        window.luma_call = service.call
        window.luma_profile_changed = lambda s: changed.append(s)
        window.present(); pump(); window.set_default_size(width, 520 if width == 360 else 740); pump(.4)
        dialog = LumaProfileDialog(window, service); dialog.present(window); pump(.4)
        assert isinstance(dialog, Card)
        bounds = inside(dialog.claim_button, window)
        inside(dialog.claim_button, dialog.main.toolbar)
        assert isinstance(dialog.claim_button, TextButton) and dialog.claim_button.has_css_class('key')
        assert dialog.claim_button.get_accessible_role() == Gtk.AccessibleRole.BUTTON
        assert not dialog.claim_button.get_sensitive()
        dialog.claim_button.emit('clicked'); dialog.handle_entry.emit('activate')
        assert not service.claims, 'empty/unchecked username cannot submit through either path'
        dialog.handle_entry.set_text('alex.rivera'); pump(.4)
        assert service.checks[-1][0] == {'handle': 'alex.rivera'}
        # A check completing after the field is cleared must not enable submit.
        pending = service.checks[-1][1]
        dialog.handle_entry.set_text(''); pending({'available': True}, None); pump()
        assert not dialog.claim_button.get_sensitive()
        dialog.handle_entry.set_text('alex.rivera'); pump(.4)
        service.checks[-1][1]({'available': False, 'reason': 'Synthetic username is taken'}, None); pump()
        assert not dialog.claim_button.get_sensitive()
        dialog.handle_entry.set_text('alex.rivera2'); pump(.4)
        service.checks[-1][1]({'available': True}, None); pump()
        assert dialog.claim_button.get_sensitive()
        # Mouse action is the same single-flight transaction as Enter.
        click = inside(dialog.claim_button, window)
        outside_click(window, round(click[0] + click[2] / 2), round(click[1] + click[3] / 2)); pump()
        dialog.handle_entry.emit('activate'); dialog.claim_button.emit('clicked')
        assert len(service.claims) == 1 and service.claims[0][0] == {'handle': 'alex.rivera2'}
        assert not dialog.handle_entry.get_sensitive()
        service.claims[0][1](None, SimpleNamespace(message='Synthetic server refused this username. ' * 8)); pump()
        assert dialog.claim_button.get_sensitive() and dialog.handle_entry.get_sensitive()
        assert dialog.handle_entry.get_text() == 'alex.rivera2'
        assert dialog.handle_status.has_css_class('error') and not dialog.handle_status.has_css_class('success')
        inside(dialog.claim_button, window)
        output = os.environ.get('LUMA_MESSAGES_USERNAME_OUTPUT')
        if output:
            out = Path(output); out.mkdir(parents=True, exist_ok=True)
            snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(window).snapshot(snap, window.get_width(), window.get_height())
            texture = window.get_renderer().render_texture(snap.to_node(), None)
            assert texture.save_to_png(str(out / f'username-{width}-{"dark" if dark else "light"}.png'))
        if width == 360:
            settings = Gtk.Settings.get_default(); original_font = settings.get_property('gtk-font-name')
            settings.set_property('gtk-font-name', 'Figtree 18')
            Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL); pump(.3)
            inside(dialog.claim_button, window)
            inside(dialog.claim_button, dialog.main.toolbar)
            settings.set_property('gtk-font-name', original_font)
            Gtk.Widget.set_default_direction(Gtk.TextDirection.LTR)
            a11y = Gio.Settings.new('org.gnome.desktop.a11y.interface')
            assert a11y.set_boolean('high-contrast', True); Gio.Settings.sync(); pump(.3)
            from luma_appkit import widgets
            assert widgets.appearance['name'] == 'high-contrast'
            inside(dialog.claim_button, window)
            assert a11y.set_boolean('high-contrast', False); Gio.Settings.sync(); pump()
        dialog.handle_entry.emit('activate')
        assert len(service.claims) == 2
        service.claims[-1][1]({'handle': 'alex.rivera2', 'name': 'Synthetic Person'}, None); pump()
        assert changed == [service] and not dialog.main.actions.get_visible()
        dialog.close(); pump()
        # Completion after dismissal cannot claim or update the dismissed view.
        dialog = LumaProfileDialog(window, service); dialog.present(window); pump()
        dialog.handle_entry.set_text('new.username'); pump(.4)
        callback = service.checks[-1][1]; dialog.close()
        callback({'available': True}, None); dialog.claim_button.emit('clicked'); pump()
        assert len(service.claims) == 2
        dialog = LumaProfileDialog(window, service); dialog.present(window); pump()
        dialog.handle_entry.set_text('new.username'); pump(.4)
        service.checks[-1][1]({'available': True}, None)
        dialog.claim_button.emit('clicked'); assert len(service.claims) == 3
        callback = service.claims[-1][1]; dialog.close()
        callback({'handle': 'new.username'}, None); pump()
        assert changed == [service], 'dismissed claim completion cannot update the window'
        results.append({'width': width, 'dark': dark, 'submit_bounds': bounds,
                        'visible_action': 'pass', 'mouse_and_enter': 'pass', 'single_flight': 'pass',
                        'stale_check': 'pass', 'error_retry': 'pass', 'dismissed_callback': 'pass'})
        window.destroy(); pump()
assert len(results) == 8
# Use the actual explicit profile action after a simulated login occurring
# after startup. File/account ownership is real; only the provider attachment
# seam is synthetic, keeping networking and E2EE credentials out of this gate.
with tempfile.TemporaryDirectory(prefix='username-discovery-') as temporary:
    root = Path(temporary); original_data = os.environ.get('XDG_DATA_HOME')
    os.environ['XDG_DATA_HOME'] = str(root / 'data')
    try:
        window = AppWindow(application=app, app_id='org.projectluma.Messages.UsernameQualification',
                           title='Messages', icon_name='org.projectluma.Messages', commands=CommandRegistry(()),
                           default_width=500, default_height=740, minimum_width=360, minimum_height=440)
        window.set_body(TextButton('Messages')); window.present(); pump()
        window.accounts = Accounts(root / 'accounts')
        window.helper_directory = root / 'helpers'; window.helper_directory.mkdir()
        window.profile_service = None; attached = []; notices = []
        window._luma_service = lambda: window.profile_service
        window._notice = notices.append
        def attach(account):
            attached.append(account.id)
            window.profile_service = SimpleNamespace(account=account)
        window.account_added = attach
        fixture = Service(); window.luma_call = fixture.call; window.luma_profile_changed = lambda *_: None
        MessagesWindow._show_luma_profile(window)
        assert 'support is missing' in notices[-1] and window.accounts.list() == [] and not attached
        # A relay login alone is not a Cloud enrolment; no account is invented.
        helper = window.helper_directory / 'luma'; helper.write_text('#!/bin/sh\nexit 1\n'); helper.chmod(0o755)
        MessagesWindow._show_luma_profile(window)
        assert 'connect this computer to Luma Cloud' in notices[-1] and window.accounts.list() == [] and not attached
        device = connect_device_file(); device.parent.mkdir(parents=True); device.write_text('{}')
        MessagesWindow._show_luma_profile(window); pump(.4)
        assert len(attached) == 1 and len(window.accounts.list()) == 1
        assert window.luma_dialog.claim_button.get_mapped()
        MessagesWindow._show_luma_profile(window); pump()
        assert len(attached) == 1 and len(window.accounts.list()) == 1, 'repeat profile intent cannot create a second account/provider'
        window.luma_dialog.close(); window.destroy(); pump()
    finally:
        if original_data is None:
            os.environ.pop('XDG_DATA_HOME', None)
        else:
            os.environ['XDG_DATA_HOME'] = original_data
print(json.dumps({'result': 'pass', 'cases': results, 'explicit_discovery': {
    'missing_helper': 'pass', 'missing_enrolment': 'pass', 'login_after_start': 'pass', 'idempotent': 'pass'}}, sort_keys=True))
