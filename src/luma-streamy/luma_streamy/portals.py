# SPDX-License-Identifier: Apache-2.0
"""The ScreenCast and Camera portals, asynchronously, with restore tokens.

A display or window widget owns one ScreenCast session. The session stays
open while the widget exists, even when its scene isn't shown, so showing
it again needs no picker: only its PipeWire node is stopped. Restore tokens
are single-use: every Start returns a new one, which the widget keeps.
"""
from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from typing import Callable

from gi.repository import Gio, GLib

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
CAMERA = "org.freedesktop.portal.Camera"
REQUEST = "org.freedesktop.portal.Request"

MONITOR, WINDOW = 1, 2
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2


@dataclass
class Capture:
    """An open capture: the PipeWire fd, the node to target, and what the portal said."""

    fd: int  # unused once open: each stream asks remote() for its own connection
    node: str | None
    size: tuple[int, int] | None
    restore_token: str | None
    session: str | None = None

    @property
    def aspect(self) -> float | None:
        if self.size and self.size[1]:
            return self.size[0] / self.size[1]
        return None


class PortalError(RuntimeError):
    pass


def camera_nodes(fd: int) -> list[tuple[str, str, tuple[int, int] | None]]:
    """(target, name, size) for each camera on a camera-portal PipeWire remote.

    The target is the node's object.serial (or its node.name), which is what
    pipewiresrc's target-object wants on that remote; a global node id is not.
    """
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    factory = Gst.DeviceProviderFactory.find("pipewiredeviceprovider")
    if factory is None:
        return []
    provider = factory.get()
    provider.set_property("fd", fd)  # the provider owns and closes it
    if not provider.start():
        return []
    try:
        nodes = []
        for device in provider.get_devices():
            if not device.get_device_class().startswith("Video/Source"):
                continue
            props = device.get_properties()
            target = None
            for key in ("object.serial", "node.name"):
                if props is not None and props.has_field(key):
                    target = str(props.get_value(key))
                    break
            if target is None:
                continue
            size = None
            caps = device.get_caps()
            if caps is not None and caps.get_size():
                first = caps.get_structure(0)
                ok_w, width = first.get_int("width")
                ok_h, height = first.get_int("height")
                if ok_w and ok_h:
                    size = (width, height)
            nodes.append((target, device.get_display_name(), size))
        return nodes
    finally:
        provider.stop()


class Portals:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.captures: dict[str, Capture] = {}  # widget id -> open capture

    # ── Requests ──

    def _token(self) -> str:
        return f"streamy_{secrets.token_hex(6)}"

    def _request(self, interface: str, method: str, args: list, options: dict, done: Callable[[dict], None],
                 failed: Callable[[str], None], fd_list: Gio.UnixFDList | None = None) -> None:
        """Call a portal method that answers through a Request object's Response signal."""
        token = self._token()
        options = dict(options)
        options["handle_token"] = GLib.Variant("s", token)
        handle = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        subscription = 0

        def response(_bus, _sender, _path, _iface, _signal, params) -> None:
            self.bus.signal_unsubscribe(subscription)
            code, results = params.unpack()
            if code == 0:
                done(results)
            else:
                failed("cancelled" if code == 1 else "failed")

        subscription = self.bus.signal_subscribe(BUS, REQUEST, "Response", handle, None,
                                                 Gio.DBusSignalFlags.NO_MATCH_RULE, response)
        def called(bus, result) -> None:
            try:
                bus.call_finish(result)
            except GLib.Error as error:
                self.bus.signal_unsubscribe(subscription)
                failed(error.message)

        self.bus.call(BUS, PATH, interface, method, GLib.Variant.new_tuple(*args, GLib.Variant("a{sv}", options)),
                      None, Gio.DBusCallFlags.NONE, -1, None, called)

    def _open_remote(self, interface: str, args: list) -> int:
        result, fds = self.bus.call_with_unix_fd_list_sync(
            BUS, PATH, interface, "OpenPipeWireRemote",
            GLib.Variant.new_tuple(*args, GLib.Variant("a{sv}", {})), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, -1, None, None)
        index = result.unpack()[0]
        return fds.get(index)

    def _property(self, interface: str, name: str):
        try:
            value = self.bus.call_sync(BUS, PATH, "org.freedesktop.DBus.Properties", "Get",
                                       GLib.Variant("(ss)", (interface, name)), GLib.VariantType("(v)"),
                                       Gio.DBusCallFlags.NONE, 2000, None)
            return value.unpack()[0]
        except GLib.Error:
            return None

    # ── Screen and window capture ──

    def screencast(self, widget_id: str, *, window: bool, restore_token: str | None,
                   done: Callable[[Capture], None], failed: Callable[[str], None], parent: str = "") -> None:
        """Pick (or restore) a display or a window; ``done`` gets the open capture."""
        if widget_id in self.captures:
            done(self.captures[widget_id])
            return
        cursor_modes = self._property(SCREENCAST, "AvailableCursorModes") or 0
        version = self._property(SCREENCAST, "version") or 1

        def created(results: dict) -> None:
            session = results["session_handle"]
            options = {"types": GLib.Variant("u", WINDOW if window else MONITOR),
                       "multiple": GLib.Variant("b", False)}
            if cursor_modes & CURSOR_EMBEDDED:
                options["cursor_mode"] = GLib.Variant("u", CURSOR_EMBEDDED)
            if version >= 4:
                options["persist_mode"] = GLib.Variant("u", PERSIST_UNTIL_REVOKED)
                if restore_token:
                    options["restore_token"] = GLib.Variant("s", restore_token)
            self._request(SCREENCAST, "SelectSources", [GLib.Variant("o", session)], options,
                          lambda _r: start(session), failed)

        def start(session: str) -> None:
            self._request(SCREENCAST, "Start", [GLib.Variant("o", session), GLib.Variant("s", parent)], {},
                          lambda results: started(session, results), failed)

        def started(session: str, results: dict) -> None:
            streams = results.get("streams") or []
            if not streams:
                failed("nothing was shared")
                return
            node_id, props = streams[0]
            # pipewire-serial (v6) is stable where node ids are reused.
            serial = props.get("pipewire-serial")
            node = str(serial) if serial is not None else str(node_id)
            size = tuple(props["size"]) if "size" in props else None
            try:
                fd = self._open_remote(SCREENCAST, [GLib.Variant("o", session)])
            except GLib.Error as error:
                failed(error.message)
                return
            os.close(fd)  # each stream opens its own connection to the session's remote
            capture = Capture(-1, node, size, results.get("restore_token"), session)
            capture.remote = lambda: self._open_remote(SCREENCAST, [GLib.Variant("o", session)])
            self.captures[widget_id] = capture
            done(capture)

        self._request(SCREENCAST, "CreateSession", [], {"session_handle_token": GLib.Variant("s", self._token())},
                      created, failed)

    # ── Cameras ──

    def camera_present(self) -> bool:
        return bool(self._property(CAMERA, "IsCameraPresent"))

    def camera(self, widget_id: str, *, done: Callable[[Capture], None], failed: Callable[[str], None]) -> None:
        if widget_id in self.captures:
            done(self.captures[widget_id])
            return

        def allowed(_results: dict) -> None:
            try:
                fd = self._open_remote(CAMERA, [])
            except GLib.Error as error:
                failed(error.message)
                return

            # The remote shows only the cameras Streamy may use; its own registry names
            # them. Asking it takes a moment, so it happens off the window's thread. One
            # PipeWire connection per remote: this one is for looking, each stream opens its own.
            def look() -> None:
                nodes = camera_nodes(fd)
                GLib.idle_add(found, nodes)

            def found(nodes) -> bool:
                if not nodes:
                    failed("no camera")
                    return False
                serial, name, size = nodes[0]
                capture = Capture(-1, serial, size, None)
                capture.remote = lambda: self._open_remote(CAMERA, [])
                capture.name = name
                self.captures[widget_id] = capture
                done(capture)
                return False

            threading.Thread(target=look, name="streamy-camera-nodes", daemon=True).start()

        self._request(CAMERA, "AccessCamera", [], {}, allowed, failed)

    # ── Closing ──

    def close(self, widget_id: str) -> None:
        """The widget is gone: close its session (its restore token stays with the widget)."""
        capture = self.captures.pop(widget_id, None)
        if capture is None:
            return
        if capture.session:
            self.bus.call(BUS, capture.session, "org.freedesktop.portal.Session", "Close", None, None,
                          Gio.DBusCallFlags.NONE, -1, None, None)

    def source_for(self, widget) -> tuple[int | None, str | None]:
        """The engine's question: a fresh PipeWire connection (pipewiresrc owns it) and the node to target."""
        capture = self.captures.get(widget.id)
        if capture is None:
            return None, None
        try:
            return capture.remote(), capture.node
        except GLib.Error:
            return None, None
