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


def serial_of(dump: str, node_id: int) -> int | None:
    """The object.serial of node ``node_id`` in pw-dump's JSON, or None."""
    import json

    try:
        objects = json.loads(dump)
    except ValueError:
        return None
    for item in objects if isinstance(objects, list) else []:
        if isinstance(item, dict) and item.get("id") == node_id:
            serial = ((item.get("info") or {}).get("props") or {}).get("object.serial")
            return int(serial) if isinstance(serial, (int, str)) and str(serial).isdigit() else None
    return None


class Stream:
    """One Mutter stream, and the PipeWire node it eventually announces."""

    def __init__(self, connection, path: str, source_type: int,
                 geometry: tuple[int, int, int, int] | None) -> None:
        self._connection = connection
        self.path = path
        self.source_type = source_type
        self.geometry = geometry
        self.node_id: int | None = None
        #: The node's object.serial (Mutter 47+, PipeWireStreamAddedV2): what
        #: an application's pipewiresrc targets. A node id is not a target.
        self.node_serial: int | None = None
        #: Called once Mutter announces the PipeWire node for this stream.
        self.on_node = None
        #: The node was announced (and its serial read, when Mutter sends one).
        self.announced = False
        self._announce = 0
        self._subscriptions = [
            connection.signal_subscribe(
                SCREEN_CAST_NAME, STREAM_IFACE, signal, path, None,
                Gio.DBusSignalFlags.NONE, handler, None)
            for signal, handler in (("PipeWireStreamAdded", self._on_node),
                                    ("PipeWireStreamAddedV2", self._on_node_v2))]

    def _on_node(self, _connection, _sender, _path, _iface, _signal, parameters,
                 _data) -> None:
        (self.node_id,) = parameters.unpack()
        # Mutter sends V2 (with the serial) right after this one: answer once it
        # has been read, or without a serial from a Mutter that has no V2.
        if not self._announce:
            self._announce = GLib.idle_add(self._announced)

    def _on_node_v2(self, _connection, _sender, _path, _iface, _signal, parameters,
                    _data) -> None:
        (properties,) = parameters.unpack()
        self.node_id = int(properties.get("node-id", self.node_id or 0)) or self.node_id
        if "node-serial" in properties:
            self.node_serial = int(properties["node-serial"])
        if not self._announce:
            self._announce = GLib.idle_add(self._announced)

    def _announced(self) -> bool:
        self._announce = 0
        if self.node_serial is None and self.node_id is not None:
            # A Mutter before ScreenCast API 5 gives only the id: read the
            # serial from PipeWire, as an application on the portal's remote
            # can target only a serial (or a name), never an id.
            self._read_serial()
            return GLib.SOURCE_REMOVE
        self._done()
        return GLib.SOURCE_REMOVE

    def _read_serial(self) -> None:
        try:
            dump = Gio.Subprocess.new(["pw-dump", "-N", str(self.node_id)],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as error:
            LOGGER.warning("could not read node %s's serial: %s", self.node_id, error.message)
            self._done()
            return

        def read(process, result) -> None:
            try:
                _ok, out, _err = process.communicate_utf8_finish(result)
                self.node_serial = serial_of(out or "", self.node_id)
            except GLib.Error as error:
                LOGGER.warning("could not read node %s's serial: %s", self.node_id, error.message)
            self._done()

        dump.communicate_utf8_async(None, None, read)

    def _done(self) -> None:
        if self.announced or not self._subscriptions:
            return  # closed meanwhile, or already told
        self.announced = True
        LOGGER.info("stream %s is PipeWire node %s (serial %s)", self.path, self.node_id,
                    self.node_serial)
        if self.on_node:
            self.on_node(self)

    def close(self) -> None:
        for subscription in self._subscriptions:
            self._connection.signal_unsubscribe(subscription)
        self._subscriptions = []
        if self._announce:
            GLib.source_remove(self._announce)
            self._announce = 0

    def portal_properties(self) -> dict:
        """What the frontend puts in the application's `streams` entry."""
        properties = {"source_type": GLib.Variant("u", self.source_type)}
        if self.node_serial is not None:
            properties["pipewire-serial"] = GLib.Variant("t", self.node_serial)
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
            stream.announced for stream in self.streams)

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
