# SPDX-License-Identifier: Apache-2.0
"""Ask the authenticated host owner to prepare native data before app startup.

The application never sees or chooses a host filesystem path. Other Linux
hosts can run a fresh sandbox without a Luma migration owner. Luma baselines
require the owner; installing an app cannot silently reset native data.
"""
import configparser
import os
from pathlib import Path
import sys
from gi.repository import Gio, GLib

BUS = 'org.projectluma.AppData1'
PATH = '/org/projectluma/AppData1'

def prepare():
    info = configparser.ConfigParser(interpolation=None)
    info.read('/.flatpak-info')
    app_id = info.get('Application', 'name', fallback='')
    if not app_id:
        raise RuntimeError('Native-data preparation requires a real application sandbox.')
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        result = connection.call_sync(BUS, PATH, BUS, 'Prepare', None,
            GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 120000, None).unpack()[0]
        if result != 'ready': raise RuntimeError('The host did not confirm application data preparation.')
    except GLib.Error as error:
        missing = Gio.DBusError.is_remote_error(error) and Gio.DBusError.get_remote_error(error) in {
            'org.freedesktop.DBus.Error.ServiceUnknown', 'org.freedesktop.DBus.Error.NameHasNoOwner'}
        host = Path('/run/host/os-release')
        on_luma = host.exists() and any(line in {'ID=luma', 'ID="luma"'} for line in host.read_text().splitlines())
        if missing and not on_luma and os.environ.get('LUMA_NATIVE_DATA_MIGRATION') != 'required':
            return
        try:
            connection.call_sync(BUS, PATH, BUS, 'Cancel', None,
                GLib.VariantType.new('()'), Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass
        raise RuntimeError('Your existing data is preserved. Close the native app and try again, or review migration in Depot.') from error

def repair_application_id(info_path=Path('/.flatpak-info')):
    """Use the sandbox's own identity; a repair view needs no extra bus name."""
    if not info_path.exists():
        return 'org.projectluma.DataMigrationReview'
    info = configparser.ConfigParser(interpolation=None, strict=True)
    info.read_string(info_path.read_text())
    app_id = info.get('Application', 'name', fallback='')
    if not Gio.Application.id_is_valid(app_id):
        raise RuntimeError('The sandbox application identity is invalid.')
    return app_id


def repair_application():
    import gi
    gi.require_version('Adw', '1')
    from gi.repository import Adw
    return Adw.Application(application_id=repair_application_id(),
        flags=Gio.ApplicationFlags.NON_UNIQUE)


def review_window(application, message):
    """Build the real repair screen without replacing the shared frame."""
    from luma_appkit import AppWindow, CommandRegistry, EmptyState, install_appkit
    install_appkit()
    window = AppWindow(application=application,
        app_id=application.get_application_id(),
        title='Keep your application data', icon_name='dialog-warning-symbolic',
        commands=CommandRegistry(()), minimum_width=360, default_width=560,
        default_height=380)
    state = EmptyState('Your data is safe', message, 'dialog-warning-symbolic',
                       primary=('Close', lambda *_: application.quit()))
    window.set_body(state)
    return window, state


def show_failure(message):
    import gi
    gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
    application = repair_application()
    def activate(app):
        window, _state = review_window(app, message)
        window.present()
    application.connect('activate', activate)
    application.run([])

if __name__ == '__main__':
    try:
        prepare()
    except RuntimeError as error:
        show_failure(str(error))
        raise SystemExit(1)
    command = sys.argv[1:]
    if not command: raise SystemExit('A sandbox application command is required.')
    os.execvp(command[0], command)
