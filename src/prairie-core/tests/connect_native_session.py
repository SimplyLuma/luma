#!/usr/bin/python3
"""Run native checks with real accessibility/GTK portal services on a private bus.

Ad-hoc dbus-daemon on Fedora can enter a role/domain combination which cannot
activate at-spi. Launching the actual service from the normal builder context
preserves enforcing policy and accessibility without a mock or a test skip.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def stop_process_group(process, root=None):
    """Reap the private session's descendants even after its leader exits.

    D-Bus-activated portal/GVFS processes can outlive dbus-run-session and
    still write the private XDG directories. Zombies cannot write them, but
    every live member of the session group must stop before directory removal.
    """
    def live_members():
        members = {}
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rpartition(')')[2].split()
                if fields[0] == 'Z':
                    continue
                owned = int(fields[2]) == process.pid
                # A provider may daemonize into another process group. Its
                # two exact private XDG paths still identify this session;
                # never select services by command name or a broad prefix.
                if not owned and root is not None:
                    environment = (path.parent / 'environ').read_bytes().split(b'\0')
                    owned = (os.fsencode('XDG_RUNTIME_DIR=' + str(root / 'runtime')) in environment
                             and os.fsencode('XDG_CONFIG_HOME=' + str(root / 'config')) in environment)
                if owned:
                    members[int(path.parent.name)] = fields[19]
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        return members

    def signal_group(sig):
        members = live_members()
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        for pid, started in members.items():
            try:
                fields = Path('/proc', str(pid), 'stat').read_text().rpartition(')')[2].split()
                if fields[19] == started:
                    os.kill(pid, sig)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass

    signal_group(signal.SIGTERM)
    deadline = time.monotonic() + 5
    while live_members() and time.monotonic() < deadline:
        time.sleep(.025)
    if live_members():
        signal_group(signal.SIGKILL)
        deadline = time.monotonic() + 5
        while live_members() and time.monotonic() < deadline:
            time.sleep(.025)
    process.wait(timeout=5)
    remaining = live_members()
    if remaining:
        raise RuntimeError('Private native session descendants did not stop: ' + str(remaining))


def session(command):
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    children = []

    def wait_for_name(bus, name):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('Native accessibility service exited')
            value = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (name,)),
                None, Gio.DBusCallFlags.NONE, 1000, None)
            if value.unpack()[0]:
                return
            time.sleep(.05)
        raise RuntimeError('Native service did not acquire ' + name)

    try:
        children.append(subprocess.Popen(['/usr/libexec/at-spi-bus-launcher', '--launch-immediately']))
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        wait_for_name(bus, 'org.a11y.Bus')
        address = bus.call_sync('org.a11y.Bus', '/org/a11y/bus', 'org.a11y.Bus',
            'GetAddress', None, None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        if not address:
            raise RuntimeError('Accessibility address is empty')
        children.append(subprocess.Popen(['/usr/libexec/at-spi2-registryd', '--use-gnome-session']))
        accessibility = Gio.DBusConnection.new_for_address_sync(address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        wait_for_name(accessibility, 'org.a11y.atspi.Registry')
        # Wait for real portal activation, not a substitute interface that only
        # satisfies the tests. Xvfb and the private runtime precede this bus.
        for interface in ('org.freedesktop.portal.Settings', 'org.freedesktop.portal.Inhibit'):
            version = bus.call_sync('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
                'org.freedesktop.DBus.Properties', 'Get', GLib.Variant('(ss)', (interface, 'version')),
                None, Gio.DBusCallFlags.NONE, 15000, None).unpack()[0]
            if version < 1:
                raise RuntimeError('Native portal interface unavailable: ' + interface)
        return subprocess.call(command)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def main():
    args = sys.argv[1:]
    if args and args[0] == '--session':
        return session(args[1:])
    if args and args[0] == '--':
        args.pop(0)
    if not args:
        raise SystemExit('A native check command is required')
    with tempfile.TemporaryDirectory(prefix='luma-native-check-') as directory:
        root = Path(directory)
        env = os.environ.copy()
        for key in ('DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY'):
            env.pop(key, None)
        for key, name in (('XDG_RUNTIME_DIR', 'runtime'), ('XDG_CONFIG_HOME', 'config'),
                          ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state')):
            path = root / name
            path.mkdir(mode=0o700)
            env[key] = str(path)
        portal = root / 'config/xdg-desktop-portal'
        portal.mkdir()
        (portal / 'portals.conf').write_text('[preferred]\ndefault=gtk\n')
        env.update(XDG_CURRENT_DESKTOP='GNOME', XDG_SESSION_TYPE='x11', GSK_RENDERER='cairo')
        command = ['xvfb-run', '-a', '--server-args=-screen 0 1920x1200x24', 'dbus-run-session', '--',
                   sys.executable, str(Path(__file__).resolve()), '--session', *args]
        process = subprocess.Popen(command, env=env, start_new_session=True)
        try:
            return process.wait(timeout=120)
        finally:
            stop_process_group(process, root)


if __name__ == '__main__':
    raise SystemExit(main())
