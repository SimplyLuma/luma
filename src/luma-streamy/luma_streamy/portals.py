# SPDX-License-Identifier: Apache-2.0
"""The ScreenCast and Camera portals, and app sound through luma_appkit.audio_capture.

A display or window widget owns one ScreenCast session. The session stays open while
the widget exists, even when its scene isn't shown, so showing it again needs no
picker: only its PipeWire node is stopped. An app-audio widget owns one capture
(luma_appkit's AudioCapture) while its scene is shown; off-scene it is closed and
comes back from its restore token. Restore tokens are single-use: every share
returns a new one, which the widget keeps.

Restoring never strands a widget: a token the portal refuses (malformed, forgotten,
from an older portal) or a restore that fails is retried once as a fresh pick, and
every way a pick can end reaches ``done`` or ``failed`` with a reason the window can
put into words ("cancelled", "timeout", "failed", "unavailable").
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from gi.repository import Gio, GLib

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
CAMERA = "org.freedesktop.portal.Camera"
REQUEST = "org.freedesktop.portal.Request"
REGISTRY = "org.freedesktop.host.portal.Registry"

MONITOR, WINDOW = 1, 2
CURSOR_HIDDEN, CURSOR_EMBEDDED = 1, 2
PERSIST_UNTIL_REVOKED = 2
#: An older luma-portal gave up on its picker after D-Bus's default 25 s and answered
#: "cancelled". A cancel that lands right then is that timeout, not the person.
_DBUS_DEFAULT_TIMEOUT = 25.0


def log(text: str) -> None:
    """One line for the journal (the app runs as a systemd unit; stderr is its journal)."""
    print(f"streamy: {text}", file=sys.stderr, flush=True)


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
    #: App audio: what is heard, in words ("Spotify, Firefox", "All sound"), and the
    #: luma_appkit capture that owns the session and the remote.
    description: str | None = None
    owner: object = None
    opener: Callable | None = None
    opening: bool = False
    pool: list = field(default_factory=list)
    watch: int = 0
    camera: object = None

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


def node_serial(fd: int, node_id: int) -> str | None:
    """The object.serial of node ``node_id`` on a portal's PipeWire remote (which owns ``fd``), or None.

    A portal older than ScreenCast v6 gives only the node id; pipewiresrc's target-object
    wants the serial, which the remote's own registry knows."""
    provider, devices = _devices(fd, "Video/Source")
    if provider is None:
        return None
    try:
        for device in devices:
            ids = {_prop(device, "object.id"), _prop(device, "node.id")}
            if device.find_property("id") is not None:
                ids.add(str(device.get_property("id")))
            if str(node_id) in ids:
                serial = _prop(device, "object.serial")
                if serial is None and device.find_property("serial") is not None:
                    serial = str(device.get_property("serial"))
                return serial
        return None
    finally:
        provider.stop()


@dataclass(frozen=True)
class Camera:
    """A camera on the camera portal's remote."""

    target: str  # object.serial (what this remote's pipewiresrc connects to)
    name: str  # node.name, stable across boots: what a widget remembers
    label: str  # "Built-in Front Camera"
    modes: tuple = ()  # ((width, height, fps), ...) best first

    @property
    def size(self) -> tuple[int, int] | None:
        return self.modes[0][:2] if self.modes else None


def _modes(caps) -> tuple:
    """(width, height, fps) a camera offers, largest first, whole frame rates."""
    found = set()
    if caps is None:
        return ()
    for i in range(caps.get_size()):
        st = caps.get_structure(i)
        ok_w, width = st.get_int("width")
        ok_h, height = st.get_int("height")
        if not (ok_w and ok_h):
            continue
        rates = []
        if st.has_field("framerate"):
            value = st.get_value("framerate")
            for v in (value if isinstance(value, list) else [value]):
                try:
                    rates.append(round(v.num / v.denom) if hasattr(v, "num") else round(float(v)))
                except (TypeError, ZeroDivisionError, ValueError):
                    pass
            ok_f, num, den = st.get_fraction("framerate")
            if ok_f and den:
                rates.append(round(num / den))
        for fps in rates or [30]:
            if fps > 0:
                found.add((width, height, fps))
    return tuple(sorted(found, key=lambda m: (m[0] * m[1], m[2]), reverse=True))


def _camera(device) -> Camera | None:
    target = _prop(device, "object.serial") or _prop(device, "node.name")
    if target is None:
        return None
    return Camera(target, _prop(device, "node.name") or target, device.get_display_name(), _modes(device.get_caps()))


class CameraWatch:
    """The cameras Streamy may use, kept current on a thread of its own (one PipeWire connection on the
    camera portal's remote); ``on_changed`` runs on the window's thread when one comes or goes."""

    def __init__(self, fd: int, on_changed: Callable[[], None]) -> None:
        self.cameras: list[Camera] = []
        self.ready = False
        self.on_changed = on_changed
        self._lock = threading.Lock()
        threading.Thread(target=self._run, args=(fd,), name="streamy-cameras", daemon=True).start()

    def _run(self, fd: int) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        provider, _devices_now = _devices(fd, "Video/Source")
        if provider is None:
            self._publish([])
            return
        self._provider = provider
        bus = provider.get_bus()
        self._publish(provider)
        while True:
            message = bus.timed_pop(Gst.CLOCK_TIME_NONE)
            if message is None:
                break
            if message.type in (Gst.MessageType.DEVICE_ADDED, Gst.MessageType.DEVICE_REMOVED,
                                Gst.MessageType.DEVICE_CHANGED):
                self._publish(provider)

    def _publish(self, provider) -> None:
        cameras = []
        if provider:
            for device in provider.get_devices():
                if device.get_device_class().startswith("Video/Source"):
                    camera = _camera(device)
                    if camera is not None:
                        cameras.append(camera)
        with self._lock:
            self.cameras, self.ready = cameras, True
        GLib.idle_add(lambda: self.on_changed() and False)

    def find(self, name: str | None) -> Camera | None:
        with self._lock:
            if name:
                return next((c for c in self.cameras if c.name == name), None)
            return self.cameras[0] if self.cameras else None


class Portals:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.captures: dict[str, Capture] = {}  # widget id -> open capture
        self.on_ready: Callable[[str], None] | None = None
        #: A capture the portal ended by itself (widget id).
        self.on_ended: Callable[[str], None] | None = None
        #: The cameras, once the camera portal said yes (CameraWatch); ``on_cameras()`` follows them.
        self.cameras: CameraWatch | None = None
        self.on_cameras: Callable[[], None] | None = None
        self._camera_waiters: list[Callable[[], None]] = []

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
                   done: Callable[[Capture], None], failed: Callable[[str], None], parent: str = "",
                   cursor: bool = True) -> None:
        """Pick (or restore) a display or a window; ``done`` gets the open capture. ``cursor``: the
        pointer drawn into the picture, or left out."""
        cursor_modes = self._property(SCREENCAST, "AvailableCursorModes") or 0
        options = {"types": GLib.Variant("u", WINDOW if window else MONITOR),
                   "multiple": GLib.Variant("b", False)}
        if cursor and cursor_modes & CURSOR_EMBEDDED:
            options["cursor_mode"] = GLib.Variant("u", CURSOR_EMBEDDED)
        elif not cursor and cursor_modes & CURSOR_HIDDEN:
            options["cursor_mode"] = GLib.Variant("u", CURSOR_HIDDEN)
        self._share(SCREENCAST, widget_id, options, restore_token, done, failed, parent)

    def audio_capture_available(self) -> bool:
        """Whether Luma's Audio Capture portal is here (the Share sound picker chooses what is heard)."""
        from luma_appkit import audio_capture

        return audio_capture.is_available(self.bus)

    def app_audio(self, widget_id: str, *, restore_token: str | None, done: Callable[[Capture], None],
                  failed: Callable[[str], None], parent: str = "", retried: bool = False) -> None:
        """Pick (or restore) the sound a widget carries: chosen apps, or all sound but Streamy's own.
        A token the portal no longer honours falls back to the picker once, as screen shares do."""
        from luma_appkit import audio_capture

        if widget_id in self.captures:
            done(self.captures[widget_id])
            return
        if not self.audio_capture_available():
            failed("unavailable")
            return

        def ready(granted, error) -> None:
            if error is not None:
                if error.cancelled:
                    failed("cancelled")
                elif restore_token and not retried:
                    self.app_audio(widget_id, restore_token=None, done=done, failed=failed, parent=parent,
                                   retried=True)
                else:
                    failed("failed")
                return
            capture = Capture(-1, granted.target, None, granted.restore_token, granted.session_handle)
            capture.description = granted.description
            capture.owner = granted
            # The capture owns its remote; each pipewiresrc gets a duplicate of it to own and close.
            capture.opener = lambda give: give(os.dup(granted.fd) if granted.fd >= 0 else None)
            granted.on_closed = lambda _c: self._ended(widget_id, capture)
            capture.pool.append(os.dup(granted.fd))
            self.captures[widget_id] = capture
            done(capture)

        audio_capture.request_audio_capture(parent or None, ready, types=audio_capture.APPLICATIONS |
                                            audio_capture.ALL_SOUND, multiple=True, include_self=False,
                                            persist=True, restore_token=restore_token, connection=self.bus)

    def _ended(self, widget_id: str, capture: "Capture") -> None:
        """The portal ended a capture (the person stopped sharing): the widget hears so."""
        if self.captures.get(widget_id) is capture:
            self.close(widget_id)
            if self.on_ended is not None:
                self.on_ended(widget_id)

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
        token = restore_token if version >= 4 and not retried else None
        state = {"session": None, "started": 0.0}
        reasons = ("cancelled", "failed", "timeout")
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
            if version >= 4:
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
            capture.opener = lambda ready: self._open_remote_async(interface, [GLib.Variant("o", session)], ready)

            def first(fd: int | None) -> None:
                if fd is None:
                    again("failed")
                    return
                capture.pool.append(fd)
                self.captures[widget_id] = capture
                self._watch_session(widget_id, capture)
                if capture.node is not None:
                    done(capture)
                    return
                # No pipewire-serial (a portal before ScreenCast v6): ask the remote's registry
                # for the node's serial, off the window's thread; the node id is the fallback.
                def resolved(look_fd: int | None) -> None:
                    if look_fd is None:
                        done(capture)
                        return

                    def look() -> None:
                        serial = node_serial(look_fd, node_id)
                        GLib.idle_add(found, serial)

                    def found(serial) -> bool:
                        if serial is not None:
                            capture.node, capture.path = serial, None
                        log(f"portal stream {node_id}: " + (f"object.serial {serial}" if serial is not None
                                                             else "no serial on the remote, connecting by id"))
                        done(capture)
                        return False

                    threading.Thread(target=look, name="streamy-node-serial", daemon=True).start()

                capture.opener(resolved)

            capture.opener(first)

        self._request(interface, "CreateSession", [], {"session_handle_token": GLib.Variant("s", self._token())},
                      created, failed)

    def _watch_session(self, widget_id: str, capture: "Capture") -> None:
        """The portal closing a share (the person stopped it in the Shell, the window went) ends the widget."""
        if not capture.session:
            return

        def closed(*_args) -> None:
            self.bus.signal_unsubscribe(capture.watch)
            capture.watch = 0
            log(f"portal session for {widget_id} closed")
            self._ended(widget_id, capture)

        capture.watch = self.bus.signal_subscribe(BUS, "org.freedesktop.portal.Session", "Closed", capture.session,
                                                  None, Gio.DBusSignalFlags.NONE, closed)

    def _close_session(self, session: str | None) -> None:
        if session:
            self.bus.call(BUS, session, "org.freedesktop.portal.Session", "Close", None, None,
                          Gio.DBusCallFlags.NONE, -1, None, None)

    # ── Cameras ──

    def camera_present(self) -> bool:
        return bool(self._property(CAMERA, "IsCameraPresent"))

    def _cameras_changed(self) -> None:
        waiters, self._camera_waiters = self._camera_waiters, []
        for then in waiters:
            then()
        if self.on_cameras is not None:
            self.on_cameras()

    def watch_cameras(self, then: Callable[[], None], failed: Callable[[str], None]) -> None:
        """Ask the camera portal once, then keep the list of cameras current; ``then()`` once it is known."""
        if self.cameras is not None:
            if self.cameras.ready:
                then()
            else:
                self._camera_waiters.append(then)
            return

        def allowed(_results: dict) -> None:
            def opened(fd: int | None) -> None:
                if fd is None:
                    failed("failed")
                    return
                self._camera_waiters.append(then)
                # The remote shows only the cameras Streamy may use; one connection is kept for
                # watching them, each stream opens its own.
                self.cameras = CameraWatch(fd, self._cameras_changed)

            self._open_remote_async(CAMERA, [], opened)

        def refused(reason: str) -> None:
            failed("denied" if reason == "cancelled" else reason)

        self._request(CAMERA, "AccessCamera", [], {}, allowed, refused)

    def camera(self, widget_id: str, *, done: Callable[[Capture], None], failed: Callable[[str], None],
               name: str | None = None) -> None:
        """Open camera ``name`` (a node name; None: the first). "no camera" when there is none,
        "missing" when that one is gone, "denied" when the portal says no."""
        if widget_id in self.captures:
            done(self.captures[widget_id])
            return

        def known() -> None:
            camera = self.cameras.find(name)
            if camera is None:
                failed("missing" if name and self.cameras.cameras else "no camera")
                return
            capture = Capture(-1, camera.target, camera.size, None)
            capture.opener = lambda ready: self._open_remote_async(CAMERA, [], ready)
            capture.name = camera.label
            capture.camera = camera
            self.captures[widget_id] = capture
            done(capture)

        self.watch_cameras(known, failed)

    # ── Closing ──

    def close(self, widget_id: str) -> None:
        """The widget is gone: close its session (its restore token stays with the widget)."""
        capture = self.captures.pop(widget_id, None)
        if capture is None:
            return
        for fd in capture.pool:
            os.close(fd)
        capture.pool.clear()
        if capture.owner is not None:
            capture.owner.on_closed = None
            capture.owner.close()  # ends its session and closes its remote
        else:
            if capture.watch:
                self.bus.signal_unsubscribe(capture.watch)
                capture.watch = 0
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
