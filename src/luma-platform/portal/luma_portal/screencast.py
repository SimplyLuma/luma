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
- Nothing is asked and nothing starts while the session is locked. A Start
  that arrives then waits (lock.py) and goes on after unlock, in the normal
  session; pickers are shown one at a time, in the order they were asked
  for, so several requests become several questions in turn, never a stack.
  Mutter pauses the shares already running while it is locked (no frames
  leave it) and resumes the same streams after unlock, so an application
  never has to ask again because the screen locked.

Restoring a share: xdg-desktop-portal owns restore tokens (it keeps them in
its permission store, per application) and never shows them to a backend.
What it keeps for us is our ``restore_data``, ``("luma", 1, choice)``,
returned from Start and handed back in SelectSources when the application
presents its token. A choice that no longer applies (a screen unplugged, a
window closed) falls back to the picker rather than failing the share.
"""

from __future__ import annotations

import collections
import logging

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import mutter  # noqa: E402
from .lock import SessionLock  # noqa: E402

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

#: Mutter refuses new shares while the session is locked. The screen shield
#: announces the lock a moment after Mutter applies it, so a restored share
#: refused for that reason is tried again every second, after unlock, and only
#: falls back to the picker if Mutter still refuses after this many tries
#: with the session open.
INHIBITED_RETRIES = 30
INHIBITED_MESSAGE = "inhibited"

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


#: Our restore data's vendor and version, as xdg-desktop-portal keeps it.
RESTORE_VENDOR = "luma"
RESTORE_VERSION = 1


def restore_data(choice: dict) -> GLib.Variant:
    """The ``(suv)`` xdg-desktop-portal keeps for a persisted share: what was chosen."""
    data = {
        "monitors": GLib.Variant("as", [str(m) for m in choice.get("monitors") or []]),
        "windows": GLib.Variant("at", [int(w) for w in choice.get("windows") or []]),
        "cursor-mode": GLib.Variant("u", int(choice.get("cursor-mode") or mutter.PORTAL_CURSOR_HIDDEN)),
        "hide-notifications": GLib.Variant("b", bool(choice.get("hide-notifications"))),
    }
    return GLib.Variant("(suv)", (RESTORE_VENDOR, RESTORE_VERSION, GLib.Variant("a{sv}", data)))


def restored_choice(value) -> dict | None:
    """The choice in restore data we wrote, or None for anything else (another backend's, an old version)."""
    if isinstance(value, GLib.Variant):
        value = value.unpack()
    try:
        vendor, version, data = value
    except (TypeError, ValueError):
        return None
    if isinstance(data, GLib.Variant):
        data = data.unpack()
    if vendor != RESTORE_VENDOR or version != RESTORE_VERSION or not isinstance(data, dict):
        return None
    choice = {
        "monitors": [str(m) for m in data.get("monitors") or []],
        "windows": [int(w) for w in data.get("windows") or []],
        "cursor-mode": int(data.get("cursor-mode") or mutter.PORTAL_CURSOR_HIDDEN),
        "hide-notifications": bool(data.get("hide-notifications")),
        "allow-control": False,
    }
    if not choice["monitors"] and not choice["windows"]:
        return None
    return choice


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
        #: The choice came from restore data, not from the picker this time.
        self.restored = False
        self.parent_window = ""
        self.choice: dict | None = None
        self.cast: mutter.ScreenCastSession | None = None
        self.closed = False
        #: (invocation, request handle) for a Start that is still being
        #: answered, or None.
        self.pending = None
        self.timeout_id = 0
        #: Mutter refused to start this restored share as locked, this often.
        self.inhibited_tries = 0

    def close(self, notify: bool = True, why: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        LOGGER.info("screen sharing session %s closed%s", self.handle, f" ({why})" if why else "")
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
            self.timeout_id = 0
        if self.pending is not None:
            invocation, _handle = self.pending
            self.pending = None
            answer(invocation, PORTAL_CANCELLED)
        self.backend.withdraw(self)
        if self.cast:
            self.cast.stop()
            self.cast = None
        self.backend.forget(self, notify_shell=True)
        if notify:
            self.backend.emit_session_closed(self.handle)


class ScreenCastPortal:
    """org.freedesktop.impl.portal.ScreenCast."""

    def __init__(self, connection, object_path: str, lock: SessionLock | None = None) -> None:
        self._connection = connection
        self._object_path = object_path
        self._lock = lock or SessionLock(connection)
        #: Starts waiting for their turn: (session, invocation, request handle).
        self._queue: collections.deque = collections.deque()
        #: The session whose picker is on screen, or None.
        self._asking: Session | None = None
        self._node = Gio.DBusNodeInfo.new_for_xml(SCREEN_CAST_INTERFACE)
        self._session_node = Gio.DBusNodeInfo.new_for_xml(SESSION_INTERFACE)
        self._request_node = Gio.DBusNodeInfo.new_for_xml(REQUEST_INTERFACE)
        self._sessions: dict[str, Session] = {}
        self._registrations: dict[str, int] = {}
        self._requests: dict[str, int] = {}
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
            # xdg-desktop-portal closes the session when the application asks
            # and when the application leaves the bus (exit, crash).
            session.close(notify=False, why="closed by xdg-desktop-portal")
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
        restored = restored_choice(options.get("restore_data"))
        if restored is not None:
            # The application presented a token xdg-desktop-portal knew: share
            # the same thing again without the picker.
            session.choice = restored
            session.restored = True
            LOGGER.info("session %s restored a previous choice", session_handle)
        self._answer_request(handle, invocation, PORTAL_SUCCESS)

    # -- Start ------------------------------------------------------------

    def _start(self, parameters, invocation) -> None:
        handle, session_handle, _app_id, parent_window, _options = \
            parameters.unpack()
        session = self._sessions.get(session_handle)
        if session is None:
            answer(invocation, PORTAL_CANCELLED)
            return

        session.parent_window = parent_window or ""
        self._watch_request(handle, lambda: self._withdraw_request(session))
        self._queue.append((session, invocation, handle))
        self._pump()

    def _pump(self) -> None:
        """Start what waits, in order: nothing while locked, one picker at a time."""
        while self._queue:
            if self._lock.locked:
                LOGGER.info("the session is locked; %d request(s) wait for unlock",
                            len(self._queue))
                self._lock.when_unlocked(self._pump)
                return
            session, invocation, handle = self._queue[0]
            if session.choice is None and self._asking is not None:
                return  # the next picker comes when this one is answered
            self._queue.popleft()
            if session.choice is not None:
                # Restored: no picker, straight to the stream.
                self._begin(session, invocation, handle)
            else:
                self._pick(session, invocation, handle)

    def withdraw(self, session) -> None:
        """A closed session's Start, still waiting, is answered and forgotten."""
        for entry in [e for e in self._queue if e[0] is session]:
            self._queue.remove(entry)
            self._answer_request(entry[2], entry[1], PORTAL_CANCELLED)
        if self._asking is session:
            self._call_shell("Cancel", None)

    def _pick(self, session, invocation, handle) -> None:
        self._asking = session
        options = {
            "app-id": GLib.Variant("s", session.app_id or ""),
            "parent-window": GLib.Variant("s", session.parent_window),
            "types": GLib.Variant("u", session.types),
            "multiple": GLib.Variant("b", session.multiple),
            "cursor-modes": GLib.Variant("u", session.cursor_modes),
            "control": GLib.Variant("b", False),
        }
        self._connection.call(
            SHELL_NAME, SHELL_PATH, SHELL_IFACE, "Choose",
            GLib.Variant("(a{sv})", (options,)), GLib.VariantType("(ua{sv})"),
            # A person is choosing: no D-Bus timeout (the default 25 s gave up
            # on anyone who took longer). Cancel and Close still end it.
            Gio.DBusCallFlags.NONE, GLib.MAXINT, None,
            lambda source, result, _data: self._chosen(
                session, invocation, handle, source, result), None)

    def _chosen(self, session, invocation, handle, source, result) -> None:
        if self._asking is session:
            self._asking = None
        # The next waiting request goes on once this one has been answered.
        GLib.idle_add(lambda: (self._pump(), GLib.SOURCE_REMOVE)[1])
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
            cast.on_closed = lambda: session.close(why="Mutter ended it")
            for connector in monitors:
                cast.record_monitor(connector, cursor_mode)
            for window_id in windows:
                cast.record_window(window_id, cursor_mode)
            for stream in cast.streams:
                stream.on_node = lambda _stream: self._maybe_ready(
                    session, invocation, handle)
            cast.start()
        except GLib.Error as error:
            if session.cast:
                session.cast.stop()
                session.cast = None
            if session.restored and INHIBITED_MESSAGE in error.message and \
                    session.inhibited_tries < INHIBITED_RETRIES:
                self._wait_for_unlock(session, invocation, handle)
                return
            if session.restored:
                self._restore_failed(session, invocation, handle, error.message)
                return
            LOGGER.error("Mutter would not start the stream: %s", error.message)
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

    def _wait_for_unlock(self, session, invocation, handle) -> None:
        """Mutter refused a restored share because the session is locked: it starts after unlock."""
        session.inhibited_tries += 1
        LOGGER.info("session %s waits for unlock to restore its share", session.handle)

        def retry():
            if not session.closed:
                self._queue.appendleft((session, invocation, handle))
                self._lock.refresh()
                self._pump()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(1, retry)

    def _restore_failed(self, session, invocation, handle, why: str) -> None:
        """A restored choice no longer applies (a screen unplugged, a window closed): ask afresh."""
        LOGGER.info("session %s: the restored choice no longer applies (%s); showing the picker",
                    session.handle, why)
        session.choice = None
        session.restored = False
        self._queue.appendleft((session, invocation, handle))
        self._pump()

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
            # xdg-desktop-portal turns this into the application's token.
            results["restore_data"] = restore_data(choice)
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
        if session.cast is not None and getattr(session.cast, "path", None):
            # The Shell drops the share's ring and pill when this Mutter
            # session ends, whatever happens to this process.
            info["mutter-session"] = GLib.Variant("o", session.cast.path)
        self._call_shell("SessionStarted",
                         GLib.Variant("(sa{sv})", (session.handle, info)))

    def _withdraw_request(self, session) -> None:
        """The application gave up: its picker comes off the screen, or never shows."""
        LOGGER.info("the application closed its request for %s", session.handle)
        session.close(why="the request was closed")

    def shutdown(self) -> None:
        """The backend is stopping: every share ends, and the Shell is told so."""
        for session in list(self._sessions.values()):
            session.close(why="the backend is stopping")
        self._connection.flush_sync(None)

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
        session.close(why="Stop")

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
