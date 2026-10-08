# SPDX-License-Identifier: Apache-2.0
"""Installed desktop application discovery for signed sandbox callers.

Only desktop identities are accepted. Executables, argv, desktop file paths
and environment are never supplied by the caller. File launch authority is a
document portal grant, not a path into another application's private data.
"""
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, unquote

from gi.repository import Gio, GLib

BUS = 'org.projectluma.ApplicationDirectory1'
OBJECT = '/org/projectluma/ApplicationDirectory1'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,250}\.desktop\Z')
MIME = re.compile(r'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+\Z')
XML = '''<node><interface name="org.projectluma.ApplicationDirectory1">
<method name="List"><arg type="s" direction="in" name="mime"/>
<arg type="a(sssbbb)" direction="out" name="applications"/></method>
<method name="Launch"><arg type="s" direction="in" name="desktop_id"/>
<arg type="as" direction="in" name="uris"/><arg type="b" direction="out" name="accepted"/></method>
<signal name="Changed"/>
</interface></node>'''


def validate_mime(value):
    if not isinstance(value, str) or len(value) > 127 or (value and not MIME.fullmatch(value)):
        raise ValueError('Invalid application content type.')
    return value


def validate_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid installed application identity.')
    return value


def validate_uris(values, uid=None, *, check_files=True):
    if not isinstance(values, (list, tuple)) or len(values) > 256:
        raise ValueError('Too many application documents.')
    uid = os.getuid() if uid is None else uid
    total = 0
    for uri in values:
        if not isinstance(uri, str) or not uri or len(uri) > 4096 or any(ord(c) < 32 for c in uri):
            raise ValueError('Invalid application document.')
        total += len(uri)
        if total > 1048576:
            raise ValueError('Application documents exceed the request limit.')
        parts = urlsplit(uri)
        if parts.scheme in {'http', 'https'}:
            if not parts.hostname or parts.username or parts.password:
                raise ValueError('Invalid web address.')
            continue
        if parts.scheme != 'file' or parts.netloc or parts.query or parts.fragment:
            raise ValueError('Only web addresses and document portal files can be opened.')
        path = Path(unquote(parts.path))
        base = Path(f'/run/user/{uid}/doc')
        if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(base):
            raise ValueError('The file has no document portal grant.')
        relative = path.relative_to(base)
        if len(relative.parts) != 2 or not re.fullmatch('[A-Za-z0-9_-]{1,64}', relative.parts[0]):
            raise ValueError('Invalid document portal identity.')
        if check_files and not path.is_file():
            raise ValueError('The shared document is unavailable.')
    return list(values)


def application_rows(mime):
    validate_mime(mime)
    default = Gio.AppInfo.get_default_for_type(mime, False) if mime else None
    default_id = default.get_id() if default else None
    apps = Gio.AppInfo.get_all_for_type(mime) if mime else Gio.AppInfo.get_all()
    result, seen, size = [], set(), 0
    for app in apps:
        identity = app.get_id()
        if not identity or not ID.fullmatch(identity) or identity in seen or not app.should_show():
            continue
        if mime and not (app.supports_files() or app.supports_uris()):
            continue
        name = app.get_display_name() or identity.removesuffix('.desktop')
        # Native icon file paths are not exported into the sandbox. Theme
        # names resolve to the sandbox's shipped/shared icon theme.
        icon = app.get_icon()
        names = icon.get_names() if isinstance(icon, Gio.ThemedIcon) else []
        icon_name = next((n for n in names if re.fullmatch('[A-Za-z0-9_.-]{1,255}', n)), '')
        name = name[:512]
        size += len(identity.encode()) + len(name.encode()) + len(icon_name.encode()) + 32
        if len(result) >= 4096 or size > 1048576:
            raise ValueError('Installed application catalogue exceeds the supported limit.')
        seen.add(identity)
        result.append((identity, name, icon_name, identity == default_id,
                       bool(app.supports_files()), bool(app.supports_uris())))
    return sorted(result, key=lambda r: (not r[3], r[1].casefold(), r[0]))


def launch_application(identity, uris):
    validate_id(identity)
    values = validate_uris(uris)
    app = Gio.DesktopAppInfo.new(identity)
    if app is None or not app.should_show():
        raise ValueError('This application is no longer installed.')
    if values:
        if app.supports_uris():
            return bool(app.launch_uris(values, None))
        if app.supports_files() and all(uri.startswith('file:') for uri in values):
            return bool(app.launch([Gio.File.new_for_uri(uri) for uri in values], None))
        raise ValueError('This application cannot receive these documents.')
    return bool(app.launch([], None))


class Broker:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.bus = None
        self.active = {}
        self.changed_source = 0
        self.last = time.monotonic()
        self.monitor = Gio.AppInfoMonitor.get()
        self.monitor.connect('changed', self._changed)
        self.owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.NONE,
            self._acquired, None, lambda *_: self.loop.quit())
        self.idle = GLib.timeout_add_seconds(30, self._idle)

    def _idle(self):
        if self.active or time.monotonic() - self.last < 60:
            return GLib.SOURCE_CONTINUE
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _changed(self, *_):
        if self.changed_source:
            return
        def emit():
            self.changed_source = 0
            if self.bus:
                self.bus.emit_signal(None, OBJECT, BUS, 'Changed', None)
            return GLib.SOURCE_REMOVE
        self.changed_source = GLib.timeout_add(250, emit)

    def _acquired(self, connection, *_):
        self.bus = connection
        connection.register_object(OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self._call, None, None)

    def _call(self, connection, sender, _path, _interface, method, args, invocation):
        from .app_data_broker import authenticate
        from concurrent.futures import ThreadPoolExecutor
        try:
            if method not in {'List', 'Launch'}:
                raise ValueError('Unsupported application operation.')
            values = args.unpack()
            if method == 'List':
                validate_mime(values[0])
            else:
                validate_id(values[0]); validate_uris(values[1], check_files=False)
            if len(self.active) >= 4 or sender in self.active:
                raise ValueError('Application discovery is already working. Try again shortly.')
            # authenticate is performed off the GTK/D-Bus main loop because
            # validating real retained signed deployments can read OSTree.
        except Exception as error:
            invocation.return_dbus_error(BUS + '.Refused', str(error)[:512]); return
        self.last = time.monotonic()
        cancelled = Gio.Cancellable()
        self.active[sender] = cancelled
        watch = Gio.bus_watch_name_on_connection(connection, sender, Gio.BusNameWatcherFlags.NONE,
            lambda *_: None, lambda *_: cancelled.cancel())
        if not hasattr(self, 'executor'):
            self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='luma-app-directory')
        def work():
            try:
                authenticate(connection, sender)
                if cancelled.is_cancelled():
                    raise ValueError('The requesting application closed.')
                result = application_rows(values[0]) if method == 'List' else launch_application(*values)
                GLib.idle_add(finish, result, '')
            except Exception as error:
                GLib.idle_add(finish, None, str(error)[:512])
        def finish(result, error):
            self.active.pop(sender, None)
            Gio.bus_unwatch_name(watch)
            if not cancelled.is_cancelled():
                if error:
                    invocation.return_dbus_error(BUS + '.Refused', error)
                else:
                    invocation.return_value(GLib.Variant('(a(sssbbb))' if method == 'List' else '(b)', (result,)))
            return GLib.SOURCE_REMOVE
        self.executor.submit(work)

    def run(self):
        try:
            self.loop.run()
        finally:
            for cancel in self.active.values():
                cancel.cancel()
            if hasattr(self, 'executor'):
                self.executor.shutdown(wait=True, cancel_futures=True)
            Gio.bus_unown_name(self.owner)


if __name__ == '__main__':
    Broker().run()
