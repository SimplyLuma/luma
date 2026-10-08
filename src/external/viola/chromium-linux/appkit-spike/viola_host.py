# SPDX-License-Identifier: GPL-3.0-only
"""Signed Viola UI's fixed private-compositor owner; no host commands or paths."""
import json
import os
from pathlib import Path
import re
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from gi.repository import Gio, GLib
from display_leases import DisplayLeases
from engine_display import EngineDisplay
from luma_installer.app_data_broker import authenticate

BUS = 'org.projectluma.ViolaHost1'
PATH = '/org/projectluma/ViolaHost1'
APP = 'com.rhyme.viola'
XML = '''<node><interface name="org.projectluma.ViolaHost1">
<method name="Acquire"><arg name="display" type="s" direction="out"/></method>
<method name="Release"><arg name="handle" type="s" direction="in"/></method>
</interface></node>'''


def runtime_directory():
    root = Path('/run/user') / str(os.getuid())
    st = root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise PermissionError('The browser runtime directory is unavailable.')
    path = root / 'luma-viola'
    path.mkdir(mode=0o700, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise PermissionError('The browser display directory is unsafe.')
    return path


class Service:
    def __init__(self, connection, factory=None, admission=authenticate):
        self.connection = connection
        self.admission = admission
        self.loop = GLib.MainLoop()
        self.work = ThreadPoolExecutor(max_workers=4, thread_name_prefix='viola-display')
        self.leases = DisplayLeases(factory or (lambda: EngineDisplay(sys.stderr, runtime_directory())))
        self.pending = 0
        self.touched = GLib.get_monotonic_time()
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = connection.register_object(PATH, node.interfaces[0], self.dispatch, None, None)
        self.subscription = connection.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
            'NameOwnerChanged', '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, self.changed)
        self.timer = GLib.timeout_add_seconds(15, self.idle)

    def idle(self):
        if (not self.pending and self.leases.empty()
                and GLib.get_monotonic_time() - self.touched > 60_000_000):
            self.loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def changed(self, connection, sender, path, interface, signal, parameters):
        name, previous, current = parameters.unpack()
        if name.startswith(':') and previous and not current:
            # A pending startup observes the removed reservation and closes its
            # compositor before returning. Closing an active display is bounded.
            displays = self.leases.detach(name)
            for display in displays:
                self.pending += 1
                future = self.work.submit(self.leases.close_detached, display)
                future.add_done_callback(lambda finished: GLib.idle_add(self.completed, None, 'Release', finished))

    def dispatch(self, connection, sender, path, interface, method, parameters, invocation):
        handle = None
        try:
            if self.admission(connection, sender) != APP:
                raise PermissionError('Only the signed Viola application can use this display.')
            if self.pending >= 4:
                raise RuntimeError('The browser display owner is busy.')
            if method == 'Acquire':
                if parameters.unpack():
                    raise ValueError('Acquire does not accept paths or commands.')
                handle = self.leases.reserve(sender)
                task = lambda: self.acquire(sender, handle)
            elif method == 'Release':
                handle, = parameters.unpack()
                if not re.fullmatch('[0-9a-f]{32}', handle):
                    raise ValueError('The display handle is invalid.')
                task = lambda: self.leases.release(sender, handle)
            else:
                raise ValueError('The browser display operation is unavailable.')
            self.pending += 1
            self.touched = GLib.get_monotonic_time()
            future = self.work.submit(task)
            future.add_done_callback(lambda finished: GLib.idle_add(self.completed, invocation, method, finished))
        except Exception as error:
            invocation.return_dbus_error(BUS + '.Refused', str(error)[:512])

    def acquire(self, sender, handle):
        display = self.leases.create(sender, handle)
        return json.dumps({'handle': handle, 'wayland_display': str(display.socket_path),
                           'private_bus_address': display.private_bus_address}, separators=(',', ':'))

    def completed(self, invocation, method, future):
        self.pending -= 1
        self.touched = GLib.get_monotonic_time()
        try:
            value = future.result()
            if invocation is not None:
                invocation.return_value(GLib.Variant('(s)', (value,)) if method == 'Acquire' else None)
        except Exception as error:
            if invocation is not None:
                invocation.return_dbus_error(BUS + '.Refused', str(error)[:512])
            else:
                print('Private display cleanup failed:', str(error)[:512], file=sys.stderr)
        return GLib.SOURCE_REMOVE

    def close(self):
        self.connection.signal_unsubscribe(self.subscription)
        self.connection.unregister_object(self.registration)
        self.work.shutdown(wait=True, cancel_futures=False)
        with self.leases.lock:
            owners = {sender for sender, display in self.leases.leases.values()}
        for owner in owners:
            self.leases.disconnect(owner)


def main():
    service = None
    loop = GLib.MainLoop()
    def acquired(connection, name):
        nonlocal service
        service = Service(connection)
        service.loop = loop
    owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.NONE, acquired,
                             None, lambda *args: loop.quit())
    try:
        loop.run()
    finally:
        if service is not None:
            service.close()
        Gio.bus_unown_name(owner)


if __name__ == '__main__':
    main()
