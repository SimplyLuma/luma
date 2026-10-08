# SPDX-License-Identifier: Apache-2.0
"""Luma's portal backend.

Two interfaces live here.

AppChooser is built as the portal rather than as a dialog Files owns, so a
sandboxed application asking OpenURI to choose gets the same chooser as a
double-click in Files. Files becomes one caller among several instead of the
only one that can show it.

ScreenCast (screencast.py) answers "share my screen" by asking the Shell to
show Luma's picker and then creating the stream through Mutter, which is where
the live previews come from; see ADR-041.
"""

from __future__ import annotations

import logging
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import apps  # noqa: E402
from .screencast import ScreenCastPortal  # noqa: E402

# Gtk, Adw, the app kit and the dialog are deliberately NOT imported here.
# Importing Adw brings in Gdk, and Gdk asks org.freedesktop.portal.Settings
# for the colour scheme as it initialises. That request is served by
# xdg-desktop-portal -- the very process waiting for this backend to claim its
# bus name -- so each waited on the other for the full 25 second activation
# timeout. The frontend then gave up and fell back to the GNOME chooser for the
# rest of the session, and every application that asked the portal for its
# colour scheme stalled for those 25 seconds too, which is what made the first
# few launches after a login take twenty seconds to appear.
#
# Nothing here may touch Gdk before the name is owned. ensure_toolkit() does
# the importing, at the point where there is actually a dialog to draw, and
# binds these names then. apps is safe: it uses Gio only.
Adw = None
Gtk = None
OpenWithDialog = None
RESPONSE_ALWAYS = RESPONSE_CANCEL = RESPONSE_ONCE = None

LOGGER = logging.getLogger("luma-portal")

BUS_NAME = "org.freedesktop.impl.portal.desktop.luma"
OBJECT_PATH = "/org/freedesktop/portal/desktop"

#: The portal's own vocabulary, which is not the dialog's: a portal answers
#: success, cancelled, or ended some other way.
PORTAL_SUCCESS = 0
PORTAL_CANCELLED = 1

INTERFACE = """
<node>
  <interface name='org.freedesktop.impl.portal.AppChooser'>
    <method name='ChooseApplication'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='s' name='parent_window' direction='in'/>
      <arg type='as' name='choices' direction='in'/>
      <arg type='a{sv}' name='options' direction='in'/>
      <arg type='u' name='response' direction='out'/>
      <arg type='a{sv}' name='results' direction='out'/>
    </method>
    <method name='UpdateChoices'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='as' name='choices' direction='in'/>
    </method>
    <property name='version' type='u' access='read'/>
  </interface>
</node>
"""

REQUEST_INTERFACE = """
<node>
  <interface name='org.freedesktop.impl.portal.Request'>
    <method name='Close'/>
  </interface>
</node>
"""


def human_size(size: int) -> str:
    """A size a person reads, in the same units the file manager uses."""
    if size < 1000:
        return f"{size} bytes"
    for unit in ("kB", "MB", "GB", "TB"):
        size /= 1000.0
        if size < 1000:
            return f"{size:.1f} {unit}".replace(".0 ", " ")
    return f"{size:.1f} PB"


def describe_target(options: dict):
    """Name, content type and size for the file being opened.

    The options carry only a basename and possibly a uri. Anything that needs
    the file itself is attempted and allowed to fail: a chooser must still open
    for a uri that cannot be stat-ed.
    """
    name = options.get("filename") or ""
    content_type = options.get("content_type") or ""
    uri = options.get("uri") or ""
    size = ""
    if uri:
        try:
            target = Gio.File.new_for_uri(uri)
            if not name:
                name = target.get_basename() or ""
            info = target.query_info(
                "standard::size,standard::content-type",
                Gio.FileQueryInfoFlags.NONE, None)
            if not content_type:
                content_type = info.get_content_type() or ""
            size = human_size(info.get_size())
        except GLib.Error:
            pass
    if not content_type and name:
        guessed, _certain = Gio.content_type_guess(name, None)
        content_type = guessed or ""
    return name or "This file", content_type, size


class AppChooserPortal:
    def __init__(self, connection) -> None:
        self._connection = connection
        self._node = Gio.DBusNodeInfo.new_for_xml(INTERFACE)
        self._request_node = Gio.DBusNodeInfo.new_for_xml(REQUEST_INTERFACE)
        self._open: dict[str, OpenWithDialog] = {}
        connection.register_object(
            OBJECT_PATH, self._node.interfaces[0], self._call, self._get, None)

    # -- Interface --------------------------------------------------------

    def _get(self, _connection, _sender, _path, _interface, prop):
        if prop == "version":
            return GLib.Variant("u", 2)
        return None

    def _call(self, _connection, _sender, _path, _interface, method, parameters,
              invocation) -> None:
        if method == "ChooseApplication":
            self._choose(parameters, invocation)
            return
        if method == "UpdateChoices":
            handle, choices = parameters.unpack()
            LOGGER.info("choices updated for %s: %d", handle, len(choices))
            invocation.return_value(None)
            return
        invocation.return_error_literal(
            Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)

    def _choose(self, parameters, invocation) -> None:
        handle, _app_id, parent_window, choices, options = parameters.unpack()
        name = content_type = None
        size = 0

        def done(response: int, desktop_id: str) -> None:
            self._open.pop(handle, None)
            if response == RESPONSE_CANCEL or not desktop_id:
                invocation.return_value(
                    GLib.Variant("(ua{sv})", (PORTAL_CANCELLED, {})))
                return
            # An app id, not a desktop file name: the frontend appends the
            # suffix itself before looking the application up.
            results = {"choice": GLib.Variant("s", apps.app_id_for(desktop_id))}
            invocation.return_value(
                GLib.Variant("(ua{sv})", (PORTAL_SUCCESS, results)))

        # Everything that can fail sits inside one attempt, and the caller is
        # answered whatever happens. An unanswered method call leaves the
        # application that asked for a chooser waiting on this process for
        # ever, so a fault in here is indistinguishable from the feature not
        # existing: no dialog, no error, nothing.
        try:
            ensure_toolkit()
            name, content_type, size = describe_target(options)
            dialog = OpenWithDialog(
                file_name=name, content_type=content_type, file_size=size,
                extra_ids=tuple(choices), on_done=done)
            self._open[handle] = dialog
            # A portal request can be closed by the caller, not only by the
            # person looking at it.
            self._connection.register_object(
                handle, self._request_node.interfaces[0],
                lambda *_a, **_k: self._closed(handle, _a[-1]), None, None)
            # Attach the chooser to whatever asked for it. On its own it is a
            # toplevel with no desktop file for the shell to associate it
            # with, so it appeared in the dock as "GTK Application": a portal
            # backend presenting itself as a running application, which it is
            # not. As a dialog of the caller it belongs to that window and the
            # dock leaves it alone.
            dialog.connect("map", lambda *_a: parent_to_caller(dialog, parent_window))
            dialog.present()
        except Exception:  # noqa: BLE001
            LOGGER.exception("could not present a chooser for %s", name)
            self._open.pop(handle, None)
            invocation.return_value(
                GLib.Variant("(ua{sv})", (PORTAL_CANCELLED, {})))

    def _closed(self, handle: str, invocation) -> None:
        dialog = self._open.pop(handle, None)
        if dialog is not None:
            dialog.close()
        invocation.return_value(None)


#: The toolkit is initialised on the first chooser request, not at startup.
_toolkit_ready = False


def ensure_toolkit() -> None:
    """Initialise GTK, but only once a dialog is actually needed.

    D-Bus activation gives a portal backend about 25 seconds to claim its bus
    name, and importing Adw costs exactly that when the Settings portal cannot
    answer -- see the note where these names are declared. Doing any of it
    before the name was taken meant the frontend gave up first: it logged

        Failed to create app chooser proxy: Error calling StartServiceByName
        for org.freedesktop.impl.portal.desktop.luma: Timeout was reached

    and silently fell back to the GNOME chooser, so Luma's dialog never
    appeared however correctly it was configured. Owning the name is cheap and
    has to come first; the toolkit can wait until there is something to draw.
    """

    global _toolkit_ready, Adw, Gtk, OpenWithDialog
    global RESPONSE_ALWAYS, RESPONSE_CANCEL, RESPONSE_ONCE
    if _toolkit_ready:
        return

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw as adw, Gtk as gtk
    from luma_appkit import add_style_sheet, install_appkit

    Adw, Gtk = adw, gtk
    from .chooser import (
        RESPONSE_ALWAYS as always, RESPONSE_CANCEL as cancel,
        RESPONSE_ONCE as once, OpenWithDialog as dialog,
    )
    RESPONSE_ALWAYS, RESPONSE_CANCEL, RESPONSE_ONCE = always, cancel, once
    OpenWithDialog = dialog

    Adw.init()
    install_appkit()
    _install_style()
    _toolkit_ready = True


def parent_to_caller(window, parent_window: str) -> None:
    """Make the chooser a dialog of the window that requested it."""

    if not parent_window or not parent_window.startswith("wayland:"):
        return
    surface = window.get_surface()
    if surface is None:
        return
    try:
        gi.require_version("GdkWayland", "4.0")
        from gi.repository import GdkWayland
        if isinstance(surface, GdkWayland.WaylandToplevel):
            surface.set_transient_for_exported(
                parent_window[len("wayland:"):])
    except Exception:  # noqa: BLE001 - an unparented dialog still works
        LOGGER.debug("could not parent the chooser to %s", parent_window)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="luma-portal: %(levelname)s %(message)s")

    loop = GLib.MainLoop()
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    AppChooserPortal(connection)
    # The screen sharing backend never touches GTK: the picker is drawn by the
    # Shell, so nothing here can repeat the Adw-before-the-name deadlock that
    # ensure_toolkit() exists to avoid.
    ScreenCastPortal(connection, OBJECT_PATH)
    Gio.bus_own_name_on_connection(
        connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        lambda *_a: LOGGER.info("owning %s", BUS_NAME),
        lambda *_a: (LOGGER.error("another app chooser owns the name"), loop.quit()))
    loop.run()
    return 0


def _install_style() -> None:
    import os
    import pathlib

    display = None
    try:
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
    except Exception:  # noqa: BLE001
        return
    if display is None:
        return
    for candidate in (
        os.environ.get("LUMA_OPEN_WITH_STYLE_PATH", ""),
        str(pathlib.Path(__file__).resolve().parent.parent / "data" / "open-with.css"),
        "/usr/share/luma-portal/open-with.css",
    ):
        if candidate and pathlib.Path(candidate).is_file():
            # The kit owns this sheet, so it follows the surface treatment.
            add_style_sheet(candidate)
            return
