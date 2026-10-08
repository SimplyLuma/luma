# SPDX-License-Identifier: Apache-2.0
"""Entry point for the `luma-tide-retire-preview-overrides` systemd user
service (see `data/luma-tide-retire-preview-overrides.service`, ordered
`Before=dbus.service graphical-session-pre.target` so it runs before the
session bus or GNOME Shell can cache a stale override).

Split out of `retire_overrides.py` so that module stays importable without
`gi`/GLib (see its own docstring) -- the reload nudges here are a thin,
best-effort layer on top of the pure retirement logic, not load-bearing for
correctness: `retire_overrides.py` alone is what guarantees a stale
override is out of the way *before* anything reads it, when the unit
ordering holds. The nudges below only matter for the fallback case where
this runs late (e.g. right after a mid-session package upgrade, with
dbus-broker/Shell already running and possibly already caching something).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .retire_overrides import retire_stale_overrides

#: A oneshot unit must never hang, no matter what GDBus does internally --
#: confirmed against a real systemd --user session where dbus.socket exists
#: but dbus-broker.service hasn't started yet: Gio.bus_get_sync() can block
#: well past its own supposed default timeout while socket activation
#: settles. This bounds the whole best-effort reload attempt from outside,
#: in a daemon thread that can't block process exit even if GDBus itself
#: never returns.
_RELOAD_TIMEOUT_SECONDS = 5.0


def _bump_mtime(retired_path: Path) -> None:
    """Touch the parent directory so GIO's inotify-backed file monitors --
    which both dbus-broker and GNOME Shell's app-info system already rely
    on for their normal auto-reload -- see a change even if the original
    move event was somehow coalesced or missed."""
    try:
        os.utime(retired_path.parent, None)
    except OSError:
        logging.getLogger("luma-tide-retire-preview-overrides").exception(
            "Could not refresh the modification time of %s", retired_path.parent
        )


def _reload_session_bus(log: logging.Logger) -> None:
    """Ask the session bus to reload its service-activation files via the
    standard, widely-supported `org.freedesktop.DBus.ReloadConfig` method.
    Best-effort: this only matters for the late-run fallback case, so any
    failure is logged, never fatal."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        log.warning("No GLib/Gio available; could not ask the session bus to reload")
        return
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ReloadConfig",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except Exception:
        log.exception("Could not ask the session bus to reload its service files")


def _reload_session_bus_with_timeout(
    log: logging.Logger, timeout: float = _RELOAD_TIMEOUT_SECONDS
) -> None:
    """Run `_reload_session_bus` in a daemon thread and give up waiting for
    it after `timeout` seconds. A daemon thread doesn't block interpreter
    exit even if the underlying GDBus call never returns, so this bounds
    the *unit's* runtime even in that case -- the reload attempt itself
    might technically keep running in the background for a moment longer,
    but nothing is waiting on it."""
    worker = threading.Thread(target=_reload_session_bus, args=(log,), daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        log.warning(
            "Asking the session bus to reload its service files did not "
            "finish within %ss; giving up rather than blocking this unit",
            timeout,
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("luma-tide-retire-preview-overrides")
    retired = retire_stale_overrides(log=log, on_retired=_bump_mtime)
    if retired:
        _reload_session_bus_with_timeout(log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
