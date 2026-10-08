# SPDX-License-Identifier: Apache-2.0
"""Asynchronous installed-app discovery and portal-authorized file launch.

Native Linux applications continue using GIO. Sandboxes query the installed
Luma host owner; no sandbox executes a host desktop Exec line. Other Linux
hosts without this companion still have the standard portal's Other app
chooser. Discovery failure is never represented as successful delivery.
"""
from dataclasses import dataclass
import os
import re
from pathlib import Path
import threading
import time

from gi.repository import Gio, GLib

BUS = 'org.projectluma.ApplicationDirectory1'
OBJECT = '/org/projectluma/ApplicationDirectory1'
_cache = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)


def sandboxed():
    return Path('/.flatpak-info').is_file()


def _call(method, parameters, signature, cancellable=None):
    return Gio.bus_get_sync(Gio.BusType.SESSION, cancellable).call_sync(
        BUS, OBJECT, BUS, method, parameters, GLib.VariantType.new(signature),
        Gio.DBusCallFlags.NONE, 15000, cancellable).unpack()[0]


@dataclass(frozen=True)
class Application:
    identity: str
    name: str
    icon: str
    default: bool
    files: bool
    uris: bool

    def get_id(self): return self.identity
    def get_name(self): return self.name
    def get_display_name(self): return self.name
    def get_icon(self): return Gio.ThemedIcon.new(self.icon) if self.icon else None
    def should_show(self): return True
    def supports_files(self): return self.files
    def supports_uris(self): return self.uris


def applications(mime=''):
    """Read cached sandbox rows only. Use discover() to refresh without UI I/O."""
    if not sandboxed():
        return Gio.AppInfo.get_all_for_type(mime) if mime else Gio.AppInfo.get_all()
    with _lock:
        return list(_cache.get(mime, (0, []))[1])


def lookup(identity):
    if not sandboxed():
        try:
            return Gio.DesktopAppInfo.new(identity)
        except (TypeError, GLib.Error):
            return None
    with _lock:
        return next((app for _stamp, rows in _cache.values() for app in rows
                     if app.get_id() == identity), None)


def discover(mime, callback, *, cancellable=None):
    """callback(applications, error) on GTK; one bounded off-main-loop query."""
    cancel = cancellable or Gio.Cancellable()
    if not _slots.acquire(blocking=False):
        GLib.idle_add(lambda: (callback(applications(mime), 'Application discovery is busy'), False)[1])
        return cancel
    def work():
        rows, error = [], None
        try:
            if sandboxed():
                wire = _call('List', GLib.Variant('(s)', (mime,)), '(a(sssbbb))', cancel)
                if len(wire) > 4096:
                    raise ValueError('Application catalogue exceeds the supported limit')
                rows = [Application(*row) for row in wire]
            else:
                rows = Gio.AppInfo.get_all_for_type(mime) if mime else Gio.AppInfo.get_all()
            with _lock:
                if mime not in _cache and len(_cache) >= 64:
                    del _cache[min(_cache, key=lambda key: _cache[key][0])]
                _cache[mime] = (time.monotonic(), rows)
        except Exception as failure:
            error = str(failure)
        finally:
            _slots.release()
        def finish():
            if not cancel.is_cancelled():
                callback(rows, error)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(finish)
    threading.Thread(target=work, daemon=True, name='luma-app-discovery').start()
    return cancel


def _document_uri(connection, file, target, cancellable):
    """Export an actual readable FD through the existing document portal."""
    path = file.get_path()
    if path is None:
        uri = file.get_uri()
        if not uri.startswith(('http://', 'https://')):
            raise ValueError('This document cannot be sent to this application')
        return uri
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        descriptors = Gio.UnixFDList.new()
        index = descriptors.append(fd)
        result, _fds = connection.call_with_unix_fd_list_sync(
            'org.freedesktop.portal.Documents', '/org/freedesktop/portal/documents',
            'org.freedesktop.portal.Documents', 'Add', GLib.Variant('(hbb)', (index, False, False)),
            GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 15000,
            descriptors, cancellable)
        document = result.unpack()[0]
        # Portal document IDs are opaque: current portals use URL-safe tokens,
        # while older versions used hexadecimal. Only one bounded component
        # is accepted; the portal remains the authority for the actual grant.
        if not isinstance(document, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', document):
            raise ValueError('The document portal returned an invalid identity')
        app_id = target.removesuffix('.desktop')
        if Gio.Application.id_is_valid(app_id):
            connection.call_sync('org.freedesktop.portal.Documents', '/org/freedesktop/portal/documents',
                'org.freedesktop.portal.Documents', 'GrantPermissions',
                GLib.Variant('(ssas)', (document, app_id, ['read'])),
                GLib.VariantType.new('()'), Gio.DBusCallFlags.NONE, 15000, cancellable)
        mount = connection.call_sync('org.freedesktop.portal.Documents', '/org/freedesktop/portal/documents',
            'org.freedesktop.portal.Documents', 'GetMountPoint', None,
            GLib.VariantType.new('(ay)'), Gio.DBusCallFlags.NONE, 15000, cancellable).unpack()[0]
        base = os.fsdecode(bytes(mount).rstrip(b'\0'))
        if str(Path(base)) != f'/run/user/{os.getuid()}/doc':
            raise ValueError('The document portal has an unexpected mount')
        return Gio.File.new_for_path(str(Path(base) / document / Path(path).name)).get_uri()
    finally:
        os.close(fd)


def launch(identity, files=(), *, context=None, callback=None, cancellable=None):
    """Return a cancellable; callback fires after actual receiver acceptance."""
    cancel = cancellable or Gio.Cancellable()
    files = tuple(files)
    if len(files) > 256:
        if callback: GLib.idle_add(lambda: (callback(False, 'Select up to 256 documents at a time'), False)[1])
        return cancel
    if not _slots.acquire(blocking=False):
        if callback:
            GLib.idle_add(lambda: (callback(False, 'Application launch is busy'), False)[1])
        return cancel
    def work():
        accepted, error = False, None
        try:
            if not sandboxed():
                app = Gio.DesktopAppInfo.new(identity)
                if app is None: raise ValueError('This application is no longer installed')
                accepted = bool(app.launch(list(files), context))
            else:
                connection = Gio.bus_get_sync(Gio.BusType.SESSION, cancel)
                uris = [_document_uri(connection, file, identity, cancel) for file in files]
                if cancel.is_cancelled(): raise ValueError('Application launch was cancelled')
                accepted = bool(_call('Launch', GLib.Variant('(sas)', (identity, uris)), '(b)', cancel))
            if not accepted: raise ValueError('The application did not accept the document')
        except Exception as failure:
            error = str(failure)
        finally:
            _slots.release()
        def finish():
            if callback and not cancel.is_cancelled(): callback(accepted, error)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(finish)
    threading.Thread(target=work, daemon=True, name='luma-app-launch').start()
    return cancel
