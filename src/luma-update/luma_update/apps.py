# SPDX-License-Identifier: Apache-2.0
"""One update brings the apps too.

Once a release is staged, the app and runtime updates waiting on the system
installation's ``luma`` remote are downloaded for it, not deployed, by
luma-system-app-updates-prefetch.service (luma-application-installer); its
first boot deploys them before the display manager starts. This agent only
starts that unit: it never touches Flatpak itself, and a system without the
unit updates its apps from Depot as before.
"""
from __future__ import annotations

import logging

log = logging.getLogger("luma-update")

PREFETCH_UNIT = "luma-system-app-updates-prefetch.service"


def start_prefetch(connection) -> bool:
    """Ask systemd to start the download; returns at once. False when it can't (no unit, no systemd)."""
    from gi.repository import Gio, GLib
    try:
        connection.call_sync("org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                             "org.freedesktop.systemd1.Manager", "StartUnit",
                             GLib.Variant("(ss)", (PREFETCH_UNIT, "replace")), GLib.VariantType.new("(o)"),
                             Gio.DBusCallFlags.NONE, 10000, None)
    except GLib.Error as error:
        log.info("app updates are not downloaded with this update: %s", error.message)
        return False
    log.info("downloading the app updates that come with this update")
    return True
