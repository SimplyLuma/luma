"""Record other apps' sound through the Audio Capture portal.

An app asks once; the person picks which apps it may hear (or all sound but
the app's own) in the Share sound picker. The app then gets a PipeWire remote
that sees one node carrying exactly that sound, and nothing else: no
microphone, no outputs, no other app. New streams of the chosen apps join the
node on their own while the capture lives.

    from luma_appkit.audio_capture import AudioCapture, request_audio_capture

    def ready(capture, error):
        if error:
            show(error)                 # cancelled, or no portal
            return
        settings.set_string("audio-token", capture.restore_token or "")
        source = capture.make_source()  # a configured pipewiresrc
        ...
        capture.close()                 # when done; also on app exit

    request_audio_capture(window, ready, multiple=True,
                          restore_token=settings.get_string("audio-token"))

With a valid restore token (from a capture made with ``persist=True``) the
picker is skipped and the same choice applies again, across restarts and
sign-ins, until the person revokes it.

For a GStreamer pipeline string, ``capture.pipeline_description()`` returns
``pipewiresrc fd=N target-object=S ...``. The capture's file descriptor must
stay open as long as the pipeline uses it; ``close()`` closes it and ends
the portal session.

The interface is org.freedesktop.portal.AudioCapture, version 1. This module
is the supported way to use it from Luma apps; if the interface changes when
it is proposed upstream, this API stays.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from typing import Callable, Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

__all__ = [
    "APPLICATIONS", "ALL_SOUND", "AudioCapture", "AudioCaptureError",
    "request_audio_capture", "is_available",
]

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.AudioCapture"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

#: What the person may choose from (AvailableSourceTypes bits).
APPLICATIONS = 1
ALL_SOUND = 2

AUDIO_MODE_EXCLUDE_SELF = 2
AUDIO_MODE_INCLUDE_SELF = 4

PERSIST_NONE = 0
PERSIST_PERSISTENT = 2


class AudioCaptureError(Exception):
    """``cancelled`` is True when the person said no."""

    def __init__(self, message: str, cancelled: bool = False) -> None:
        super().__init__(message)
        self.cancelled = cancelled


@dataclass
class AudioCapture:
    """A granted capture. Valid until close() or the portal ends it."""

    session_handle: str
    fd: int
    node_id: int
    serial: int
    source_type: int
    applications: list[str] = field(default_factory=list)
    restore_token: Optional[str] = None
    _connection: Optional[Gio.DBusConnection] = None
    _closed_id: int = 0
    on_closed: Optional[Callable[["AudioCapture"], None]] = None

    @property
    def target(self) -> str:
        """The value for pipewiresrc's target-object."""
        return str(self.serial or self.node_id)

    @property
    def description(self) -> str:
        """What is being recorded, in words for the app's UI."""
        if self.source_type == ALL_SOUND:
            return "All sound"
        return ", ".join(self.applications) or "App sound"

    def pipeline_description(self, client_name: str = "") -> str:
        """A gst-launch style source; the fd must stay open while it runs."""
        name = f" client-name={_quote(client_name)}" if client_name else ""
        return f"pipewiresrc fd={self.fd} target-object={self.target} do-timestamp=true{name}"

    def make_source(self, name: str = "audio-capture"):
        """A pipewiresrc on this capture (GStreamer must be initialised)."""
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        source = Gst.ElementFactory.make("pipewiresrc", name)
        if source is None:
            raise AudioCaptureError("pipewiresrc is not available")
        source.set_property("fd", self.fd)
        source.set_property("target-object", self.target)
        source.set_property("do-timestamp", True)
        return source

    def close(self) -> None:
        """End the capture: close the session and the remote."""
        if self._connection is not None:
            if self._closed_id:
                self._connection.signal_unsubscribe(self._closed_id)
                self._closed_id = 0
            self._connection.call(PORTAL_NAME, self.session_handle, SESSION_IFACE, "Close",
                                  None, None, Gio.DBusCallFlags.NONE, -1, None, None, None)
            self._connection = None
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bus() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _token() -> str:
    return "luma_" + secrets.token_hex(8)


def _request_path(connection: Gio.DBusConnection, token: str) -> str:
    sender = connection.get_unique_name().lstrip(":").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"


def is_available(connection: Optional[Gio.DBusConnection] = None) -> bool:
    """True when the portal answers AudioCapture version 1 or later."""
    connection = connection or _bus()
    try:
        reply = connection.call_sync(
            PORTAL_NAME, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (PORTAL_IFACE, "version")), GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return False
    return reply.unpack()[0] >= 1


def _parent_identifier(parent) -> str:
    """A window identifier for the portal (see xdg-desktop-portal's
    window-identifiers), or "" when there is none to give."""
    if parent is None:
        return ""
    if isinstance(parent, str):
        return parent
    return getattr(parent, "luma_portal_parent", "") or ""


class _Flow:
    """CreateSession → SelectSources → Start → OpenPipeWireRemote."""

    def __init__(self, connection, parent, callback, *, types, multiple, include_self,
                 persist, restore_token) -> None:
        self.connection = connection
        self.parent = parent
        self.callback = callback
        self.types = types
        self.multiple = multiple
        self.include_self = include_self
        self.persist = persist
        self.restore_token = restore_token
        self.session_handle = ""
        self.subscription = 0

    def run(self) -> None:
        session_token = _token()
        self._request("CreateSession", lambda options: GLib.Variant("(a{sv})", (dict(
            options, session_handle_token=GLib.Variant("s", session_token)),)), self._created)

    def _request(self, method: str, build_args, on_results) -> None:
        token = _token()
        path = _request_path(self.connection, token)

        def on_response(_c, _s, _p, _i, _n, parameters, _d) -> None:
            self.connection.signal_unsubscribe(self.subscription)
            self.subscription = 0
            response, results = parameters.unpack()
            if response == 0:
                on_results(results)
            elif response == 1:
                self._fail("The request was cancelled.", cancelled=True)
            else:
                self._fail("The sound could not be shared.")

        # Subscribe before calling, so a fast answer is never missed.
        self.subscription = self.connection.signal_subscribe(
            PORTAL_NAME, REQUEST_IFACE, "Response", path, None,
            Gio.DBusSignalFlags.NONE,
            on_response, None)
        args = build_args({"handle_token": GLib.Variant("s", token)})

        def called(source, result, _d) -> None:
            try:
                source.call_finish(result)
            except GLib.Error as error:
                if self.subscription:
                    self.connection.signal_unsubscribe(self.subscription)
                    self.subscription = 0
                self._fail(error.message)

        self.connection.call(PORTAL_NAME, PORTAL_PATH, PORTAL_IFACE, method, args,
                             GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None, called, None)

    def _created(self, results) -> None:
        self.session_handle = results.get("session_handle", "")
        if not self.session_handle:
            self._fail("The portal did not create a session.")
            return
        options = {
            "types": GLib.Variant("u", self.types),
            "multiple": GLib.Variant("b", self.multiple),
            "audio_mode": GLib.Variant("u", AUDIO_MODE_INCLUDE_SELF if self.include_self
                                       else AUDIO_MODE_EXCLUDE_SELF),
            "persist_mode": GLib.Variant("u", PERSIST_PERSISTENT if self.persist else PERSIST_NONE),
        }
        if self.restore_token:
            options["restore_token"] = GLib.Variant("s", self.restore_token)
        self._request("SelectSources",
                      lambda base: GLib.Variant("(oa{sv})", (self.session_handle, dict(base, **options))),
                      self._selected)

    def _selected(self, _results) -> None:
        self._request("Start",
                      lambda base: GLib.Variant("(osa{sv})", (self.session_handle,
                                                             _parent_identifier(self.parent), base)),
                      self._started)

    def _started(self, results) -> None:
        streams = results.get("streams") or []
        if len(streams) != 1:
            self._fail("The portal returned no capture.")
            return
        node_id, props = streams[0]
        capture = AudioCapture(
            session_handle=self.session_handle, fd=-1, node_id=int(node_id),
            serial=int(props.get("pipewire-serial") or 0),
            source_type=int(props.get("source_type") or APPLICATIONS),
            applications=list(props.get("applications") or []),
            restore_token=results.get("restore_token"))

        def opened(source, result, _d) -> None:
            try:
                reply, fd_list = source.call_with_unix_fd_list_finish(result)
            except GLib.Error as error:
                self._fail(error.message)
                return
            (index,) = reply.unpack()
            capture.fd = fd_list.get(index)
            capture._connection = self.connection
            capture._closed_id = self.connection.signal_subscribe(
                PORTAL_NAME, SESSION_IFACE, "Closed", self.session_handle, None,
                Gio.DBusSignalFlags.NONE, lambda *_a: self._closed(capture), None)
            self.callback(capture, None)

        self.connection.call_with_unix_fd_list(
            PORTAL_NAME, PORTAL_PATH, PORTAL_IFACE, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session_handle, {})), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, -1, None, None, opened, None)

    def _closed(self, capture: AudioCapture) -> None:
        capture._connection = None
        if capture.on_closed is not None:
            capture.on_closed(capture)

    def _fail(self, message: str, cancelled: bool = False) -> None:
        if self.session_handle and not cancelled:
            self.connection.call(PORTAL_NAME, self.session_handle, SESSION_IFACE, "Close",
                                 None, None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        self.callback(None, AudioCaptureError(message, cancelled=cancelled))


def request_audio_capture(parent, callback: Callable[[Optional[AudioCapture], Optional[AudioCaptureError]], None],
                          *, types: int = APPLICATIONS | ALL_SOUND, multiple: bool = True,
                          include_self: bool = False, persist: bool = True,
                          restore_token: Optional[str] = None,
                          connection: Optional[Gio.DBusConnection] = None) -> None:
    """Ask for a capture; callback(capture, None) or callback(None, error).

    parent: the asking window's portal identifier string, or None.
    types: APPLICATIONS, ALL_SOUND or both, offered in the picker.
    multiple: whether several apps can be chosen.
    include_self: whether all sound keeps the app's own output.
    persist: whether the choice may be remembered (restore_token).
    restore_token: a token from an earlier capture to skip the picker.
    """
    _Flow(connection or _bus(), parent, callback, types=types, multiple=multiple,
          include_self=include_self, persist=persist, restore_token=restore_token).run()
