"""Opening an AppImage runs it.

A double-click in Filer, Open in a browser's downloads or any other OpenURI
caller hands the file to luma-install. For an AppImage nothing is reviewed and
no Valet window opens: the application is unpacked into Valet's private copy
the first time, started in the AppImage sandbox, and its window is the first
thing the person sees. The executable bit does not matter.

It runs from its file without being added to Applications. Once it is up,
one notification offers Keep in Apps; that is the only install step, and it
comes after the application has opened. The same file opened again starts
the copy already there, without asking again.

Nothing here imports GTK: the launch's activation token has to reach the
application's first window, not a window of Valet's.
"""
from __future__ import annotations

import os
from pathlib import Path

from .errors import InstallerError

#: An application that is still running this long after it started opened.
SETTLE_SECONDS = 3
#: How long the offer is listened for after the application has quit.
OFFER_SECONDS_AFTER_EXIT = 120
APPIMAGE_MAGIC = (b"AI\x01", b"AI\x02")


def is_appimage(path: Path) -> bool:
    """An AppImage by name or by the marker its runtime carries (types 1 and 2)."""
    try:
        if not path.is_file():
            return False
        if path.name.lower().endswith(".appimage"):
            return True
        with path.open("rb") as stream:
            header = stream.read(12)
    except OSError:
        return False
    return header[:4] == b"\x7fELF" and header[8:11] in APPIMAGE_MAGIC


def prepare(path: Path) -> dict[str, object] | None:
    """The application record to start, or None when Valet should explain why not."""
    from . import backends, workflow
    from .inspectors import _appimage, architecture_is_compatible
    from .safety import fingerprint
    size, digest = fingerprint(path)
    record = workflow.added_appimage(digest)
    if record is not None:
        return record
    report = _appimage(path, size, digest)
    if not architecture_is_compatible(report):
        return None
    return backends.prepare_appimage(report)


def _offer_keep(record: dict[str, object], process) -> None:
    """One notification, after the application has opened: Keep in Apps."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    from .backends import keep_appimage
    from .desktop import write_record

    loop = GLib.MainLoop()
    state = {"id": 0, "running": True}
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return
    name = str(record.get("name") or "This application")
    source = Path(str(record.get("source") or "")).name

    def finish(*_args):
        loop.quit()
        return GLib.SOURCE_REMOVE

    def exited(_pid, status, *_args):
        state["running"] = False
        if not state["id"]:
            # It quit or failed before it settled; the launcher reports failures.
            loop.quit()
        else:
            GLib.timeout_add_seconds(OFFER_SECONDS_AFTER_EXIT, finish)

    def signal(_connection, _sender, _path, _interface, member, parameters):
        values = parameters.unpack()
        if not state["id"] or values[0] != state["id"]:
            return
        if member == "ActionInvoked" and values[1] == "keep":
            try:
                keep_appimage(record)
            except (InstallerError, OSError):
                pass
        loop.quit()

    def offer():
        if not state["running"]:
            return GLib.SOURCE_REMOVE
        try:
            reply = bus.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)", (
                    "Valet", 0, str(record.get("icon") or "io.luma.Valet"), name,
                    f"Opened from {source}." if source else "Opened from its file.",
                    ["default", "", "keep", "Keep in Apps"],
                    {"desktop-entry": GLib.Variant("s", "io.luma.Valet")}, -1)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            loop.quit()
            return GLib.SOURCE_REMOVE
        state["id"] = reply.unpack()[0]
        try:
            write_record(str(record["application_id"]), {**record, "offered": True})
        except OSError:
            pass
        return GLib.SOURCE_REMOVE

    for member in ("ActionInvoked", "NotificationClosed"):
        bus.signal_subscribe("org.freedesktop.Notifications", "org.freedesktop.Notifications", member,
                             "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE, signal)
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, process.pid, exited)
    GLib.timeout_add_seconds(SETTLE_SECONDS, offer)
    loop.run()


def run(path: Path) -> int | None:
    """Start the AppImage; None hands the file to Valet's window instead."""
    from . import workflow
    path = path.expanduser().absolute()
    try:
        record = prepare(path)
    except InstallerError:
        # Valet's card says what is wrong with the file.
        return None
    if record is None:
        return None
    process = workflow.open_application(record)
    if process is None:
        return 0
    if record.get("kept") is False and not record.get("offered") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        _offer_keep(record, process)
    return 0


def main_for_open(values: list[str]) -> int | None:
    """luma-install FILE: an AppImage runs; anything else is Valet's to review."""
    if len(values) != 1 or values[0].startswith("-") or os.environ.get("LUMA_VALET_FIXTURE"):
        return None
    path = Path(values[0]).expanduser()
    if not is_appimage(path):
        return None
    return run(path)
