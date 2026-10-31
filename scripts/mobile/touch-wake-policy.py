#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Arm controller wake gestures on display blank, without polling touch/motion."""

import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys

from gi.repository import Gio, GLib, GLibUnix


def watch_session():
    """Read display state with the active user's session-bus credentials."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    display = Gio.DBusProxy.new_sync(
        bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
        "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
        "org.gnome.Mutter.DisplayConfig", None)
    last = None

    def changed(*_args):
        nonlocal last
        mode = display.get_cached_property("PowerSaveMode")
        blank = bool(display.get_name_owner() and mode is not None and
                     mode.unpack() in (1, 2, 3))
        if blank != last:
            print(int(blank), flush=True)
            last = blank

    display.connect("g-properties-changed", changed)
    display.connect("notify::g-name-owner", changed)
    changed()
    GLib.MainLoop().run()


def wake_nodes():
    nodes = []
    for bus in ("spi", "i2c"):
        for node in Path(f"/sys/bus/{bus}/devices").glob("*/wake_gesture"):
            node = node.resolve()
            if node.is_relative_to("/sys/devices"):
                nodes.append(node)
    return nodes


def apply_mode(nodes, blank):
    for node in nodes:
        try:
            permitted = (node.parent / "power/wakeup").read_text().strip() == "enabled"
            wanted = "1" if blank and permitted else "0"
            if node.read_text().strip() != wanted:
                node.write_text(wanted)
        except OSError as error:
            print(f"Touch wake unavailable: {error}", flush=True)


class TouchWakePolicy:
    def __init__(self):
        self.nodes = wake_nodes()
        self.child = None
        self.pipe_watch = 0
        self.retry = 0
        self.attempts = 0
        self.loop = GLib.MainLoop()
        self.system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.seat = Gio.DBusProxy.new_sync(
            self.system, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            "org.freedesktop.login1", "/org/freedesktop/login1/seat/seat0",
            "org.freedesktop.login1.Seat", None)
        self.seat.connect("g-properties-changed", self.seat_changed)

    def set_mode(self, blank):
        apply_mode(self.nodes, blank)

    def stop_watcher(self):
        # Restore touch even if a watcher has already exited during logout.
        self.set_mode(False)
        if self.pipe_watch:
            GLib.source_remove(self.pipe_watch)
            self.pipe_watch = 0
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait()
            self.child.stdout.close()
            self.child = None

    def state_received(self, _fd, condition):
        if condition & GLib.IO_IN:
            line = self.child.stdout.readline()
            if line in (b"0\n", b"1\n"):
                self.attempts = 0
                self.set_mode(line == b"1\n")
                return GLib.SOURCE_CONTINUE
        self.pipe_watch = 0
        self.stop_watcher()
        # The bus can appear just after the seat changes. Retry briefly at
        # login, never continuously while the device is idle.
        self.attempts += 1
        if self.attempts < 5:
            self.retry = GLib.timeout_add_seconds(1, self.retry_active)
        return GLib.SOURCE_REMOVE

    def seat_changed(self, _proxy, changed, _invalidated):
        if "ActiveSession" in changed.unpack():
            self.attempts = 0
            if self.retry:
                GLib.source_remove(self.retry)
                self.retry = 0
            self.bind_active()

    def bind_active(self):
        self.stop_watcher()
        active = self.seat.get_cached_property("ActiveSession")
        if active is None or not active.unpack()[0]:
            return
        session = Gio.DBusProxy.new_sync(
            self.system, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            "org.freedesktop.login1", active.unpack()[1],
            "org.freedesktop.login1.Session", None)
        user = session.get_cached_property("User")
        if user is None:
            return
        uid = user.unpack()[0]
        account = pwd.getpwuid(uid)
        env = os.environ.copy()
        env.update(DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{uid}/bus",
                   XDG_RUNTIME_DIR=f"/run/user/{uid}", HOME=account.pw_dir,
                   USER=account.pw_name, LOGNAME=account.pw_name)
        # Python drops credentials in the child before exec. Only the parent
        # writes the capability-discovered boolean sysfs nodes. The child can
        # request 0/1, never an arbitrary path or privileged command.
        self.child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch-session"],
            user=uid, group=account.pw_gid, extra_groups=[], env=env,
            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self.pipe_watch = GLib.io_add_watch(
            self.child.stdout.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
            self.state_received)

    def retry_active(self):
        self.retry = 0
        self.bind_active()
        return GLib.SOURCE_REMOVE

    def run(self):
        if not self.nodes:
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, self.loop.quit)
        try:
            self.bind_active()
            self.loop.run()
        finally:
            self.stop_watcher()


if __name__ == "__main__":
    if sys.argv[1:] == ["--watch-session"]:
        watch_session()
    elif sys.argv[1:] == ["--reset"]:
        apply_mode(wake_nodes(), False)
    elif len(sys.argv) == 1:
        TouchWakePolicy().run()
    else:
        raise SystemExit("Unexpected arguments")
