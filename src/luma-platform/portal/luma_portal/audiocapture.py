# SPDX-License-Identifier: Apache-2.0
"""The Audio Capture portal backend.

An application asking org.freedesktop.portal.AudioCapture to record other
applications' sound reaches xdg-desktop-portal (patch 0004), which asks this
backend. The backend shows the picker (capture_picker.py): the apps playing
now, with their icons and live levels, and "All sound except <caller>". The
chosen sound becomes one PipeWire node, made by WirePlumber through
luma-audio-devices (org.projectluma.AudioDevices1.CreateCapture); the
frontend then gives the application a PipeWire remote that sees that node
and nothing else.

Rules this file keeps, like screencast.py:

- Nothing is captured before the person chose: the capture is created after
  the picker answers (or after a restore the frontend vouched for).
- The caller is always answered, whatever fails.
- The capture lives exactly as long as the portal session. It is owned by
  this process on the bus, so it also ends if this process does.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .lock import SessionLock  # noqa: E402

LOGGER = logging.getLogger("luma-portal")

AUDIO_NAME = "org.projectluma.AudioDevices"
AUDIO_PATH = "/org/projectluma/AudioDevices"
AUDIO_IFACE = "org.projectluma.AudioDevices1"

PORTAL_SUCCESS = 0
PORTAL_CANCELLED = 1

SOURCE_APPLICATIONS = 1
SOURCE_SYSTEM = 2
AVAILABLE_SOURCE_TYPES = SOURCE_APPLICATIONS | SOURCE_SYSTEM

AUDIO_MODE_EXCLUDE_SELF = 2
AUDIO_MODE_INCLUDE_SELF = 4
AVAILABLE_AUDIO_MODES = AUDIO_MODE_EXCLUDE_SELF | AUDIO_MODE_INCLUDE_SELF

PERSIST_NONE = 0

#: What a restore token stands for, as the frontend hands it back.
RESTORE_VENDOR = "luma"
RESTORE_VERSION = 1

INTERFACE = """
<node>
  <interface name='org.freedesktop.impl.portal.AudioCapture'>
    <method name='CreateSession'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='o' name='session_handle' direction='in'/>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='a{sv}' name='options' direction='in'/>
      <arg type='u' name='response' direction='out'/>
      <arg type='a{sv}' name='results' direction='out'/>
    </method>
    <method name='SelectSources'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='o' name='session_handle' direction='in'/>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='a{sv}' name='options' direction='in'/>
      <arg type='u' name='response' direction='out'/>
      <arg type='a{sv}' name='results' direction='out'/>
    </method>
    <method name='Start'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='o' name='session_handle' direction='in'/>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='s' name='parent_window' direction='in'/>
      <arg type='a{sv}' name='options' direction='in'/>
      <arg type='u' name='response' direction='out'/>
      <arg type='a{sv}' name='results' direction='out'/>
    </method>
    <property name='AvailableSourceTypes' type='u' access='read'/>
    <property name='AvailableAudioModes' type='u' access='read'/>
    <property name='version' type='u' access='read'/>
  </interface>
</node>
"""

SESSION_INTERFACE = """
<node>
  <interface name='org.freedesktop.impl.portal.Session'>
    <method name='Close'/>
    <signal name='Closed'/>
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


def unpack(options) -> dict:
    out = {}
    for key, value in (options or {}).items():
        out[key] = value.unpack() if isinstance(value, GLib.Variant) else value
    return out


def choice_from_restore(data, types: int) -> dict | None:
    """The choice a restore token stands for, if it is still one this
    session may make. Anything else shows the picker again."""
    if not isinstance(data, (tuple, list)) or len(data) != 3:
        return None
    vendor, version, payload = data
    if vendor != RESTORE_VENDOR or version != RESTORE_VERSION:
        return None
    if isinstance(payload, GLib.Variant):
        payload = payload.unpack()
    if not isinstance(payload, dict):
        return None
    mode = payload.get("mode")
    if isinstance(mode, GLib.Variant):
        mode = mode.unpack()
    apps_chosen = payload.get("apps") or []
    if isinstance(apps_chosen, GLib.Variant):
        apps_chosen = apps_chosen.unpack()
    names = payload.get("names") or []
    if isinstance(names, GLib.Variant):
        names = names.unpack()
    if mode == "system" and types & SOURCE_SYSTEM:
        return {"mode": "system", "apps": [], "names": []}
    if mode == "apps" and types & SOURCE_APPLICATIONS and apps_chosen:
        return {"mode": "apps", "apps": [str(a) for a in apps_chosen],
                "names": [str(n) for n in names]}
    return None


def restore_data(choice: dict) -> GLib.Variant:
    payload = GLib.Variant("a{sv}", {
        "mode": GLib.Variant("s", choice["mode"]),
        "apps": GLib.Variant("as", list(choice.get("apps") or [])),
        "names": GLib.Variant("as", list(choice.get("names") or [])),
    })
    return GLib.Variant("(suv)", (RESTORE_VENDOR, RESTORE_VERSION, payload))


def app_name(app_id: str) -> str:
    """What to call the caller in "All sound except …"."""
    if app_id:
        desktop_app_info = getattr(Gio, "DesktopAppInfo", None)
        try:
            info = desktop_app_info.new(app_id + ".desktop") if desktop_app_info else None
        except TypeError:
            info = None
        if info is not None:
            return info.get_display_name() or info.get_name() or app_id
        return app_id.rsplit(".", 1)[-1]
    return "this app"


def answer(invocation, response: int, results=None) -> None:
    invocation.return_value(GLib.Variant("(ua{sv})", (response, results or {})))


class Session:
    def __init__(self, handle: str, app_id: str) -> None:
        self.handle = handle
        self.app_id = app_id
        self.types = AVAILABLE_SOURCE_TYPES
        self.multiple = False
        self.audio_mode = AUDIO_MODE_EXCLUDE_SELF
        self.persist_mode = PERSIST_NONE
        self.restored: dict | None = None
        self.capture_id: str | None = None
        self.picker = None
        self.closed = False
        #: A Start waiting for the session to be unlocked, or None.
        self.waiting = None


class AudioCapturePortal:
    """org.freedesktop.impl.portal.AudioCapture."""

    def __init__(self, connection, object_path: str, show_picker, lock=None) -> None:
        """show_picker(app_id, app_name, types, multiple, parent_window,
        on_done) presents the picker and returns an object with close();
        on_done(choice or None). Nothing is asked or started while the
        session is locked (lock.py): a Start waits for unlock."""
        self._connection = connection
        self._lock = lock or SessionLock(connection)
        self._show_picker = show_picker
        self._sessions: dict[str, Session] = {}
        self._registrations: dict[str, int] = {}
        self._node = Gio.DBusNodeInfo.new_for_xml(INTERFACE)
        self._session_node = Gio.DBusNodeInfo.new_for_xml(SESSION_INTERFACE)
        self._request_node = Gio.DBusNodeInfo.new_for_xml(REQUEST_INTERFACE)
        connection.register_object(object_path, self._node.interfaces[0],
                                   self._call, self._get, None)
        LOGGER.info("audio capture backend ready")

    def _get(self, _c, _s, _p, _i, prop):
        if prop == "AvailableSourceTypes":
            return GLib.Variant("u", AVAILABLE_SOURCE_TYPES)
        if prop == "AvailableAudioModes":
            return GLib.Variant("u", AVAILABLE_AUDIO_MODES)
        if prop == "version":
            return GLib.Variant("u", 1)
        return None

    def _call(self, _c, _sender, _p, _i, method, parameters, invocation) -> None:
        handler = {
            "CreateSession": self._create_session,
            "SelectSources": self._select_sources,
            "Start": self._start,
        }.get(method)
        if handler is None:
            invocation.return_error_literal(Gio.dbus_error_quark(),
                                            Gio.DBusError.UNKNOWN_METHOD, method)
            return
        try:
            handler(parameters, invocation)
        except Exception:  # noqa: BLE001 - the caller is always answered
            LOGGER.exception("audio capture %s failed", method)
            answer(invocation, PORTAL_CANCELLED)

    # -- Sessions ---------------------------------------------------------

    def _create_session(self, parameters, invocation) -> None:
        _handle, session_handle, app_id, _options = parameters.unpack()
        session = Session(session_handle, app_id)
        self._sessions[session_handle] = session
        self._registrations[session_handle] = self._connection.register_object(
            session_handle, self._session_node.interfaces[0],
            lambda *args: self._session_call(session, args[-1]), None, None)
        LOGGER.info("audio capture session %s for %r", session_handle,
                    app_id or "a host application")
        answer(invocation, PORTAL_SUCCESS, {"session_handle": GLib.Variant("s", session_handle)})

    def _session_call(self, session: Session, invocation) -> None:
        if invocation.get_method_name() == "Close":
            self.close(session, notify=False)
        invocation.return_value(None)

    def close(self, session: Session, notify: bool = True) -> None:
        if session.closed:
            return
        session.closed = True
        if session.waiting is not None:
            answer(session.waiting, PORTAL_CANCELLED)
            session.waiting = None
        if session.picker is not None:
            session.picker.close()
            session.picker = None
        self._close_capture(session)
        self._sessions.pop(session.handle, None)
        registration = self._registrations.pop(session.handle, 0)
        if registration:
            self._connection.unregister_object(registration)
        if notify:
            try:
                self._connection.emit_signal(None, session.handle,
                                             "org.freedesktop.impl.portal.Session",
                                             "Closed", None)
            except GLib.Error:
                pass

    def _close_capture(self, session: Session) -> None:
        if not session.capture_id:
            return
        capture_id, session.capture_id = session.capture_id, None
        self._connection.call(AUDIO_NAME, AUDIO_PATH, AUDIO_IFACE, "CloseCapture",
                              GLib.Variant("(s)", (capture_id,)), None,
                              Gio.DBusCallFlags.NONE, -1, None, None, None)

    # -- SelectSources ----------------------------------------------------

    def _select_sources(self, parameters, invocation) -> None:
        _handle, session_handle, _app_id, options = parameters.unpack()
        session = self._sessions.get(session_handle)
        if session is None:
            answer(invocation, PORTAL_CANCELLED)
            return
        options = unpack(options)
        session.types = int(options.get("types") or AVAILABLE_SOURCE_TYPES) & AVAILABLE_SOURCE_TYPES
        session.types = session.types or AVAILABLE_SOURCE_TYPES
        session.multiple = bool(options.get("multiple"))
        session.audio_mode = int(options.get("audio_mode") or AUDIO_MODE_EXCLUDE_SELF)
        session.persist_mode = int(options.get("persist_mode") or PERSIST_NONE)
        if "restore_data" in options:
            session.restored = choice_from_restore(options["restore_data"], session.types)
        answer(invocation, PORTAL_SUCCESS)

    # -- Start ------------------------------------------------------------

    def _start(self, parameters, invocation) -> None:
        handle, session_handle, app_id, parent_window, _options = parameters.unpack()
        session = self._sessions.get(session_handle)
        if session is None:
            answer(invocation, PORTAL_CANCELLED)
            return
        if self._lock.locked:
            LOGGER.info("the session is locked; audio capture %s waits for unlock", session_handle)
            session.waiting = invocation

            def resume():
                if session.waiting is invocation:
                    session.waiting = None
                    self._start_now(session, handle, app_id, parent_window, invocation)
            self._lock.when_unlocked(resume)
            return
        self._start_now(session, handle, app_id, parent_window, invocation)

    def _start_now(self, session: Session, handle, app_id, parent_window, invocation) -> None:
        session_handle = session.handle
        if session.restored is not None:
            LOGGER.info("session %s restored a previous choice", session_handle)
            self._begin(session, invocation, session.restored)
            return

        request = {"registration": 0}

        def request_call(*args):
            inv = args[-1]
            if inv.get_method_name() == "Close":
                LOGGER.info("the application closed its request for %s", session_handle)
                self.close(session)
            inv.return_value(None)

        try:
            request["registration"] = self._connection.register_object(
                handle, self._request_node.interfaces[0], request_call, None, None)
        except GLib.Error:
            pass

        def done(choice) -> None:
            session.picker = None
            if request["registration"]:
                self._connection.unregister_object(request["registration"])
                request["registration"] = 0
            if session.closed:
                return
            if not choice:
                answer(invocation, PORTAL_CANCELLED)
                return
            self._begin(session, invocation, choice)

        session.picker = self._show_picker(app_id, app_name(app_id), session.types,
                                           session.multiple, parent_window, done)

    def _begin(self, session: Session, invocation, choice: dict) -> None:
        include_self = session.audio_mode == AUDIO_MODE_INCLUDE_SELF
        label = ", ".join(choice.get("names") or []) if choice["mode"] == "apps" else "All sound"

        def created(source, result, _data=None) -> None:
            try:
                capture_id, node_id, serial = source.call_finish(result).unpack()
            except GLib.Error as error:
                LOGGER.error("the capture could not be created: %s", error.message)
                answer(invocation, PORTAL_CANCELLED)
                return
            if session.closed:
                self._connection.call(AUDIO_NAME, AUDIO_PATH, AUDIO_IFACE, "CloseCapture",
                                      GLib.Variant("(s)", (capture_id,)), None,
                                      Gio.DBusCallFlags.NONE, -1, None, None, None)
                return
            session.capture_id = capture_id
            source_type = SOURCE_SYSTEM if choice["mode"] == "system" else SOURCE_APPLICATIONS
            stream = (node_id, {
                "source_type": GLib.Variant("u", source_type),
                "applications": GLib.Variant("as", list(choice.get("names") or [])),
                "pipewire-serial": GLib.Variant("t", serial),
            })
            results = {"streams": GLib.Variant("a(ua{sv})", [stream])}
            if session.persist_mode != PERSIST_NONE:
                results["persist_mode"] = GLib.Variant("u", session.persist_mode)
                results["restore_data"] = restore_data(choice)
            LOGGER.info("session %s captures node %d (%s)", session.handle, node_id, choice["mode"])
            answer(invocation, PORTAL_SUCCESS, results)

        self._connection.call(
            AUDIO_NAME, AUDIO_PATH, AUDIO_IFACE, "CreateCapture",
            GLib.Variant("(ssasbs)", (session.app_id or "", choice["mode"],
                                      list(choice.get("apps") or []), include_self, label)),
            GLib.VariantType("(sut)"), Gio.DBusCallFlags.NONE, 15000, None, created, None)
