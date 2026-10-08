# SPDX-License-Identifier: Apache-2.0
"""Mutter's ScreenCast API, as the portal backend uses it.

This is the same conversation xdg-desktop-portal-gnome has with Mutter, and
deliberately nothing more: a session, a stream for each chosen source, and the
PipeWire node the application then connects to. The picker is not here; the
Shell draws it (ADR-041).
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

LOGGER = logging.getLogger("luma-portal")

SCREEN_CAST_NAME = "org.gnome.Mutter.ScreenCast"
SCREEN_CAST_PATH = "/org/gnome/Mutter/ScreenCast"
SCREEN_CAST_IFACE = "org.gnome.Mutter.ScreenCast"
SESSION_IFACE = "org.gnome.Mutter.ScreenCast.Session"
STREAM_IFACE = "org.gnome.Mutter.ScreenCast.Stream"

#: Mutter's cursor modes, which are not the portal's.
MUTTER_CURSOR_HIDDEN = 0
MUTTER_CURSOR_EMBEDDED = 1
MUTTER_CURSOR_METADATA = 2

#: The portal's cursor modes (org.freedesktop.portal.ScreenCast).
PORTAL_CURSOR_HIDDEN = 1
PORTAL_CURSOR_EMBEDDED = 2
PORTAL_CURSOR_METADATA = 4

#: The portal's source types.
SOURCE_MONITOR = 1
SOURCE_WINDOW = 2
SOURCE_VIRTUAL = 4


def cursor_mode_for_portal(portal_mode: int) -> int:
    """Mutter's cursor mode for one of the portal's."""
    if portal_mode == PORTAL_CURSOR_EMBEDDED:
        return MUTTER_CURSOR_EMBEDDED
    if portal_mode == PORTAL_CURSOR_METADATA:
        return MUTTER_CURSOR_METADATA
    return MUTTER_CURSOR_HIDDEN


class Stream:
    """One Mutter stream, and the PipeWire node it eventually announces."""

    def __init__(self, connection, path: str, source_type: int,
                 geometry: tuple[int, int, int, int] | None) -> None:
        self._connection = connection
        self.path = path
        self.source_type = source_type
        self.geometry = geometry
        self.node_id: int | None = None
        #: Called once Mutter announces the PipeWire node for this stream.
        self.on_node = None
        self._subscription = connection.signal_subscribe(
            SCREEN_CAST_NAME, STREAM_IFACE, "PipeWireStreamAdded", path, None,
            Gio.DBusSignalFlags.NONE, self._on_node, None)

    def _on_node(self, _connection, _sender, _path, _iface, _signal, parameters,
                 _data) -> None:
        (self.node_id,) = parameters.unpack()
        LOGGER.info("stream %s is PipeWire node %s", self.path, self.node_id)
        if self.on_node:
            self.on_node(self)

    def close(self) -> None:
        if self._subscription:
            self._connection.signal_unsubscribe(self._subscription)
            self._subscription = 0

    def portal_properties(self) -> dict:
        """What the frontend puts in the application's `streams` entry."""
        properties = {"source_type": GLib.Variant("u", self.source_type)}
        if self.geometry:
            x, y, width, height = self.geometry
            properties["position"] = GLib.Variant("(ii)", (x, y))
            properties["size"] = GLib.Variant("(ii)", (width, height))
        return properties


class ScreenCastSession:
    """A Mutter screen cast session with one stream for each chosen source."""

    def __init__(self, connection, *, disable_animations: bool = False,
                 remote_desktop_session_id: str | None = None) -> None:
        self._connection = connection
        self.streams: list[Stream] = []
        self._closed_subscription = 0
        self.on_closed = None

        properties = {
            "disable-animations": GLib.Variant("b", disable_animations),
        }
        if remote_desktop_session_id:
            properties["remote-desktop-session-id"] = \
                GLib.Variant("s", remote_desktop_session_id)
        reply = connection.call_sync(
            SCREEN_CAST_NAME, SCREEN_CAST_PATH, SCREEN_CAST_IFACE,
            "CreateSession", GLib.Variant("(a{sv})", (properties,)),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None)
        (self.path,) = reply.unpack()
        self._closed_subscription = connection.signal_subscribe(
            SCREEN_CAST_NAME, SESSION_IFACE, "Closed", self.path, None,
            Gio.DBusSignalFlags.NONE, self._on_closed, None)
        LOGGER.info("Mutter screen cast session %s", self.path)

    def _on_closed(self, *_args) -> None:
        LOGGER.info("Mutter closed %s", self.path)
        if self.on_closed:
            self.on_closed()

    def _record(self, method: str, parameters, source_type: int,
                geometry) -> Stream:
        reply = self._connection.call_sync(
            SCREEN_CAST_NAME, self.path, SESSION_IFACE, method, parameters,
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None)
        (path,) = reply.unpack()
        stream = Stream(self._connection, path, source_type, geometry)
        self.streams.append(stream)
        return stream

    def record_monitor(self, connector: str, cursor_mode: int,
                       geometry=None) -> Stream:
        properties = {
            "cursor-mode": GLib.Variant("u", cursor_mode),
            # A share, not a recording. Mutter's remote-access handle carries
            # this through, and the Shell's Stop pill only ever sees shares.
            "is-recording": GLib.Variant("b", False),
        }
        return self._record(
            "RecordMonitor", GLib.Variant("(sa{sv})", (connector, properties)),
            SOURCE_MONITOR, geometry)

    def record_window(self, window_id: int, cursor_mode: int,
                      geometry=None) -> Stream:
        properties = {
            "window-id": GLib.Variant("t", window_id),
            "cursor-mode": GLib.Variant("u", cursor_mode),
            "is-recording": GLib.Variant("b", False),
        }
        return self._record(
            "RecordWindow", GLib.Variant("(a{sv})", (properties,)),
            SOURCE_WINDOW, geometry)

    @property
    def ready(self) -> bool:
        """True once every stream has its PipeWire node."""
        return bool(self.streams) and all(
            stream.node_id is not None for stream in self.streams)

    def start(self) -> None:
        self._connection.call_sync(
            SCREEN_CAST_NAME, self.path, SESSION_IFACE, "Start", None, None,
            Gio.DBusCallFlags.NONE, -1, None)
        LOGGER.info("started %s with %d stream(s)", self.path, len(self.streams))

    def stop(self) -> None:
        for stream in self.streams:
            stream.close()
        self.streams = []
        if self._closed_subscription:
            self._connection.signal_unsubscribe(self._closed_subscription)
            self._closed_subscription = 0
        try:
            self._connection.call_sync(
                SCREEN_CAST_NAME, self.path, SESSION_IFACE, "Stop", None, None,
                Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error as error:
            # A session Mutter already closed is not an error worth raising:
            # the caller is tearing down either way.
            LOGGER.debug("stopping %s: %s", self.path, error.message)
