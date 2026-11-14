# SPDX-License-Identifier: Apache-2.0
"""Prepared launch for Python GTK applications.

libluma-app-prepare.so gives C applications Luma's prepared-launch protocol;
its symbol hooks never see a PyGObject application, so Luma's own apps always
started cold. This is the same protocol for Python:

Started with ``LUMA_APP_PREPARE=1`` (and ``LUMA_APP_PREPARE_NOTIFY`` naming a
Unix datagram socket), the application runs as its D-Bus service, holds
itself, and runs its own ``activate`` once: its window is built, styled and
realized, but not mapped. Then it reports ready on the socket. The next
activation is a launch, and a launch only maps the window that already
exists, on a surface made for the output it opens on: a present, not a start. A launch that arrives while the app is still
preparing is kept, and the window shows as soon as it is ready. Nothing else
about the app changes; an app that is never prepared never loads this module.

The kit installs this when it is imported with LUMA_APP_PREPARE=1
(luma_appkit/__init__.py); luma-app-prepared@.service starts TV apps so.
"""
from __future__ import annotations

import os
import socket
import sys

_preparing = True
_deferred: list = []
_installed = False


def _notify(path: str) -> None:
    if not path:
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(str(os.getpid()).encode(), path)
    except OSError:
        pass


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk

    original_present = Gtk.Window.present

    def show(window, *args):
        # The window was realized while preparing, often at session start before
        # the outputs, their scale and the shell's posture were final. Shown, it
        # gets a new surface laid out against the output it opens on, as a cold
        # start would; the built and styled widgets are kept.
        if window in _deferred:
            _deferred.remove(window)
            if window.get_realized() and not window.get_mapped():
                window.unrealize()
                window.queue_resize()
        return original_present(window, *args)

    def present(window, *args):
        global _preparing
        if _preparing:
            # Built and realized (styles, fonts, GL), never mapped while preparing.
            window.realize()
            if window not in _deferred:
                _deferred.append(window)
            return None
        return show(window, *args)

    Gtk.Window.present = present

    original_run = Gio.Application.run

    def run(application, argv=None):
        # Only the process that runs the application prepares (a launcher that
        # execs it keeps the request); anything the app starts launches normally.
        os.environ.pop("LUMA_APP_PREPARE", None)
        notify = os.environ.pop("LUMA_APP_PREPARE_NOTIFY", "")
        # Prepared, the app is a service whatever its own argv says (some apps
        # run([]) and drop --gapplication-service): it is only ever activated
        # by a launch, never by its own start, so a window shows only when
        # somebody opens the app.
        application.set_flags(application.get_flags() | Gio.ApplicationFlags.IS_SERVICE)
        # A service has no --gapplication-service option to parse: the flag says it.
        if argv:
            argv = [arg for arg in argv if arg != "--gapplication-service"]

        own = {"activating": False, "wanted": False}

        def activated(_application) -> None:
            # A launch (D-Bus Activate) that arrives while preparing: remember it.
            if _preparing and not own["activating"]:
                own["wanted"] = True

        def prepared() -> bool:
            global _preparing
            # Only the app that owns its name is reached by a launch; anything
            # else would sit in memory unused, so it leaves (and is prepared again).
            if application.get_is_remote() or not application.get_is_registered():
                print("luma prepare: not the primary instance; leaving", file=sys.stderr)
                application.quit()
                return GLib.SOURCE_REMOVE
            own["activating"] = True
            try:
                application.activate()
            finally:
                own["activating"] = False
            _preparing = False
            _notify(notify)
            if own["wanted"]:
                # Someone opened the app while it was preparing: show it now.
                for window in list(_deferred):
                    show(window)
            return GLib.SOURCE_REMOVE

        def startup(_application) -> None:
            # Resident without a window: a launch is an activation of this process.
            application.hold()
            GLib.idle_add(prepared)

        application.connect_after("startup", startup)
        application.connect("activate", activated)
        return original_run(application, argv)

    Gio.Application.run = run
