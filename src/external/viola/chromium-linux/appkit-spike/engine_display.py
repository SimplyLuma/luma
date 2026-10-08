# SPDX-License-Identifier: GPL-3.0-only
"""Use installed Mutter for engine surfaces outside the user's desktop.

The same private Wayland/broker setup is used by headless_qa. Chromium retains
its native GPU compositor; only AppKit windows connect to the desktop display.
"""
import os
from pathlib import Path
import signal
import json
import re
import stat
import socket
import subprocess
import tempfile
import time
import uuid


class EngineDisplay:
    def __init__(self, log, runtime_directory=None):
        self.remote_connection = self.remote_handle = None
        self.socket_directory = None
        self.broker = self.compositor = None
        self.pointer_connection = self.pointer_session = None
        self.directory = None
        if os.environ.get("FLATPAK_ID"):
            self._acquire_host()
            return
        display_root = Path(os.environ['XDG_RUNTIME_DIR'])
        if runtime_directory is not None:
            self.socket_directory = tempfile.TemporaryDirectory(prefix='display-', dir=runtime_directory)
            display_root = Path(self.socket_directory.name)
        self.directory = tempfile.TemporaryDirectory(prefix='viola-engine-bus-',
            dir=display_root if runtime_directory is not None else None)
        root = Path(self.directory.name)
        self.private_bus_address = 'unix:path=' + str(root / 'bus')
        config = root / 'bus.conf'
        config.write_text('''<busconfig><type>session</type><auth>EXTERNAL</auth>
          <policy context="default"><allow own="*"/><allow send_destination="*"/>
          <allow receive_sender="*"/></policy></busconfig>''')
        self.display = 'viola-engine-' + uuid.uuid4().hex
        self.socket_path = display_root / self.display
        environment = dict(os.environ, DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(root / 'bus'),
                           WAYLAND_DISPLAY=self.display, XDG_RUNTIME_DIR=str(display_root), GTK_A11Y='none',
                           GSETTINGS_BACKEND='keyfile', XDG_CONFIG_HOME=str(root / 'config'))
        environment.pop('DISPLAY', None)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(root / 'bus'))
            listener.listen(32)
            descriptor = listener.fileno()
            setup = f'exec 3<&{descriptor}; '
            if descriptor != 3:
                setup += f'exec {descriptor}<&-; '
            setup += 'export LISTEN_PID=$$; exec "$@"'
            self.broker = subprocess.Popen(['/bin/bash', '-c', setup, 'viola-engine-bus',
                '/usr/bin/dbus-broker-launch', '--scope=user', '--config-file=' + str(config)],
                pass_fds=(descriptor,), env=environment | {'LISTEN_FDS': '1'},
                stdout=log, stderr=log, start_new_session=True)
            listener.close()
            self.compositor = subprocess.Popen(['/usr/bin/mutter', '--headless', '--wayland',
                '--no-x11', '--virtual-monitor=1920x1080@60', '--wayland-display=' + self.display],
                env=environment, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 15
            while not self.socket_path.exists():
                if self.broker.poll() is not None or self.compositor.poll() is not None:
                    raise RuntimeError('The private engine display stopped during startup')
                if time.monotonic() > deadline:
                    raise TimeoutError('The private engine display did not become ready')
                time.sleep(.05)
            if runtime_directory is None:
                self.establish_pointer(environment['DBUS_SESSION_BUS_ADDRESS'])
        except Exception:
            self.close()
            raise
        finally:
            listener.close()

    def _acquire_host(self):
        if os.environ['FLATPAK_ID'] != 'com.rhyme.viola':
            raise PermissionError('This private display belongs to Viola.')
        from gi.repository import Gio, GLib
        self.remote_connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        try:
            result = self.remote_connection.call_sync('org.projectluma.ViolaHost1',
                '/org/projectluma/ViolaHost1', 'org.projectluma.ViolaHost1', 'Acquire',
                None, GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 30000, None).unpack()[0]
            if len(result) > 1024:
                raise ValueError('The private display reply is oversized.')
            record = json.loads(result)
            handle = record.get('handle', '')
            if not re.fullmatch('[0-9a-f]{32}', handle):
                raise ValueError('The private display handle is invalid.')
            self.remote_handle = handle
            socket_path = Path(record['wayland_display'])
            root = Path('/run/user') / str(os.getuid()) / 'luma-viola'
            if (socket_path.parent.parent != root
                    or not re.fullmatch('display-[a-z0-9_]{8}', socket_path.parent.name)
                    or not re.fullmatch('viola-engine-[0-9a-f]{32}', socket_path.name)):
                raise ValueError('The private display path is invalid.')
            for parent in (root, socket_path.parent):
                st = parent.lstat()
                if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                    raise PermissionError('The private display directory is unsafe.')
            st = socket_path.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
                raise PermissionError('The private display socket is unsafe.')
            address = record.get('private_bus_address', '')
            if not isinstance(address, str) or not address.startswith('unix:path='):
                raise ValueError('The private display bus is unavailable.')
            bus_path = Path(address.removeprefix('unix:path='))
            if (bus_path.name != 'bus' or bus_path.parent.parent != socket_path.parent
                    or not re.fullmatch('viola-engine-bus-[a-z0-9_]{8}', bus_path.parent.name)):
                raise ValueError('The private display bus path is invalid.')
            st = bus_path.parent.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                raise PermissionError('The private display bus directory is unsafe.')
            st = bus_path.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
                raise PermissionError('The private display bus socket is unsafe.')
            self.socket_path = socket_path
            self.display = str(socket_path)
            self.private_bus_address = address
            self.establish_pointer(address)
        except Exception:
            self.close()
            raise

    def establish_pointer(self, address):
        # The owned headless compositor has no hardware seat. CDP events alone
        # do not advertise pointer capabilities, so websites see hover:none.
        # Reuse Mutter's virtual device support on this exact private bus;
        # never connect RemoteDesktop to the user's desktop session.
        from gi.repository import Gio, GLib
        self.pointer_connection = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        connection = self.pointer_connection
        service = 'org.gnome.Mutter.RemoteDesktop'
        deadline = time.monotonic() + 5
        while not connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (service,)),
                None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]:
            if time.monotonic() >= deadline:
                raise TimeoutError('Private engine pointer service did not become ready')
            time.sleep(.05)
        def call(path, interface, method, params=None):
            return connection.call_sync(service, path, interface, method, params,
                None, Gio.DBusCallFlags.NONE, 5000, None)
        self.pointer_session = call('/org/gnome/Mutter/RemoteDesktop', service, 'CreateSession').unpack()[0]
        call(self.pointer_session, service + '.Session', 'Start')
        # This creates Mutter's virtual pointer before Chromium connects. No
        # page is open yet, and actual input remains on the target-bound pipe.
        call(self.pointer_session, service + '.Session', 'NotifyPointerMotionRelative',
             GLib.Variant('(dd)', (0.0, 0.0)))

    def environment(self):
        # Browser services, portals, audio, and credentials retain the user's
        # session bus. Only its Wayland surfaces use the private compositor.
        environment = dict(os.environ, WAYLAND_DISPLAY=self.display)
        environment.pop('DISPLAY', None)
        return environment

    def close(self):
        if self.pointer_connection:
            from gi.repository import Gio
            try:
                if self.pointer_session:
                    self.pointer_connection.call_sync('org.gnome.Mutter.RemoteDesktop',
                        self.pointer_session, 'org.gnome.Mutter.RemoteDesktop.Session',
                        'Stop', None, None, Gio.DBusCallFlags.NONE, 1000, None)
            except Exception:
                pass  # The owned compositor may already have stopped.
            finally:
                self.pointer_connection.close_sync(None)
                self.pointer_connection = self.pointer_session = None
        if self.remote_connection is not None:
            from gi.repository import Gio, GLib
            connection, handle = self.remote_connection, self.remote_handle
            self.remote_connection = self.remote_handle = None
            if handle:
                try:
                    connection.call_sync('org.projectluma.ViolaHost1', '/org/projectluma/ViolaHost1',
                        'org.projectluma.ViolaHost1', 'Release', GLib.Variant('(s)', (handle,)),
                        None, Gio.DBusCallFlags.NONE, 10000, None)
                except GLib.Error:
                    pass  # The host also reclaims the display on sender loss.
            return
        for process in (self.compositor, self.broker):
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        if self.directory is not None:
            self.directory.cleanup()
        if self.socket_directory is not None:
            self.socket_directory.cleanup()
