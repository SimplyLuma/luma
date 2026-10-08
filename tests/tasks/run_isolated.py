# SPDX-License-Identifier: Apache-2.0
"""Host runner: private D-Bus and throwaway EDS data, never the user's sources."""
from pathlib import Path
import os
import subprocess
import tempfile
import sys
import time
from gi.repository import Gio, GLib

with tempfile.TemporaryDirectory(prefix="luma-tasks-test-") as temporary:
    root = Path(temporary)
    env = dict(os.environ, LUMA_TASKS_ISOLATED_TEST="1", XDG_DATA_HOME=str(root / "data"),
               XDG_STATE_HOME=str(root / "state"),
               XDG_CONFIG_HOME=str(root / "config"), XDG_CACHE_HOME=str(root / "cache"),
               DBUS_SESSION_BUS_ADDRESS="unix:path=" + str(root / "bus"))
    (root / "bus.conf").write_text("<busconfig><type>session</type><listen>" + env["DBUS_SESSION_BUS_ADDRESS"] + "</listen><auth>EXTERNAL</auth><policy context=\"default\"><allow send_destination=\"*\"/><allow receive_sender=\"*\"/><allow own=\"*\"/></policy></busconfig>")
    processes = []
    bus_pid = None
    xvfb_pattern = None
    try:
        bus_pid = subprocess.check_output(["toolbox", "run", "-c", "luma-dev-f44", "dbus-daemon", "--fork", "--print-pid=1", "--config-file=" + str(root / "bus.conf")], text=True).strip()
        assert bus_pid.isdigit(), "No private bus PID"
        bus_connection = Gio.DBusConnection.new_for_address_sync(env["DBUS_SESSION_BUS_ADDRESS"], Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        services = () if any(option in sys.argv for option in ("--identity", "--unit")) else (("evolution-source-registry", "org.gnome.evolution.dataserver.Sources5"), ("evolution-calendar-factory", "org.gnome.evolution.dataserver.Calendar8"))
        for service, name in services:
            processes.append(subprocess.Popen(["/usr/libexec/" + service], env=dict(env, GIO_USE_VFS="local"), stdout=subprocess.DEVNULL))
            for _ in range(100):
                owned = bus_connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                if owned: break
                time.sleep(.05)
            else: raise RuntimeError(service + " did not start")
        if "--unit" in sys.argv:
            subprocess.run(["/usr/bin/python3", str(Path(__file__).with_name("unit_runtime.py"))], env=env, check=True)
        elif any(option in sys.argv for option in ("--ui", "--fixture", "--identity")):
            authority = root / "Xauthority"
            authority.touch(mode=0o600)
            xvfb_pattern = "^Xvfb -displayfd 1 -screen 0 1280x900x24 -ac -nolisten tcp -auth " + str(authority) + "$"
            display = subprocess.Popen(["toolbox", "run", "-c", "luma-dev-f44", "Xvfb", "-displayfd", "1", "-screen", "0", "1280x900x24", "-ac", "-nolisten", "tcp", "-auth", str(authority)], stdout=subprocess.PIPE, text=True)
            processes.append(display)
            number = display.stdout.readline().strip()
            assert number.isdigit(), "No isolated display"
            env.update(DISPLAY=":" + number, GDK_BACKEND="x11", GSK_RENDERER="cairo")
            script = "ui_identity_runtime.py" if "--identity" in sys.argv else "ui_runtime.py" if "--fixture" in sys.argv else "ui_eds_runtime.py"
            subprocess.run(["/usr/bin/python3", str(Path(__file__).with_name(script))], env=env, check=True)
        else:
            subprocess.run(["/usr/bin/python3", str(Path(__file__).with_name("eds_runtime.py"))], env=env, check=True)
    finally:
        if xvfb_pattern:
            stopped = subprocess.run(["toolbox", "run", "-c", "luma-dev-f44", "pkill", "-f", xvfb_pattern])
            assert stopped.returncode in (0, 1), "Could not stop isolated X server"
        if bus_pid: subprocess.run(["toolbox", "run", "-c", "luma-dev-f44", "kill", "-TERM", bus_pid], check=True)
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
