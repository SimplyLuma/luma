# SPDX-License-Identifier: Apache-2.0
"""In-app Flatpak updates delegated to the installed host's update portal.

No repository URLs, executable downloads, host commands or privileged writes
belong to this client. Flatpak owns trust, dependencies, permission comparison
and deployment. One monitor is shared by an application's windows. The source
API is flatpak/data/org.freedesktop.portal.Flatpak.xml (Flatpak 1.16.1).
"""
from __future__ import annotations

import configparser
from pathlib import Path
import re
import uuid

from gi.repository import Gio, GLib, GObject

BUS = 'org.freedesktop.portal.Flatpak'
PATH = '/org/freedesktop/portal/Flatpak'
MONITOR = BUS + '.UpdateMonitor'
PREFIX = PATH + '/update_monitor/'
COMMIT = re.compile(r'[0-9a-f]{64}\Z')
APP_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){2,}\Z')
REVIEW_ERRORS = {'org.freedesktop.DBus.Error.NotSupported',
                 'org.freedesktop.DBus.Error.AccessDenied'}


def running_flatpak(app_id: str, info_path=Path('/.flatpak-info')) -> bool:
    """Use the running sandbox identity, never an environment URL or app label."""
    try:
        with info_path.open('r', encoding='utf-8') as stream:
            text = stream.read(65537)
        if len(text) > 65536 or not APP_ID.fullmatch(app_id or ''):
            return False
        info = configparser.ConfigParser(interpolation=None, strict=True)
        info.read_string(text)
        return info.get('Application', 'name', fallback='') == app_id
    except (OSError, ValueError, configparser.Error):
        return False


class AppUpdates(GObject.Object):
    """A non-blocking update monitor; success means a terminal portal result."""
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, app_id):
        super().__init__()
        self.app_id = app_id
        self.state = 'checking'
        self.message = ''
        self.running = self.local = self.remote = ''
        self.connection = None
        self.handle = ''
        self.subscription = 0
        self.owner_watch = 0
        self.owner = ''
        self.created = False
        self.completion = 0
        self.cancellable = Gio.Cancellable()
        self.closed = False
        self.started = False
        self.generation = 0
        self.have_commits = False

    def _set(self, state, message=''):
        if not self.closed:
            self.state, self.message = state, message
            self.emit('changed')

    def start(self):
        if self.closed or self.started:
            return
        self.started = True
        Gio.bus_get(Gio.BusType.SESSION, self.cancellable, self._connected)

    def _connected(self, _source, result):
        try:
            connection = Gio.bus_get_finish(result)
        except GLib.Error:
            self._set('unavailable', 'Check for updates in your software manager.')
            return
        if self.closed:
            return
        self.connection = connection
        self.owner_watch = Gio.bus_watch_name_on_connection(
            connection, BUS, Gio.BusNameWatcherFlags.AUTO_START,
            self._owner_appeared, self._owner_vanished)

    def _request(self):
        return self.generation, self.owner, self.handle

    def _current(self, request):
        return not self.closed and request == self._request()

    def _owner_appeared(self, connection, _name, owner):
        if self.closed or owner == self.owner:
            return
        if self.created:
            self._close_handle(connection, self.owner, self.handle)
        self.generation += 1
        self._unsubscribe()
        self.owner = owner
        self.created = self.have_commits = False
        self._set('checking')
        token = 'luma_' + uuid.uuid4().hex
        sender = connection.get_unique_name().removeprefix(':').replace('.', '_')
        self.handle = PREFIX + sender + '/' + token
        # Subscribe to this unique owner before creation. A valid initial
        # snapshot stays pending until this exact monitor is acknowledged.
        self.subscription = connection.signal_subscribe(
            owner, MONITOR, None, self.handle, None, Gio.DBusSignalFlags.NONE, self._signal)
        connection.call(owner, PATH, BUS, 'CreateUpdateMonitor',
                        GLib.Variant('(a{sv})', ({'handle_token': GLib.Variant('s', token)},)),
                        GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, 10000,
                        None, self._created, self._request())

    def _owner_vanished(self, connection, _name):
        if self.closed:
            return
        if self.created:
            self._close_handle(connection, self.owner, self.handle)
        self.generation += 1
        self.owner = ''
        self.created = self.have_commits = False
        self._unsubscribe()
        self._set('unavailable', 'The update service stopped. Check your software manager to retry.')

    def _commits_state(self):
        if self.created and self.have_commits:
            self._set('available' if self.remote != self.local else
                      'ready' if self.local != self.running else 'current')

    def _created(self, connection, result, request):
        try:
            handle, = connection.call_finish(result).unpack()
        except GLib.Error:
            if self._current(request):
                self._close_handle(connection, request[1], request[2])
                self._unsubscribe()
                self._set('unavailable', 'Check for updates in your software manager.')
            return
        owned = handle == request[2]
        if not self._current(request):
            if owned:
                self._close_handle(connection, request[1], request[2])
            return
        if not owned:
            # Reap our generated path, never a foreign path returned by a peer.
            self._close_handle(connection, request[1], request[2])
            self._unsubscribe()
            self._set('unavailable', 'Check for updates in your software manager.')
            return
        self.created = True
        self._commits_state()

    def _signal(self, _connection, _sender, _path, _interface, name, parameters):
        if (self.closed or _sender != self.owner or _path != self.handle
                or not parameters.is_of_type(GLib.VariantType.new('(a{sv})'))
                or parameters.get_size() > 65536):
            return
        info, = parameters.unpack()
        if name == 'UpdateAvailable':
            commits = [info.get(key, '') for key in ('running-commit', 'local-commit', 'remote-commit')]
            if not all(isinstance(value, str) and COMMIT.fullmatch(value) for value in commits):
                return
            self.running, self.local, self.remote = commits
            self.have_commits = True
            if self.state == 'installing':
                return
            self._commits_state()
        elif name == 'Progress' and self.state == 'installing':
            status = info.get('status')
            if type(status) is not int:
                return
            if status == 2:
                # The currently running process keeps its old code. Never
                # restart or discard a document as part of installing an update.
                self.completion += 1
                self.local = self.remote
                self._set('ready', 'The update will be used when you reopen this app.')
            elif status == 1:
                self._set('ready' if self.local != self.running else 'current')
            elif status == 3:
                self._set('review' if info.get('error') in REVIEW_ERRORS
                          else 'failed', 'Open your software manager to review or retry this update.')

    def action_enabled(self) -> bool:
        return (not self.closed and (self.state in ('review', 'unavailable') or
                self.created and self.state not in ('checking', 'installing')))

    def install(self) -> bool:
        if (self.closed or not self.created or not self.owner
                or self.state not in ('available', 'failed') or self.connection is None):
            return False
        self._set('installing')
        self.connection.call(self.owner, self.handle, MONITOR, 'Update',
                             GLib.Variant('(sa{sv})', ('', {})), None,
                             Gio.DBusCallFlags.NONE, 5000, self.cancellable, self._requested, self._request())
        return True

    def _requested(self, connection, result, request):
        try:
            connection.call_finish(result)
            # An accepted request is not a completed installation. Progress
            # supplies the terminal result, possibly before this reply.
        except GLib.Error as error:
            if not self._current(request) or self.state != 'installing':
                return
            remote = Gio.DBusError.get_remote_error(error)
            self._set('review' if remote in REVIEW_ERRORS else 'failed',
                      'Open your software manager to review or retry this update.')

    def _unsubscribe(self):
        if self.subscription and self.connection is not None:
            self.connection.signal_unsubscribe(self.subscription)
        self.subscription = 0

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.generation += 1
        self.cancellable.cancel()
        self._unsubscribe()
        if self.owner_watch:
            Gio.bus_unwatch_name(self.owner_watch)
            self.owner_watch = 0
        if self.created and self.connection is not None and self.handle:
            self._close_handle(self.connection, self.owner, self.handle)
        self.created = False
        self.connection = None
        self.emit('changed')

    @staticmethod
    def _close_handle(connection, owner, handle):
        if owner and owner.startswith(':') and handle and handle.startswith(PREFIX):
            connection.call(owner, handle, MONITOR, 'Close', None, None,
                            Gio.DBusCallFlags.NONE, 5000, None, None)


def for_application(application):
    app_id = application.get_application_id()
    if not running_flatpak(app_id):
        return None
    controller = getattr(application, '_lumaui_app_updates', None)
    if controller is None:
        controller = AppUpdates(app_id)
        application._lumaui_app_updates = controller
        application.connect('shutdown', lambda *_: controller.close())
        controller.start()
    return controller


def refresh_native_action(window):
    """Mirror the shared updater's state after native menu action creation."""
    controller = getattr(window, '_app_updates', None)
    if controller is None:
        return
    action = window.get_application().lookup_action('luma-application-update')
    if action is not None:
        action.set_enabled(controller.action_enabled())


def window_commands(window, registry):
    """Same app-menu affordance in every Python AppWindow, on stock Linux too."""
    controller = for_application(window.get_application())
    if controller is None:
        return registry
    from .commands import Command, CommandGroup, CommandRegistry
    from gi.repository import Adw, Gtk
    from .action_toast import Toast
    window._app_updates = controller
    announced = set()

    def changed(*_args):
        refresh_native_action(window)
        if controller.closed:
            return
        available = ('available', controller.remote)
        ready = ('ready', controller.local, controller.completion)
        if controller.state == 'available' and available not in announced:
            announced.add(available)
            Toast.show(window, 'An update is available in the app menu', kind='notified')
        elif controller.state == 'ready' and ready not in announced:
            announced.add(ready)
            Toast.show(window, 'Update ready. Reopen this app when you are ready.', kind='done')

    signal = 0
    def mapped(*_args):
        nonlocal signal
        if not signal:
            signal = controller.connect('changed', changed)
        changed()
    def unrealized(*_args):
        nonlocal signal
        if signal and controller.handler_is_connected(signal):
            controller.disconnect(signal)
        signal = 0
    window.connect('unrealize', unrealized)

    def manager():
        # The host routes appstream through its installed software manager;
        # this never invokes a shell or downloads an executable from a web URL.
        launcher = Gtk.UriLauncher.new('appstream://' + controller.app_id)
        def opened(source, result):
            try:
                source.launch_finish(result)
            except GLib.Error:
                dialog = Adw.AlertDialog(heading='Open your software manager',
                    body='Find this app in your software manager and choose Update.')
                dialog.add_response('close', 'Close')
                dialog.present(window)
        launcher.launch(window, None, opened)

    def update():
        if controller.state in ('review', 'unavailable'):
            manager()
        elif controller.state in ('available', 'failed'):
            controller.install()
        elif controller.state == 'ready':
            Toast.show(window, 'Save your work, then reopen this app to use the update.')
        elif controller.state == 'current':
            Toast.show(window, 'This app is up to date.')

    # Menus are constructed before the asynchronous initial monitor reply.
    # Keep one stable row so every later state remains reachable in both the
    # native application menu and the shared toolkit's menu.
    command = Command('luma.application.update', 'App updates', update, icon='download',
                      enabled=controller.action_enabled)
    result = CommandRegistry((*registry.groups, CommandGroup(None, (command,))))
    # Signals arriving before the window maps remain discoverable in its menu.
    window.connect('map', mapped)
    return result
