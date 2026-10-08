# SPDX-License-Identifier: Apache-2.0
"""Ari host authority comes from the bus process, never caller-supplied IDs."""
import os
from pathlib import Path
from gi.repository import Gio, GLib

APP = "org.projectluma.Ari"

class Refused(ValueError):
    pass

def start(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]

def credential_for_bus(connection):
    pid = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "GetConnectionUnixProcessID",
        GLib.Variant("(s)", ("org.freedesktop.DBus",)), GLib.VariantType.new("(u)"),
        Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    if pid <= 0 or Path(f"/proc/{pid}").stat().st_uid != os.getuid():
        raise Refused("The host session identity is unavailable.")
    return pid

def authenticate(connection, sender):
    if not sender or not sender.startswith(":"):
        raise Refused("A current application connection is required.")
    def credential(method):
        return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", method, GLib.Variant("(s)", (sender,)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    uid, pid = credential("GetConnectionUnixUser"), credential("GetConnectionUnixProcessID")
    if uid == 0 or uid != os.getuid() or pid <= 0:
        raise Refused("Ari belongs to this signed-in user.")
    before = start(pid)
    try:
        # Only genuine absence selects native authority. Permission or malformed
        # identity errors never fall through to the more permissive native path.
        Path(f"/proc/{pid}/root/.flatpak-info").open("rb").close()
    except FileNotFoundError:
        root = Path(f"/proc/{pid}/root").stat()
        host = Path("/").stat()
        if (root.st_dev, root.st_ino) != (host.st_dev, host.st_ino):
            raise Refused("This process does not use the host filesystem.")
        # Ari has PrivateTmp; compare the actual session bus broker namespace,
        # not this daemon's private namespace or an inaccessible root PID1.
        bus_pid = credential_for_bus(connection)
        if os.readlink(f"/proc/{pid}/ns/mnt") != os.readlink(f"/proc/{bus_pid}/ns/mnt"):
            raise Refused("A sandbox must use the signed application interface.")
        identity = "native"
    else:
        from luma_installer.app_data_broker import authenticate as signed
        if signed(connection, sender) != APP:
            raise Refused("This application is not Ari.")
        identity = APP
    if start(pid) != before:
        raise Refused("The application process changed.")
    return identity
