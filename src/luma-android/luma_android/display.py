# SPDX-License-Identifier: Apache-2.0
"""Bounded compositor readiness for the supervised graphical session."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import stat

from .errors import RuntimeUnavailableError


def wayland_socket() -> Path:
    display = Path(os.environ.get("WAYLAND_DISPLAY") or "wayland-0")
    if display.is_absolute():
        return display
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    return runtime / display


def _listening(path: Path) -> bool:
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.1)
            connection.connect(str(path))
        return True
    except OSError:
        return False


def wait_for_wayland(timeout: float = 180) -> None:
    """Wait only during startup; retain no timer or monitor after readiness.

    Monitor before probing to cover compositor creation during registration.
    The short startup-only retry also covers bind-before-listen and a full
    listen queue, neither of which generates a filesystem readiness event.
    """
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    if not 0 < timeout <= 180:
        raise ValueError("Compositor readiness deadline must be within 180 seconds.")
    path = wayland_socket()
    loop = GLib.MainLoop()
    ready = False
    monitor = None
    retry_id = deadline_id = 0

    def probe(*_args):
        nonlocal ready
        if _listening(path):
            ready = True
            loop.quit()
        return GLib.SOURCE_CONTINUE

    def expired():
        nonlocal deadline_id
        deadline_id = 0
        loop.quit()
        return GLib.SOURCE_REMOVE

    try:
        try:
            monitor = Gio.File.new_for_path(str(path.parent)).monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", probe)
        except GLib.Error:
            # The bounded retry covers an unavailable directory monitor too.
            pass
        probe()
        if not ready:
            retry_id = GLib.timeout_add(250, probe)
            deadline_id = GLib.timeout_add(max(1, int(timeout * 1000)), expired)
            loop.run()
    finally:
        if retry_id:
            GLib.source_remove(retry_id)
        if deadline_id:
            GLib.source_remove(deadline_id)
        if monitor is not None:
            monitor.cancel()
    if not ready:
        raise RuntimeUnavailableError("The graphical display did not become ready for Android.")
