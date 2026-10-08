# SPDX-License-Identifier: Apache-2.0
"""The ScreenCast portal backend.

An application asking org.freedesktop.portal.ScreenCast to share the screen
reaches xdg-desktop-portal, which asks this backend. The backend asks the
Shell to show Luma's picker (live previews, Windows/Screens, the options) and
then creates the stream through Mutter's ScreenCast API, exactly as
xdg-desktop-portal-gnome does. See ADR-041 for why the picker lives in the
Shell and not in a dialog here.

Three rules this file exists to keep:

- The application never learns about a source that was not chosen. The Shell
  returns only the choice; nothing here enumerates windows.
- Nothing streams before Share. The Mutter session is created in Start(),
  after the picker has answered.
- The caller is always answered. An unanswered portal method leaves the
  application waiting on this process for ever, which is indistinguishable
  from the feature not existing, so every method answers even when it fails.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import secrets

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import mutter  # noqa: E402

LOGGER = logging.getLogger("luma-portal")

SHELL_NAME = "org.gnome.Shell"
SHELL_PATH = "/org/projectluma/Shell/ScreenShare"
SHELL_IFACE = "org.projectluma.Shell.ScreenShare"

PORTAL_SUCCESS = 0
PORTAL_CANCELLED = 1
PORTAL_ENDED = 2

#: Every source type and cursor mode Luma offers.
AVAILABLE_SOURCE_TYPES = mutter.SOURCE_MONITOR | mutter.SOURCE_WINDOW
AVAILABLE_CURSOR_MODES = (mutter.PORTAL_CURSOR_HIDDEN |
                          mutter.PORTAL_CURSOR_EMBEDDED |
                          mutter.PORTAL_CURSOR_METADATA)

#: persist_mode, as the portal defines it.
PERSIST_NONE = 0
PERSIST_TRANSIENT = 1
PERSIST_PERSISTENT = 2

#: How long to wait for Mutter to announce a stream's PipeWire node before
#: giving up and telling the application the share did not happen.
NODE_TIMEOUT_SECONDS = 10

SCREEN_CAST_INTERFACE = """
<node>
  <interface name='org.freedesktop.impl.portal.ScreenCast'>
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
    <property name='AvailableCursorModes' type='u' access='read'/>
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


def unpack(options: dict) -> dict:
    """A plain dict from a a{sv}, whichever way GLib handed it over."""
    out = {}
    for key, value in (options or {}).items():
        out[key] = value.unpack() if isinstance(value, GLib.Variant) else value
    return out


class TokenStore:
    """Restore tokens, bound to the application they were issued to.

    A token is a name for a choice the person already made, and it is honoured
    only for the same application: another application presenting someone
    else's token gets nothing and is asked afresh. Transient tokens live in
    this process; persistent ones live in the user's data directory.
    """

    def __init__(self) -> None:
        self._transient: dict[str, dict] = {}
        base = os.environ.get("XDG_DATA_HOME") or \
            os.path.expanduser("~/.local/share")
        self._path = pathlib.Path(base) / "luma-portal" / "screencast-tokens.json"
        self._persistent = self._read()

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 0600: a token names what someone chose to share.
            temporary = self._path.with_suffix(".json.new")
            temporary.write_text(json.dumps(self._persistent))
            temporary.chmod(0o600)
            temporary.replace(self._path)
        except OSError as error:
            LOGGER.warning("could not save restore tokens: %s", error)

    def issue(self, app_id: str, choice: dict, persist_mode: int) -> str | None:
        if persist_mode == PERSIST_NONE:
            return None
        token = secrets.token_hex(16)
        entry = {"app-id": app_id, "choice": choice}
        if persist_mode == PERSIST_TRANSIENT:
            self._transient[token] = entry
        else:
            self._persistent[token] = entry
            self._write()
        return token

    def take(self, app_id: str, token: str) -> dict | None:
        for store in (self._transient, self._persistent):
            entry = store.get(token)
            if entry is None:
                continue
            if entry.get("app-id") != app_id:
                LOGGER.warning("a restore token was offered by %r, not %r",
                               app_id, entry.get("app-id"))
                return None
            return entry.get("choice")
        return None

    def drop(self, token: str) -> None:
        self._transient.pop(token, None)
        if self._persistent.pop(token, None) is not None:
            self._write()


class Session:
    """One portal session: what was chosen, and the Mutter session for it."""

    def __init__(self, backend, handle: str, app_id: str) -> None:
        self.backend = backend
        self.handle = handle
        self.app_id = app_id
        self.types = 0
        self.multiple = False
        self.cursor_modes = mutter.PORTAL_CURSOR_HIDDEN
        self.persist_mode = PERSIST_NONE
        self.restore_token: str | None = None
        self.choice: dict | None = None
        self.cast: mutter.ScreenCastSession | None = None
        self.closed = False
        #: (invocation, request handle) for a Start that is still being
        #: answered, or None.
        self.pending = None
        self.timeout_id = 0

    def close(self, notify: bool = True) -> None:
        if self.closed:
            return
        self.closed = True
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
            self.timeout_id = 0
        if self.pending is not None:
            invocation, _handle = self.pending
            self.pending = None
            answer(invocation, PORTAL_CANCELLED)
        if self.cast:
            self.cast.stop()
            self.cast = None
        self.backend.forget(self, notify_shell=True)
        if notify:
            self.backend.emit_session_closed(self.handle)


class ScreenCastPortal:
    """org.freedesktop.impl.portal.ScreenCast."""

    def __init__(self, connection, object_path: str) -> None:
        self._connection = connection
        self._object_path = object_path
        self._node = Gio.DBusNodeInfo.new_for_xml(SCREEN_CAST_INTERFACE)
        self._session_node = Gio.DBusNodeInfo.new_for_xml(SESSION_INTERFACE)
        self._request_node = Gio.DBusNodeInfo.new_for_xml(REQUEST_INTERFACE)
        self._sessions: dict[str, Session] = {}
        self._registrations: dict[str, int] = {}
        self._requests: dict[str, int] = {}
        self._tokens = TokenStore()
        connection.register_object(
            object_path, self._node.interfaces[0], self._call, self._get, None)
        # The Shell asks for a session to end when the person presses Stop on
        # the pill. Mutter has already torn the stream down by then; this is
        # what tells the application its session is over.
        connection.signal_subscribe(
            None, SHELL_IFACE, "StopRequested", SHELL_PATH, None,
            Gio.DBusSignalFlags.NONE, self._on_stop_requested, None)
        LOGGER.info("screen sharing backend ready")

    # -- Interface --------------------------------------------------------

    def _get(self, _connection, _sender, _path, _iface, prop):
        if prop == "AvailableSourceTypes":
            return GLib.Variant("u", AVAILABLE_SOURCE_TYPES)
        if prop == "AvailableCursorModes":
            return GLib.Variant("u", AVAILABLE_CURSOR_MODES)
        if prop == "version":
            return GLib.Variant("u", 5)
        return None

    def _call(self, _connection, _sender, _path, _iface, method, parameters,
              invocation) -> None:
        handlers = {
            "CreateSession": self._create_session,
            "SelectSources": self._select_sources,
            "Start": self._start,
        }
        handler = handlers.get(method)
        if handler is None:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)
            return
        try:
            handler(parameters, invocation)
        except Exception:  # noqa: BLE001
            LOGGER.exception("%s failed", method)
            answer(invocation, PORTAL_CANCELLED)

    # -- CreateSession ----------------------------------------------------

    def _create_session(self, parameters, invocation) -> None:
        handle, session_handle, app_id, _options = parameters.unpack()
        session = Session(self, session_handle, app_id)
        self._sessions[session_handle] = session
        registration = self._connection.register_object(
            session_handle, self._session_node.interfaces[0],
            lambda *args: self._session_call(session, args[-1]), None, None)
        self._registrations[session_handle] = registration
        LOGGER.info("screen sharing session %s for %r", session_handle,
                    app_id or "a host application")
        self._answer_request(handle, invocation, PORTAL_SUCCESS)

    def _session_call(self, session: Session, invocation) -> None:
        if invocation.get_method_name() == "Close":
            session.close(notify=False)
        invocation.return_value(None)

    # -- SelectSources ----------------------------------------------------

    def _select_sources(self, parameters, invocation) -> None:
        handle, session_handle, _app_id, options = parameters.unpack()
        session = self._sessions.get(session_handle)
        if session is None:
            answer(invocation, PORTAL_CANCELLED)
            return
        options = unpack(options)
        session.types = int(options.get("types") or AVAILABLE_SOURCE_TYPES)
        session.types &= AVAILABLE_SOURCE_TYPES
        if not session.types:
            session.types = AVAILABLE_SOURCE_TYPES
        session.multiple = bool(options.get("multiple"))
        session.cursor_modes = int(options.get("cursor_mode") or
                                   mutter.PORTAL_CURSOR_HIDDEN)
        session.persist_mode = int(options.get("persist_mode") or PERSIST_NONE)
        token = options.get("restore_token")
        if token:
            restored = self._tokens.take(session.app_id, str(token))
            if restored:
                # A restored session shares again without the picker. The old
                # token is spent; Start issues a fresh one.
                session.choice = restored
                session.restore_token = str(token)
                LOGGER.info("session %s restored a previous choice",
                            session_handle)
        self._answer_request(handle, invocation, PORTAL_SUCCESS)

    # -- Start ------------------------------------------------------------

    def _start(self, parameters, invocation) -> None:
        handle, session_handle, app_id, parent_window, _options = \
            parameters.unpack()
        session = self._sessions.get(session_handle)
        if session is None:
            answer(invocation, PORTAL_CANCELLED)
            return

        if session.choice is not None:
            # Restored: no picker, straight to the stream.
            self._begin(session, invocation, handle)
            return

        self._watch_request(handle, lambda: self._cancel_picker(session))
        options = {
            "app-id": GLib.Variant("s", app_id or ""),
            "parent-window": GLib.Variant("s", parent_window or ""),
            "types": GLib.Variant("u", session.types),
            "multiple": GLib.Variant("b", session.multiple),
            "cursor-modes": GLib.Variant("u", session.cursor_modes),
            "control": GLib.Variant("b", False),
        }
        self._connection.call(
            SHELL_NAME, SHELL_PATH, SHELL_IFACE, "Choose",
            GLib.Variant("(a{sv})", (options,)), GLib.VariantType("(ua{sv})"),
            Gio.DBusCallFlags.NONE, -1, None,
            lambda source, result, _data: self._chosen(
                session, invocation, handle, source, result), None)

    def _chosen(self, session, invocation, handle, source, result) -> None:
        try:
            reply = source.call_finish(result)
        except GLib.Error as error:
            # The Shell could not show the picker. Nothing is shared and the
            # application is told so, rather than left waiting.
            LOGGER.error("the Shell could not show the picker: %s",
                         error.message)
            self._answer_request(handle, invocation, PORTAL_CANCELLED)
            return
        response, results = reply.unpack()
        if response != PORTAL_SUCCESS:
            LOGGER.info("session %s was cancelled", session.handle)
            self._answer_request(handle, invocation, PORTAL_CANCELLED)
            return
        results = unpack(results)
        session.choice = {
            "windows": [int(window) for window in results.get("windows", [])],
            "monitors": [str(m) for m in results.get("monitors", [])],
            "cursor-mode": int(results.get("cursor-mode") or
                               mutter.PORTAL_CURSOR_HIDDEN),
            "hide-notifications": bool(results.get("hide-notifications")),
            "allow-control": bool(results.get("allow-control")),
        }
        self._begin(session, invocation, handle)

    def _begin(self, session, invocation, handle) -> None:
        choice = session.choice or {}
        windows = choice.get("windows") or []
        monitors = choice.get("monitors") or []
        if not windows and not monitors:
            self._answer_request(handle, invocation, PORTAL_CANCELLED)
            return
        cursor_mode = mutter.cursor_mode_for_portal(
            int(choice.get("cursor-mode") or mutter.PORTAL_CURSOR_HIDDEN))
        try:
            cast = mutter.ScreenCastSession(self._connection)
            session.cast = cast
            cast.on_closed = lambda: session.close()
            for connector in monitors:
                cast.record_monitor(connector, cursor_mode)
            for window_id in windows:
                cast.record_window(window_id, cursor_mode)
            for stream in cast.streams:
                stream.on_node = lambda _stream: self._maybe_ready(
                    session, invocation, handle)
            cast.start()
        except GLib.Error as error:
            LOGGER.error("Mutter would not start the stream: %s", error.message)
            if session.cast:
                session.cast.stop()
                session.cast = None
            self._answer_request(handle, invocation, PORTAL_CANCELLED)
            return

        # Mutter announces each stream's PipeWire node after Start, so the
        # application is answered once every node exists: a `streams` entry
        # with node 0 is one the application cannot connect to. The timeout is
        # the promise that the caller is answered either way.
        session.pending = (invocation, handle)
        session.timeout_id = GLib.timeout_add_seconds(
            NODE_TIMEOUT_SECONDS, lambda: self._nodes_timed_out(session))
        self._maybe_ready(session, invocation, handle)

    def _maybe_ready(self, session, invocation, handle) -> None:
        if session.pending is None or not session.cast or not session.cast.ready:
            return
        self._finish(session, invocation, handle, session.cast)

    def _nodes_timed_out(self, session) -> bool:
        session.timeout_id = 0
        if session.pending is None:
            return GLib.SOURCE_REMOVE
        invocation, handle = session.pending
        session.pending = None
        LOGGER.error("Mutter never announced a PipeWire node for %s",
                     session.handle)
        if session.cast:
            session.cast.stop()
            session.cast = None
        self._answer_request(handle, invocation, PORTAL_CANCELLED)
        self.forget(session)
        return GLib.SOURCE_REMOVE

    def _finish(self, session, invocation, handle, cast) -> None:
        session.pending = None
        if session.timeout_id:
            GLib.source_remove(session.timeout_id)
            session.timeout_id = 0
        choice = session.choice or {}
        results = {
            "streams": GLib.Variant("a(ua{sv})", [
                (stream.node_id, stream.portal_properties())
                for stream in cast.streams
            ]),
        }
        if session.persist_mode != PERSIST_NONE:
            if session.restore_token:
                self._tokens.drop(session.restore_token)
            token = self._tokens.issue(session.app_id, choice,
                                       session.persist_mode)
            if token:
                results["restore_token"] = GLib.Variant("s", token)
                results["persist_mode"] = GLib.Variant("u", session.persist_mode)

        # The Shell needs to know what is being shared and with whom, for the
        # violet edge and the Stop pill. It is told only what was chosen.
        self._tell_shell_started(session, choice)
        self._answer_request(handle, invocation, PORTAL_SUCCESS, results)

    # -- The Shell --------------------------------------------------------

    def _tell_shell_started(self, session, choice) -> None:
        info = {
            "app-id": GLib.Variant("s", session.app_id or ""),
            "windows": GLib.Variant("at", choice.get("windows") or []),
            "monitors": GLib.Variant("as", choice.get("monitors") or []),
            "hide-notifications": GLib.Variant(
                "b", bool(choice.get("hide-notifications"))),
        }
        self._call_shell("SessionStarted",
                         GLib.Variant("(sa{sv})", (session.handle, info)))

    def _cancel_picker(self, session) -> None:
        """The application gave up: take the picker off the screen."""
        LOGGER.info("the application closed its request for %s", session.handle)
        self._call_shell("Cancel", None)
        session.close()

    def _call_shell(self, method: str, parameters) -> None:
        self._connection.call(
            SHELL_NAME, SHELL_PATH, SHELL_IFACE, method, parameters, None,
            Gio.DBusCallFlags.NONE, -1, None, None, None)

    def _on_stop_requested(self, _connection, _sender, _path, _iface, _signal,
                           parameters, _data) -> None:
        (handle,) = parameters.unpack()
        session = self._sessions.get(handle)
        if session is None:
            return
        LOGGER.info("Stop was pressed for %s", handle)
        session.close()

    # -- Bookkeeping ------------------------------------------------------

    def forget(self, session: Session, notify_shell: bool = False) -> None:
        self._sessions.pop(session.handle, None)
        registration = self._registrations.pop(session.handle, 0)
        if registration:
            self._connection.unregister_object(registration)
        if notify_shell:
            self._call_shell("SessionClosed",
                             GLib.Variant("(s)", (session.handle,)))

    def emit_session_closed(self, handle: str) -> None:
        try:
            self._connection.emit_signal(
                None, handle, "org.freedesktop.impl.portal.Session", "Closed",
                None)
        except GLib.Error as error:
            LOGGER.debug("could not announce %s closed: %s", handle,
                         error.message)

    # A portal request can be closed by the application, not only answered.
    # Start's request is the one that matters: the picker is on screen while
    # it is open, and an application that gives up must not leave a modal card
    # asking about a session that no longer exists.
    def _watch_request(self, handle: str, on_close) -> None:
        registration = 0

        def call(*args):
            invocation = args[-1]
            if invocation.get_method_name() == "Close":
                self._drop_request(handle)
                on_close()
            invocation.return_value(None)

        try:
            registration = self._connection.register_object(
                handle, self._request_node.interfaces[0], call, None, None)
        except GLib.Error as error:
            LOGGER.debug("could not watch request %s: %s", handle,
                         error.message)
            return
        self._requests[handle] = registration

    def _drop_request(self, handle: str) -> None:
        registration = self._requests.pop(handle, 0)
        if registration:
            self._connection.unregister_object(registration)

    def _answer_request(self, handle, invocation, response, results=None):
        self._drop_request(handle)
        answer(invocation, response, results)


def answer(invocation, response: int, results=None) -> None:
    """Answer a portal method, once."""
    invocation.return_value(
        GLib.Variant("(ua{sv})", (response, results or {})))
