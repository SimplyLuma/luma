# SPDX-License-Identifier: Apache-2.0
"""Background agents: keep an app working when its windows are closed.

This module never imports GTK. An agent is the small, windowless half of an
app that Luma starts, wakes and limits; the whole point is that it does not
carry the interface with it. See docs/developer/kit/background-agents.md.
"""

from __future__ import annotations

import os
import re
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

__all__ = [
    "BADGE_DOT",
    "BADGE_VALUE",
    "Agent",
    "AgentValue",
    "BackgroundResult",
    "LiveExtensionBinding",
    "Wake",
    "from_agent",
    "is_sandboxed",
    "request_background",
    "run_agent",
]

SERVICE_NAME = "org.projectluma.Background1"
SERVICE_PATH = "/org/projectluma/Background1"
SERVICE_INTERFACE = "org.projectluma.Background1"
AGENT_INTERFACE = "org.projectluma.BackgroundAgent1"
AGENT_PATH = "/org/projectluma/BackgroundAgent1"
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
AGENT_SWITCH = "--agent"

# The standard value for an unread badge on the app's dock icon: an unsigned
# count of things waiting for the person, or BADGE_DOT for something new
# without a number. Declare it in `publishes` for the dock to read it.
BADGE_VALUE = "badge"
BADGE_DOT = "dot"

WAKE_REASONS = frozenset({"login", "network", "resume", "schedule", "request"})
_VALUE_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_SCHEDULE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_APP_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\.[A-Za-z_][A-Za-z0-9_-]*$")

AGENT_XML = """
<node>
  <interface name="org.projectluma.BackgroundAgent1">
    <method name="Wake">
      <arg name="reason" type="s" direction="in"/>
      <arg name="details" type="a{sv}" direction="in"/>
    </method>
    <property name="AppId" type="s" access="read"/>
    <property name="Values" type="a{sv}" access="read"/>
  </interface>
</node>
"""


def is_sandboxed() -> bool:
    return os.path.exists("/.flatpak-info")


# -- Values ------------------------------------------------------------------


def _variant(value: Any) -> GLib.Variant:
    """Publishable values: plain data only, so any toolkit can read them."""

    if isinstance(value, GLib.Variant):
        return value
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        if -(2**63) <= value < 2**63:
            return GLib.Variant("x", value)
        raise ValueError("integers must fit in 64 bits")
    if isinstance(value, float):
        return GLib.Variant("d", value)
    if isinstance(value, str):
        return GLib.Variant("s", value)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return GLib.Variant("as", list(value))
    if isinstance(value, Mapping):
        return GLib.Variant("a{sv}", {str(key): _variant(item) for key, item in value.items()})
    raise TypeError(f"cannot publish a value of type {type(value).__name__}")


def _unpack(value: Any) -> Any:
    return value.unpack() if isinstance(value, GLib.Variant) else value


# -- Wakes -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Wake:
    reason: str
    schedule: str = ""
    missed: bool = False


@dataclass(frozen=True, slots=True)
class BackgroundResult:
    allowed: bool
    autostart: bool
    response: int


class _ConnectionShim:
    """What the Semantic Broker's publisher needs from a Gio.Application."""

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self._connection = connection

    def get_dbus_connection(self) -> Gio.DBusConnection:
        return self._connection


class Agent:
    """Subclass this, set `app_id` and `agent_id`, and override the hooks."""

    app_id: str = ""
    agent_id: str = ""

    def __init__(self, *, connection: Gio.DBusConnection | None = None) -> None:
        if not _APP_ID.fullmatch(self.app_id or ""):
            raise ValueError("Agent.app_id must be a valid application ID")
        if not (self.agent_id or "").startswith(self.app_id + ".") or len(self.agent_id) <= len(self.app_id) + 1:
            raise ValueError("Agent.agent_id must start with app_id followed by a dot")
        self._connection = connection
        self._values: dict[str, GLib.Variant] = {}
        self._changed: set[str] = set()
        self._invalidated: set[str] = set()
        self._flush_source = 0
        self._loop: GLib.MainLoop | None = None
        self._queue: deque[Wake] = deque()
        self._waking = False
        self._started = False
        self._registration = 0
        self._name_id = 0
        self._exit_status = 0
        self._bindings: list[LiveExtensionBinding] = []
        self._notification_ids: dict[int, str] = {}
        self.managed = os.environ.get("LUMA_BACKGROUND_MANAGED") == "1"

    # -- Hooks to override ---------------------------------------------------

    def on_start(self) -> None:
        """Called once the agent owns its name, before the first wake."""

    def on_wake(self, wake: Wake) -> None:
        """Called for every wake, one at a time."""

    def on_stop(self) -> None:
        """Called when the agent is asked to stop. Keep it short."""

    # -- Values --------------------------------------------------------------

    @property
    def values(self) -> Mapping[str, Any]:
        return {name: value.unpack() for name, value in self._values.items()}

    def publish(self, name: str, value: Any) -> None:
        if not _VALUE_NAME.fullmatch(name or ""):
            raise ValueError(f"invalid value name: {name!r}")
        variant = _variant(value)
        current = self._values.get(name)
        if current is not None and current.equal(variant):
            return
        self._values[name] = variant
        self._changed.add(name)
        self._invalidated.discard(name)
        self._schedule_flush()

    def set_badge(self, badge: int | str | None) -> None:
        """Show what waits for the person on the app's dock icon.

        A count (0 shows nothing), BADGE_DOT for something new without a
        number, or None to stop publishing a badge at all. Never use it for
        running, syncing or other background work.
        """

        if badge is None:
            self.unpublish(BADGE_VALUE)
        elif badge == BADGE_DOT:
            self.publish(BADGE_VALUE, BADGE_DOT)
        elif isinstance(badge, int) and not isinstance(badge, bool) and badge >= 0:
            self.publish(BADGE_VALUE, GLib.Variant("u", min(badge, 2**32 - 1)))
        else:
            raise ValueError(f"a badge is a count of at least 0, {BADGE_DOT!r} or None, not {badge!r}")

    def unpublish(self, name: str) -> None:
        if self._values.pop(name, None) is not None:
            self._invalidated.add(name)
            self._changed.discard(name)
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        if not self._flush_source:
            self._flush_source = GLib.idle_add(self._flush)

    def _flush(self) -> bool:
        self._flush_source = 0
        self._changed.clear()
        self._invalidated.clear()
        if self._connection is not None and self._registration:
            self._connection.emit_signal(
                None, AGENT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (AGENT_INTERFACE,
                                            {"Values": GLib.Variant("a{sv}", dict(self._values))}, [])))
        for binding in list(self._bindings):
            binding.refresh()
        return GLib.SOURCE_REMOVE

    # -- Service calls -------------------------------------------------------

    def _service(self, method: str, parameters: GLib.Variant) -> None:
        if self._connection is None:
            raise RuntimeError("the agent is not running")
        try:
            self._connection.call_sync(SERVICE_NAME, SERVICE_PATH, SERVICE_INTERFACE, method,
                                       parameters, None, Gio.DBusCallFlags.NONE, 10_000, None)
        except GLib.Error as error:
            Gio.DBusError.strip_remote_error(error)
            raise RuntimeError(error.message) from error

    def schedule(self, name: str, *, at: datetime | None = None, every: timedelta | None = None) -> None:
        if not _SCHEDULE_NAME.fullmatch(name or "") or name == "interval":
            raise ValueError(f"invalid schedule name: {name!r}")
        if (at is None) == (every is None):
            raise ValueError("pass exactly one of at and every")
        options: dict[str, GLib.Variant] = {}
        if at is not None:
            if at.tzinfo is None:
                raise ValueError("at must be timezone-aware")
            options["at"] = GLib.Variant("x", int(at.timestamp()))
        else:
            seconds = int(every.total_seconds())  # type: ignore[union-attr]
            if seconds < 300:
                raise ValueError("a repeating schedule is at least five minutes")
            options["every"] = GLib.Variant("u", seconds)
        self._service("Schedule", GLib.Variant("(sa{sv})", (name, options)))

    def unschedule(self, name: str) -> None:
        self._service("Unschedule", GLib.Variant("(s)", (name,)))

    # -- Notifications and the app -------------------------------------------

    def notify(self, notification_id: str, title: str, body: str = "", *,
               priority: str = "normal", open_app: bool = True) -> None:
        if self._connection is None:
            raise RuntimeError("the agent is not running")
        if priority not in {"low", "normal", "high", "urgent"}:
            raise ValueError("priority must be low, normal, high or urgent")
        if is_sandboxed():
            notification = {
                "title": GLib.Variant("s", title),
                "body": GLib.Variant("s", body),
                "priority": GLib.Variant("s", priority),
            }
            if open_app:
                notification["default-action"] = GLib.Variant("s", "app.activate")
            self._connection.call(PORTAL_NAME, PORTAL_PATH, "org.freedesktop.portal.Notification",
                                  "AddNotification", GLib.Variant("(sa{sv})", (notification_id, notification)),
                                  None, Gio.DBusCallFlags.NONE, 10_000, None, None)
            return
        urgency = {"low": 0, "normal": 1, "high": 1, "urgent": 2}[priority]
        hints = {"desktop-entry": GLib.Variant("s", self.app_id), "urgency": GLib.Variant("y", urgency)}

        def sent(connection, result) -> None:
            try:
                (identifier,) = connection.call_finish(result).unpack()
            except GLib.Error:
                return
            if open_app:
                self._notification_ids[identifier] = notification_id

        self._connection.call(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", (self.app_id, 0, self.app_id, title, body,
                                            ["default", title] if open_app else [], hints, -1)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 10_000, None, sent)

    def _notification_action(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        identifier, action = parameters.unpack()
        if self._notification_ids.pop(identifier, None) is not None and action == "default":
            self.open_app()

    def open_app(self) -> None:
        if is_sandboxed() or self._connection is None:
            path = "/" + self.app_id.replace(".", "/").replace("-", "_")
            if self._connection is not None:
                self._connection.call(self.app_id, path, "org.freedesktop.Application", "Activate",
                                      GLib.Variant("(a{sv})", ({},)), None, Gio.DBusCallFlags.NONE,
                                      10_000, None, None)
            return
        info = Gio.DesktopAppInfo.new(self.app_id + ".desktop")
        if info is not None:
            info.launch([], None)

    # -- Lifecycle -----------------------------------------------------------

    def quit(self, status: int = 0) -> None:
        """End cleanly. A clean exit is not a crash; the next wake starts the agent again."""
        self._exit_status = status
        if self._loop is not None:
            GLib.idle_add(self._loop.quit)

    def _handle_call(self, connection, sender, _path, _interface, method, parameters, invocation) -> None:
        if method != "Wake":
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        if not self._from_service(sender):
            invocation.return_dbus_error("org.projectluma.BackgroundAgent1.Error.NotAuthorized",
                                         "wakes are delivered by luma-background")
            return
        reason, details = parameters.unpack()
        if reason not in WAKE_REASONS:
            invocation.return_dbus_error("org.projectluma.BackgroundAgent1.Error.InvalidArgument",
                                         "unknown wake reason")
            return
        invocation.return_value(None)
        self._enqueue(Wake(reason, str(details.get("schedule", "")), bool(details.get("missed", False))))

    def _from_service(self, sender: str) -> bool:
        try:
            owner = self._connection.call_sync(  # type: ignore[union-attr]
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner",
                GLib.Variant("(s)", (SERVICE_NAME,)), GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE, 2_000, None).unpack()[0]
        except GLib.Error:
            return False
        return owner == sender

    def _get_property(self, _connection, _sender, _path, _interface, name):
        if name == "AppId":
            return GLib.Variant("s", self.app_id)
        if name == "Values":
            return GLib.Variant("a{sv}", dict(self._values))
        return None

    def _enqueue(self, wake: Wake) -> None:
        self._queue.append(wake)
        if not self._waking:
            GLib.idle_add(self._drain)

    def _drain(self) -> bool:
        if self._waking:
            return GLib.SOURCE_REMOVE
        self._waking = True
        try:
            while self._queue:
                wake = self._queue.popleft()
                try:
                    self.on_wake(wake)
                except Exception as error:  # noqa: BLE001 - one failed wake must not end the agent
                    print(f"{self.agent_id}: the {wake.reason} wake failed: {error!r}", file=sys.stderr)
        finally:
            self._waking = False
        return GLib.SOURCE_REMOVE

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        """Register the agent interface and ask for the bus name.

        `on_start` runs once the name is owned. `run()` calls this; call it
        yourself only when the agent shares a main loop you already run.
        """

        if self._registration:
            return
        if self._connection is None:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Bindings to this agent's own name, made inside this process, read
        # its values directly for as long as it runs.
        _LOCAL_AGENTS[self.agent_id] = self
        info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
        self._registration = self._connection.register_object(
            AGENT_PATH, info, self._handle_call, self._get_property, None)
        self._connection.signal_subscribe(
            "org.freedesktop.Notifications", "org.freedesktop.Notifications", "ActionInvoked",
            "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE, self._notification_action)
        self._name_id = Gio.bus_own_name_on_connection(
            self._connection, self.agent_id, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
            self._name_acquired, self._name_lost)

    def _name_acquired(self, _connection, _name) -> None:
        if self._started:
            return
        self._started = True
        if not self.managed:
            print(f"{self.agent_id}: running unmanaged; luma-background will not wake it",
                  file=sys.stderr)
        try:
            self.on_start()
        except Exception as error:  # noqa: BLE001
            print(f"{self.agent_id}: on_start failed: {error!r}", file=sys.stderr)
            self.quit(1)
            return
        self._schedule_flush()
        if self._queue:
            GLib.idle_add(self._drain)

    def _name_lost(self, _connection, _name) -> None:
        if not self._started:
            print(f"{self.agent_id}: the agent's name is already owned", file=sys.stderr)
            self._exit_status = 1
        if self._loop is not None:
            self._loop.quit()

    def stop(self) -> None:
        """Undo start(): run on_stop, withdraw bindings, release the name."""

        if self._started:
            self._started = False
            try:
                self.on_stop()
            except Exception as error:  # noqa: BLE001
                print(f"{self.agent_id}: on_stop failed: {error!r}", file=sys.stderr)
        for binding in list(self._bindings):
            binding.close()
        if _LOCAL_AGENTS.get(self.agent_id) is self:
            del _LOCAL_AGENTS[self.agent_id]
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        if self._registration and self._connection is not None:
            self._connection.unregister_object(self._registration)
            self._registration = 0
            self._connection.flush_sync(None)

    def run(self, argv: list[str] | None = None) -> int:
        import signal

        self._loop = GLib.MainLoop()
        try:
            self.start()
        except GLib.Error as error:
            print(f"{self.agent_id}: cannot join the session bus: {error.message}", file=sys.stderr)
            return 1

        def terminate() -> bool:
            self._loop.quit()  # type: ignore[union-attr]
            return GLib.SOURCE_CONTINUE  # removed below, once the loop has ended

        sources = [GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, terminate)
                   for signum in (signal.SIGTERM, signal.SIGINT)]
        try:
            self._loop.run()
        finally:
            for source in sources:
                GLib.source_remove(source)
            self.stop()
        return self._exit_status


def run_agent(agent_class: type[Agent], argv: list[str] | None = None) -> int | None:
    """Run the agent if `--agent` was passed; otherwise return None."""

    arguments = list(sys.argv if argv is None else argv)
    if AGENT_SWITCH not in arguments[1:]:
        return None
    return agent_class().run(arguments)


# -- Requesting --------------------------------------------------------------


def request_background(app_id: str, *, reason: str, autostart: bool = True,
                       commandline: list[str] | None = None, parent_window: str = "",
                       callback: Callable[[BackgroundResult], None] | None = None,
                       connection: Gio.DBusConnection | None = None) -> None:
    """Ask for background activity through the portal (sandboxed) or the service."""

    if not _APP_ID.fullmatch(app_id or ""):
        raise ValueError("invalid application ID")
    if len(reason) > 256:
        raise ValueError("reason is at most 256 characters")
    callback = callback or (lambda result: None)
    connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    if not is_sandboxed():
        def answered(bus, result) -> None:
            try:
                response, results = bus.call_finish(result).unpack()
            except GLib.Error:
                callback(BackgroundResult(False, False, 2))
                return
            callback(BackgroundResult(bool(results.get("background")), bool(results.get("autostart")), response))

        connection.call(SERVICE_NAME, SERVICE_PATH, SERVICE_INTERFACE, "RequestBackground",
                        GLib.Variant("(sa{sv})", (parent_window, {
                            "reason": GLib.Variant("s", reason),
                            "autostart": GLib.Variant("b", autostart),
                        })),
                        GLib.VariantType.new("(ua{sv})"), Gio.DBusCallFlags.NONE, GLib.MAXINT, None,
                        answered)
        return

    token = f"luma_background_{os.getpid()}_{GLib.random_int_range(0, 1 << 30)}"
    sender = connection.get_unique_name().lstrip(":").replace(".", "_")
    handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    if commandline is None:
        commandline = [os.path.basename(sys.argv[0]) or app_id, AGENT_SWITCH]
    subscription = 0

    def responded(_bus, _sender, _path, _interface, _signal, parameters) -> None:
        connection.signal_unsubscribe(subscription)
        response, results = parameters.unpack()
        callback(BackgroundResult(bool(results.get("background")) and response == 0,
                                  bool(results.get("autostart")), response))

    subscription = connection.signal_subscribe(PORTAL_NAME, "org.freedesktop.portal.Request",
                                               "Response", handle, None,
                                               Gio.DBusSignalFlags.NONE, responded)
    connection.call(PORTAL_NAME, PORTAL_PATH, "org.freedesktop.portal.Background", "RequestBackground",
                    GLib.Variant("(sa{sv})", (parent_window, {
                        "handle_token": GLib.Variant("s", token),
                        "reason": GLib.Variant("s", reason),
                        "autostart": GLib.Variant("b", autostart),
                        "commandline": GLib.Variant("as", commandline),
                    })),
                    GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, -1, None, None)


# -- Bindings ----------------------------------------------------------------

_LOCAL_AGENTS: dict[str, Agent] = {}


class AgentValue:
    """A value an agent publishes, followed as it changes."""

    def __init__(self, bus_name: str, name: str, *, connection: Gio.DBusConnection | None = None) -> None:
        if not _VALUE_NAME.fullmatch(name or ""):
            raise ValueError(f"invalid value name: {name!r}")
        self.bus_name = bus_name
        self.name = name
        self._connection = connection
        self._remote: dict[str, Any] = {}
        self._listeners: list[Callable[["AgentValue"], None]] = []
        self._subscriptions: list[int] = []
        self._watch = 0

    @property
    def local_agent(self) -> Agent | None:
        # A binding given its own connection always reads over the bus.
        if self._connection is not None:
            return None
        return _LOCAL_AGENTS.get(self.bus_name)

    @property
    def present(self) -> bool:
        agent = self.local_agent
        if agent is not None:
            return self.name in agent.values
        return self.name in self._remote

    @property
    def value(self) -> Any:
        agent = self.local_agent
        if agent is not None:
            return agent.values.get(self.name)
        return self._remote.get(self.name)

    def connect(self, listener: Callable[["AgentValue"], None]) -> None:
        self._listeners.append(listener)
        if self.local_agent is None and not self._subscriptions:
            self._follow()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self)

    def _follow(self) -> None:
        connection = self._connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._connection = connection
        self._subscriptions.append(connection.signal_subscribe(
            self.bus_name, "org.freedesktop.DBus.Properties", "PropertiesChanged", AGENT_PATH,
            AGENT_INTERFACE, Gio.DBusSignalFlags.NONE, self._properties_changed))
        # Never auto-start: a binding shows what a running agent says, and a
        # stopped agent means there is nothing to show.
        self._watch = Gio.bus_watch_name_on_connection(
            connection, self.bus_name, Gio.BusNameWatcherFlags.NONE, self._appeared, self._vanished)

    def _appeared(self, connection, _name, _owner) -> None:
        def got(bus, result) -> None:
            try:
                (value,) = bus.call_finish(result).unpack()
            except GLib.Error:
                return
            self._remote = dict(value) if isinstance(value, dict) else {}
            self._notify()

        connection.call(self.bus_name, AGENT_PATH, "org.freedesktop.DBus.Properties", "Get",
                        GLib.Variant("(ss)", (AGENT_INTERFACE, "Values")), GLib.VariantType.new("(v)"),
                        Gio.DBusCallFlags.NO_AUTO_START, 5_000, None, got)

    def _vanished(self, _connection, _name) -> None:
        if self._remote:
            self._remote = {}
            self._notify()

    def _properties_changed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        _iface, changed, _invalidated = parameters.unpack()
        if "Values" in changed:
            self._remote = dict(changed["Values"])
            self._notify()

    def close(self) -> None:
        if self._connection is not None:
            for subscription in self._subscriptions:
                self._connection.signal_unsubscribe(subscription)
        self._subscriptions.clear()
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        self._listeners.clear()


def from_agent(bus_name: str, name: str) -> AgentValue:
    """Bind to a value published by the agent that owns `bus_name`."""
    return AgentValue(bus_name, name)


Field = str | AgentValue | Callable[[Mapping[str, Any]], Any] | None


class LiveExtensionBinding:
    """A Live Extension kept published from agent values, withdrawn when they go."""

    def __init__(self, owner: Agent | Gio.Application, *, extension_id: str, category: str,
                 title: Field, subtitle: Field = None, progress: Field = None,
                 privacy: str = "private", expires_in: timedelta = timedelta(minutes=30),
                 visible: Callable[[Mapping[str, Any]], bool] | None = None,
                 actions: list[dict] | None = None, app_id: str | None = None) -> None:
        if isinstance(owner, Agent):
            app_id = owner.app_id
            owner._bindings.append(self)
            connection = owner._connection
        else:
            app_id = app_id or owner.get_application_id()
            connection = owner.get_dbus_connection()
        if connection is None:
            raise RuntimeError("the owner must be running on the session bus")
        if expires_in < timedelta(minutes=1):
            raise ValueError("expires_in must be at least a minute")
        self.owner = owner
        self.app_id = app_id
        self.extension_id = extension_id
        self.category = category
        self.fields = {"title": title, "subtitle": subtitle, "progress": progress}
        self.privacy = privacy
        self.expires_in = expires_in
        self.visible = visible
        self.actions = list(actions or [])
        self._bindings = [value for value in self.fields.values() if isinstance(value, AgentValue)]
        self._refresh_source = 0
        for binding in self._bindings:
            binding.connect(lambda _binding: self.refresh())
        try:
            from luma_semantic_broker.client import LiveExtensionPublisher
        except ImportError:  # A platform without the broker shows nothing, quietly.
            self.publisher = None
            return
        self.publisher = LiveExtensionPublisher(_ConnectionShim(connection), app_id, extension_id,
                                                self.payload)

    def _values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for binding in self._bindings:
            if binding.present:
                values[binding.name] = _unpack(binding.value)
        if isinstance(self.owner, Agent):
            values = {**self.owner.values, **values}
        return values

    def _resolve(self, field: Field, values: Mapping[str, Any]) -> Any:
        if isinstance(field, AgentValue):
            return _unpack(field.value) if field.present else None
        if callable(field):
            try:
                return field(values)
            except (KeyError, TypeError, ValueError):
                return None
        return field

    def payload(self) -> dict[str, Any] | None:
        values = self._values()
        if self.visible is not None:
            try:
                if not self.visible(values):
                    return None
            except (KeyError, TypeError, ValueError):
                return None
        elif any(not binding.present for binding in self._bindings):
            return None
        title = self._resolve(self.fields["title"], values)
        if not isinstance(title, str) or not title.strip():
            return None
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "schema_version": "0.1",
            "id": self.extension_id,
            "app_id": self.app_id,
            "category": self.category,
            "title": title.strip()[:120],
            "privacy": self.privacy,
            "progress": -1.0,
            "actions": list(self.actions),
            "expires_at": (now + self.expires_in).isoformat(),
        }
        subtitle = self._resolve(self.fields["subtitle"], values)
        if isinstance(subtitle, str) and subtitle.strip():
            payload["subtitle"] = subtitle.strip()[:240]
        progress = self._resolve(self.fields["progress"], values)
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            payload["progress"] = max(0.0, min(1.0, float(progress)))
        return payload

    def refresh(self) -> None:
        if self.publisher is not None:
            self.publisher.sync()
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
        # Republish before the expiry runs out, while there is something to show.
        seconds = max(30, int(self.expires_in.total_seconds() * 0.6))
        self._refresh_source = GLib.timeout_add_seconds(seconds, self._periodic)

    def _periodic(self) -> bool:
        self._refresh_source = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def close(self) -> None:
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        if self.publisher is not None:
            self.publisher.close()
        for binding in self._bindings:
            binding.close()
        if isinstance(self.owner, Agent) and self in self.owner._bindings:
            self.owner._bindings.remove(self)
