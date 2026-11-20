# SPDX-License-Identifier: Apache-2.0
"""The ScreenCast, AudioCapture and Camera portals, asynchronously, with restore tokens.

A display, window or app-audio widget owns one portal session. The session stays
open while the widget exists, even when its scene isn't shown, so showing it again
needs no picker: only its PipeWire node is stopped. Restore tokens are single-use:
every Start returns a new one, which the widget keeps.

Restoring never strands a widget: a token the portal refuses (malformed, forgotten,
from an older portal) or a restore that fails is retried once as a fresh pick, and
every way a pick can end reaches ``done`` or ``failed`` with a reason the window can
put into words ("cancelled", "timeout", "failed", "unavailable").
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from gi.repository import Gio, GLib

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
AUDIOCAPTURE = "org.freedesktop.portal.AudioCapture"
CAMERA = "org.freedesktop.portal.Camera"
REQUEST = "org.freedesktop.portal.Request"
REGISTRY = "org.freedesktop.host.portal.Registry"

MONITOR, WINDOW = 1, 2
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2
#: AudioCapture: the person picks applications; Streamy's own sound is left out.
APPLICATIONS, EXCLUDE_SELF = 1, 2

#: An older luma-portal gave up on its picker after D-Bus's default 25 s and answered
#: "cancelled". A cancel that lands right then is that timeout, not the person.
_DBUS_DEFAULT_TIMEOUT = 25.0


def register_host_app(app_id: str) -> None:
    """Tell xdg-desktop-portal who this host app is, before any other portal call on the
    session bus: the picker and the Shell's sharing pill then name Streamy, and restore
    tokens are kept for it rather than for "a host application". A portal without the
    registry (older than 1.19) is fine: nothing changes."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(BUS, PATH, REGISTRY, "Register", GLib.Variant("(sa{sv})", (app_id, {})), None,
                      Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        pass


@dataclass
class Capture:
    """An open capture: the PipeWire fd, the node to target, and what the portal said."""

    fd: int  # unused: each stream takes a connection of its own from ``pool``
    node: str | None
    size: tuple[int, int] | None
    restore_token: str | None
    session: str | None = None
    path: str | None = None
    name: str | None = None
    #: AudioCapture: the applications the person chose, by name.
    applications: list = field(default_factory=list)
    opener: Callable | None = None
    opening: bool = False
    pool: list = field(default_factory=list)

    @property
    def aspect(self) -> float | None:
        if self.size and self.size[1]:
            return self.size[0] / self.size[1]
        return None


class PortalError(RuntimeError):
    pass


def _devices(fd: int, wanted: str):
    """The PipeWire devices a remote shows, through GStreamer's provider (which owns and closes ``fd``)."""
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    factory = Gst.DeviceProviderFactory.find("pipewiredeviceprovider")
    if factory is None:
        os.close(fd)
        return None, []
    provider = factory.get()
    provider.set_property("fd", fd)
    if not provider.start():
        return None, []
    return provider, [d for d in provider.get_devices() if d.get_device_class().startswith(wanted)]


def _prop(device, key: str):
    props = device.get_properties()
    return str(props.get_value(key)) if props is not None and props.has_field(key) else None


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
        self.on_ready: Callable[[str], None] | None = None

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

    def _open_remote_async(self, interface: str, args: list, done: Callable[[int | None], None]) -> None:
        """OpenPipeWireRemote without waiting on the portal: ``done(fd)``, or ``done(None)``."""
        def opened(bus, result) -> None:
            try:
                value, fds = bus.call_with_unix_fd_list_finish(result)
                done(fds.get(value.unpack()[0]))
            except GLib.Error:
                done(None)

        self.bus.call_with_unix_fd_list(BUS, PATH, interface, "OpenPipeWireRemote",
                                        GLib.Variant.new_tuple(*args, GLib.Variant("a{sv}", {})),
                                        GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, 10000, None, None, opened)

    def _property(self, interface: str, name: str):
        try:
            value = self.bus.call_sync(BUS, PATH, "org.freedesktop.DBus.Properties", "Get",
                                       GLib.Variant("(ss)", (interface, name)), GLib.VariantType("(v)"),
                                       Gio.DBusCallFlags.NONE, 2000, None)
            return value.unpack()[0]
        except GLib.Error:
            return None

    # ── Screen, window and application sound ──

    def screencast(self, widget_id: str, *, window: bool, restore_token: str | None,
                   done: Callable[[Capture], None], failed: Callable[[str], None], parent: str = "") -> None:
        """Pick (or restore) a display or a window; ``done`` gets the open capture."""
        cursor_modes = self._property(SCREENCAST, "AvailableCursorModes") or 0
        options = {"types": GLib.Variant("u", WINDOW if window else MONITOR),
                   "multiple": GLib.Variant("b", False)}
        if cursor_modes & CURSOR_EMBEDDED:
            options["cursor_mode"] = GLib.Variant("u", CURSOR_EMBEDDED)
        self._share(SCREENCAST, widget_id, options, restore_token, done, failed, parent)

    def audio_capture_available(self) -> bool:
        """Whether Luma's AudioCapture portal is here (its backend chooses which apps are heard)."""
        types = self._property(AUDIOCAPTURE, "AvailableSourceTypes")
        return bool(types and types & APPLICATIONS)

    def app_audio(self, widget_id: str, *, restore_token: str | None, done: Callable[[Capture], None],
                  failed: Callable[[str], None], parent: str = "") -> None:
        """Pick (or restore) the applications whose sound a widget carries, never Streamy's own."""
        if not self.audio_capture_available():
            failed("unavailable")
            return
        options = {"types": GLib.Variant("u", APPLICATIONS), "multiple": GLib.Variant("b", True),
                   "audio_mode": GLib.Variant("u", EXCLUDE_SELF)}
        self._share(AUDIOCAPTURE, widget_id, options, restore_token, done, failed, parent)

    def _share(self, interface: str, widget_id: str, options: dict, restore_token: str | None,
               done: Callable[[Capture], None], failed: Callable[[str], None], parent: str,
               *, retried: bool = False) -> None:
        """CreateSession → SelectSources → Start → OpenPipeWireRemote, the ScreenCast way.

        A restore token is only a shortcut: if the portal refuses it, or the restored
        share fails, the same widget is asked once more with no token (the picker)."""
        if widget_id in self.captures:
            done(self.captures[widget_id])
            return
        version = self._property(interface, "version") or 1
        token = restore_token if (version >= 4 or interface != SCREENCAST) and not retried else None
        state = {"session": None, "started": 0.0}
        reasons = ("cancelled", "failed", "timeout", "unavailable")
        given_up = failed
        failed = lambda reason: given_up(reason if reason in reasons else "failed")  # noqa: E731

        def again(reason: str) -> None:
            self._close_session(state["session"])
            if token and not retried and reason != "cancelled":
                self._share(interface, widget_id, options, None, done, failed, parent, retried=True)
                return
            if reason == "cancelled" and state["started"] and \
                    abs(time.monotonic() - state["started"] - _DBUS_DEFAULT_TIMEOUT) < 1.5:
                reason = "timeout"  # the picker's own D-Bus call ran out, not the person
            failed(reason)

        def created(results: dict) -> None:
            session = state["session"] = results["session_handle"]
            select = dict(options)
            if version >= 4 or interface != SCREENCAST:
                select["persist_mode"] = GLib.Variant("u", PERSIST_UNTIL_REVOKED)
                if token:
                    select["restore_token"] = GLib.Variant("s", token)
            self._request(interface, "SelectSources", [GLib.Variant("o", session)], select,
                          lambda _r: start(session), again)

        def start(session: str) -> None:
            state["started"] = time.monotonic()
            self._request(interface, "Start", [GLib.Variant("o", session), GLib.Variant("s", parent)], {},
                          lambda results: started(session, results), again)

        def started(session: str, results: dict) -> None:
            streams = results.get("streams") or []
            if not streams:
                again("failed")
                return
            node_id, props = streams[0]
            size = tuple(props["size"]) if "size" in props else None
            # pipewiresrc's target-object is an object.serial, never a node id: pipewire-serial
            # (portal v6) is that serial; an older portal gives only the id, which goes to "path"
            # (pw_stream_connect's target id, as OBS connects).
            serial = props.get("pipewire-serial")
            capture = Capture(-1, str(serial) if serial is not None else None, size,
                              results.get("restore_token"), session)
            capture.path = None if serial is not None else str(node_id)
            capture.applications = [str(a) for a in props.get("applications") or []]
            capture.opener = lambda ready: self._open_remote_async(interface, [GLib.Variant("o", session)], ready)

            def first(fd: int | None) -> None:
                if fd is None:
                    again("failed")
                    return
                capture.pool.append(fd)
                self.captures[widget_id] = capture
                done(capture)

            capture.opener(first)

        self._request(interface, "CreateSession", [], {"session_handle_token": GLib.Variant("s", self._token())},
                      created, failed)

    def _close_session(self, session: str | None) -> None:
        if session:
            self.bus.call(BUS, session, "org.freedesktop.portal.Session", "Close", None, None,
                          Gio.DBusCallFlags.NONE, -1, None, None)

    # ── Cameras ──

    def camera_present(self) -> bool:
        return bool(self._property(CAMERA, "IsCameraPresent"))

    def camera(self, widget_id: str, *, done: Callable[[Capture], None], failed: Callable[[str], None]) -> None:
        if widget_id in self.captures:
            done(self.captures[widget_id])
            return

        def allowed(_results: dict) -> None:
            self._open_remote_async(CAMERA, [], lambda fd: look_on(fd) if fd is not None else failed("failed"))

        def look_on(fd: int) -> None:
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
                capture.opener = lambda ready: self._open_remote_async(CAMERA, [], ready)
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
        for fd in capture.pool:
            os.close(fd)
        capture.pool.clear()
        self._close_session(capture.session)

    def source_for(self, widget) -> tuple[int | None, str | None]:
        """The engine's question: a PipeWire connection of its own (pipewiresrc owns it) and the node
        (an object.serial, or "path:<id>"). Connections are opened ahead without waiting on the portal;
        when none is ready this says (None, None) and ``on_ready(widget_id)`` follows once one is."""
        capture = self.captures.get(widget.id)
        if capture is None:
            return None, None
        target = capture.node or (f"path:{capture.path}" if capture.path else None)
        if capture.pool:
            fd = capture.pool.pop()
            self._refill(widget.id, capture)
            return fd, target
        self._refill(widget.id, capture, notify=True)
        return None, None

    def _refill(self, widget_id: str, capture: "Capture", notify: bool = False) -> None:
        if capture.opening or capture.opener is None or capture.pool:
            return
        capture.opening = True

        def ready(fd: int | None) -> None:
            capture.opening = False
            if self.captures.get(widget_id) is not capture:
                if fd is not None:
                    os.close(fd)
                return
            if fd is not None:
                capture.pool.append(fd)
                if notify and self.on_ready is not None:
                    self.on_ready(widget_id)

        capture.opener(ready)
