# SPDX-License-Identifier: Apache-2.0
"""The Luma background agent contract for Charlie (ADR-033, Background1).

Contract (docs/developer/kit/background-agents.md, interface version 1):

* The agent owns ``<app id>.Agent`` with ``DO_NOT_QUEUE``; a second start finds
  the name taken and exits.
* It exports ``/org/projectluma/BackgroundAgent1`` with
  ``org.projectluma.BackgroundAgent1``: ``Wake(s reason, a{sv} details)``,
  accepted only from the current owner of ``org.projectluma.Background1``
  (reasons ``login``, ``network``, ``resume``, ``schedule``, ``request``;
  details may carry ``schedule`` s and ``missed`` b), and the properties
  ``AppId`` (s) and ``Values`` (a{sv}), announced with ``PropertiesChanged``.
* ``luma-background`` starts allowed agents in units it generates itself
  (``LUMA_BACKGROUND_MANAGED=1``) and delivers their wakes. Apps ship no unit
  and no D-Bus activation file for the agent.

``luma_appkit.background`` owns this contract. Charlie runs its agent through
that module when it is installed and can be loaded without importing
``luma_appkit/__init__`` (which loads GTK); otherwise the equivalent below is
used, with the identical bus contract. :class:`Notifier` (lock-screen privacy)
and :class:`EnvironmentWakes` (the unmanaged fallback for network, resume and
unlock) are Charlie's in either case. Nothing here imports a toolkit.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import importlib.util
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

LOG = logging.getLogger("luma.background")

SERVICE_NAME = "org.projectluma.Background1"
SERVICE_PATH = "/org/projectluma/Background1"
SERVICE_INTERFACE = "org.projectluma.Background1"
AGENT_INTERFACE = "org.projectluma.BackgroundAgent1"
AGENT_PATH = "/org/projectluma/BackgroundAgent1"
AGENT_SWITCH = "--agent"

WAKE_REASONS = frozenset({"login", "network", "resume", "schedule", "request"})
_VALUE_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
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


def is_managed() -> bool:
    """Started by luma-background in its generated unit."""
    return os.environ.get("LUMA_BACKGROUND_MANAGED") == "1"


def name_has_owner(connection: Gio.DBusConnection, name: str) -> bool:
    try:
        result = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                      "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(b)"),
                                      Gio.DBusCallFlags.NONE, 2000, None)
        return bool(result.unpack()[0])
    except GLib.Error:
        return False


def service_installed(connection: Gio.DBusConnection) -> bool:
    """Whether luma-background runs in this session or can be activated in it."""
    if name_has_owner(connection, SERVICE_NAME):
        return True
    try:
        result = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                      "ListActivatableNames", None, GLib.VariantType("(as)"),
                                      Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return False
    return SERVICE_NAME in result.unpack()[0]


def service_installed_async(connection: Gio.DBusConnection, callback: Callable[[bool], None]) -> None:
    """:func:`service_installed` without blocking the caller's main loop."""

    def activatable(bus, result) -> None:
        try:
            names = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            names = []
        callback(SERVICE_NAME in names)

    def owned(bus, result) -> None:
        try:
            has_owner = bool(bus.call_finish(result).unpack()[0])
        except GLib.Error:
            has_owner = False
        if has_owner:
            callback(True)
            return
        bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListActivatableNames",
                 None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 5000, None, activatable)

    connection.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                    GLib.Variant("(s)", (SERVICE_NAME,)), GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 5000,
                    None, owned)


def _variant(value: Any) -> GLib.Variant:
    """Publishable values, typed exactly as the kit types them."""
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


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def _canonical_app(app_id: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in app_id.removesuffix(".desktop").lower())


@dataclass
class _Posted:
    key: str
    id: int
    summary: str
    body: str
    actions: list[tuple[str, str]]
    options: dict
    on_action: Callable[[str], None] | None
    redacted: bool
    public: tuple[str, str]
    hints: dict = field(default_factory=dict)


class Notifier:
    """org.freedesktop.Notifications for an agent, with lock-screen privacy.

    Notifications carry the app's desktop id, so the shell shows and groups
    them as the app's and applies its per-app settings. While the screen is
    locked and the person's settings keep details off the lock screen
    (``show-in-lock-screen`` or the app's ``details-in-lock-screen``), only the
    public text is sent; unlocking replaces it with the real one, silently.
    """

    BUS = "org.freedesktop.Notifications"
    PATH = "/org/freedesktop/Notifications"
    INTERFACE = "org.freedesktop.Notifications"

    def __init__(self, connection: Gio.DBusConnection, app_id: str, app_name: str, *, icon: str | None = None,
                 lock_state: Callable[[], bool] | None = None,
                 details_on_lock_screen: Callable[[], bool] | None = None) -> None:
        self.connection = connection
        self.app_id, self.app_name = app_id, app_name
        self.icon = icon or app_id
        self._posted: dict[str, _Posted] = {}
        self._by_id: dict[int, str] = {}
        self._tokens: dict[int, str] = {}
        self._locked = lock_state or self._screen_locked
        self._details = details_on_lock_screen or self._details_allowed
        self._subscriptions = [
            connection.signal_subscribe(self.BUS, self.INTERFACE, "ActionInvoked", self.PATH, None,
                                        Gio.DBusSignalFlags.NONE, self._action_invoked),
            connection.signal_subscribe(self.BUS, self.INTERFACE, "ActivationToken", self.PATH, None,
                                        Gio.DBusSignalFlags.NONE, self._activation_token),
            connection.signal_subscribe(self.BUS, self.INTERFACE, "NotificationClosed", self.PATH, None,
                                        Gio.DBusSignalFlags.NONE, self._closed),
            connection.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                        "/org/freedesktop/DBus", self.BUS, Gio.DBusSignalFlags.NONE,
                                        self._server_changed),
        ]
        self.on_unlock: list[Callable[[], None]] = []
        self._screensaver = connection.signal_subscribe(None, "org.gnome.ScreenSaver", "ActiveChanged", None, None,
                                                        Gio.DBusSignalFlags.NONE, self._screensaver_changed)

    # -- privacy -----------------------------------------------------------
    def _screen_locked(self) -> bool:
        try:
            result = self.connection.call_sync("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver",
                                               "org.gnome.ScreenSaver", "GetActive", None, GLib.VariantType("(b)"),
                                               Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
            return bool(result.unpack()[0])
        except GLib.Error:
            return False

    def _details_allowed(self) -> bool:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return False
        general = source.lookup("org.gnome.desktop.notifications", True)
        if general is None:
            return False
        if general.has_key("show-in-lock-screen") and not Gio.Settings.new_full(general, None, None).get_boolean(
                "show-in-lock-screen"):
            return False
        per_app = source.lookup("org.gnome.desktop.notifications.application", True)
        if per_app is None or not per_app.has_key("details-in-lock-screen"):
            return False
        path = f"/org/gnome/desktop/notifications/application/{_canonical_app(self.app_id)}/"
        return Gio.Settings.new_full(per_app, None, path).get_boolean("details-in-lock-screen")

    def private(self) -> bool:
        return self._locked() and not self._details()

    def _screensaver_changed(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        if parameters.unpack()[0]:
            return
        for posted in list(self._posted.values()):
            if posted.redacted:
                self._send(posted, redact=False, silent=True)
        for callback in list(self.on_unlock):
            callback()

    # -- posting -----------------------------------------------------------
    def notify(self, key: str, summary: str, body: str = "", *, actions: list[tuple[str, str]] = (),
               on_action: Callable[[str], None] | None = None, category: str = "", urgency: int = 1,
               public_summary: str | None = None, public_body: str = "", resident: bool = False,
               transient: bool = False, sound: str | None = None, hints: dict | None = None,
               timeout: int = -1) -> int:
        """Show (or replace) the notification ``key``; returns its server id, 0 if none."""
        previous = self._posted.get(key)
        posted = _Posted(key, previous.id if previous else 0, summary, body, list(actions),
                         dict(category=category, urgency=urgency, resident=resident, transient=transient,
                              sound=sound, timeout=timeout),
                         on_action, False, (public_summary or self.app_name, public_body), dict(hints or {}))
        return self._send(posted, redact=self.private(), silent=False)

    def _send(self, posted: _Posted, *, redact: bool, silent: bool) -> int:
        options = posted.options
        summary, body = posted.public if redact else (posted.summary, posted.body)
        hints = {
            "desktop-entry": GLib.Variant("s", self.app_id),
            "urgency": GLib.Variant("y", max(0, min(2, int(options["urgency"])))),
        }
        if options["category"]:
            hints["category"] = GLib.Variant("s", options["category"])
        if options["resident"]:
            hints["resident"] = GLib.Variant("b", True)
        if options["transient"]:
            hints["transient"] = GLib.Variant("b", True)
        if silent:
            hints["suppress-sound"] = GLib.Variant("b", True)
        elif options["sound"]:
            hints["sound-name"] = GLib.Variant("s", options["sound"])
        if not redact:
            hints.update(posted.hints)
        flat: list[str] = []
        for action, label in posted.actions:
            flat += [action, label]
        try:
            result = self.connection.call_sync(
                self.BUS, self.PATH, self.INTERFACE, "Notify",
                GLib.Variant("(susssasa{sv}i)", (self.app_name, posted.id, self.icon, summary, body, flat, hints,
                                                 int(options["timeout"]))),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as error:
            LOG.warning("A notification was not shown: %s", error.message)
            return 0
        identifier = int(result.unpack()[0])
        if posted.id and posted.id != identifier:
            self._by_id.pop(posted.id, None)
        posted.id, posted.redacted = identifier, redact
        self._posted[posted.key] = posted
        self._by_id[identifier] = posted.key
        return identifier

    def withdraw(self, key: str) -> None:
        posted = self._posted.pop(key, None)
        if posted is None:
            return
        self._by_id.pop(posted.id, None)
        self._tokens.pop(posted.id, None)
        self.connection.call(self.BUS, self.PATH, self.INTERFACE, "CloseNotification",
                             GLib.Variant("(u)", (posted.id,)), None, Gio.DBusCallFlags.NONE, 2000, None, None)

    def posted(self, key: str) -> int:
        item = self._posted.get(key)
        return item.id if item else 0

    def key_for(self, notification_id: int) -> str | None:
        return self._by_id.get(int(notification_id))

    def keys(self) -> list[str]:
        return list(self._posted)

    def activation_token(self, key: str) -> str:
        """The XDG activation token the server sent for ``key``'s last action, if any."""
        item = self._posted.get(key)
        return self._tokens.get(item.id, "") if item else ""

    def _activation_token(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        notification_id, token = parameters.unpack()
        if int(notification_id) in self._by_id:
            self._tokens[int(notification_id)] = str(token)

    def _action_invoked(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        notification_id, action = parameters.unpack()
        key = self._by_id.get(int(notification_id))
        if key is None:
            return
        posted = self._posted.get(key)
        if posted is not None and posted.on_action is not None:
            try:
                posted.on_action(str(action))
            except Exception:
                LOG.exception("A notification action failed")

    def _closed(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        notification_id, _reason = parameters.unpack()
        self._tokens.pop(int(notification_id), None)
        key = self._by_id.pop(int(notification_id), None)
        if key is not None and (posted := self._posted.get(key)) is not None and posted.id == int(notification_id):
            self._posted.pop(key, None)

    def _server_changed(self, *_args) -> None:
        # A restarted shell forgets every notification it had.
        self._posted.clear()
        self._by_id.clear()
        self._tokens.clear()

    def close(self) -> None:
        for subscription in self._subscriptions + [self._screensaver]:
            self.connection.signal_unsubscribe(subscription)
        self._subscriptions = []



# ---------------------------------------------------------------------------
# The agent (used when the kit module is not available)
# ---------------------------------------------------------------------------


class Agent:
    """The kit's ``Agent`` surface that Charlie uses, on the same bus contract."""

    app_id: str = ""
    agent_id: str = ""

    def __init__(self, *, connection: Gio.DBusConnection | None = None) -> None:
        if not _APP_ID.fullmatch(self.app_id or ""):
            raise ValueError("Agent.app_id must be a valid application ID")
        if not (self.agent_id or "").startswith(self.app_id + ".") or len(self.agent_id) <= len(self.app_id) + 1:
            raise ValueError("Agent.agent_id must start with app_id followed by a dot")
        self._connection = connection
        self._values: dict[str, GLib.Variant] = {}
        self._flush_source = 0
        self._loop: GLib.MainLoop | None = None
        self._queue: deque[Wake] = deque()
        self._waking = False
        self._started = False
        self._registration = 0
        self._name_id = 0
        self._exit_status = 0
        self.managed = is_managed()

    # -- hooks ---------------------------------------------------------------
    def on_start(self) -> None:
        """Called once the agent owns its name, before the first wake."""

    def on_wake(self, wake: Wake) -> None:
        """Called for every wake, one at a time."""

    def on_stop(self) -> None:
        """Called when the agent is asked to stop."""

    # -- values --------------------------------------------------------------
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
        self._schedule_flush()

    def unpublish(self, name: str) -> None:
        if self._values.pop(name, None) is not None:
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        if not self._flush_source:
            self._flush_source = GLib.idle_add(self._flush)

    def _flush(self) -> bool:
        self._flush_source = 0
        if self._connection is not None and self._registration:
            self._connection.emit_signal(
                None, AGENT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (AGENT_INTERFACE,
                                            {"Values": GLib.Variant("a{sv}", dict(self._values))}, [])))
        return GLib.SOURCE_REMOVE

    # -- bus -----------------------------------------------------------------
    def _handle_call(self, _connection, sender, _path, _interface, method, parameters, invocation) -> None:
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
                GLib.Variant("(s)", (SERVICE_NAME,)), GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
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
                except Exception:
                    LOG.exception("%s: the %s wake failed", self.agent_id, wake.reason)
        finally:
            self._waking = False
        return GLib.SOURCE_REMOVE

    # -- lifecycle -----------------------------------------------------------
    def quit(self, status: int = 0) -> None:
        """End cleanly; the next wake starts the agent again."""
        self._exit_status = status
        if self._loop is not None:
            GLib.idle_add(self._loop.quit)

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._registration:
            return
        if self._connection is None:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
        self._registration = self._connection.register_object(AGENT_PATH, info, self._handle_call,
                                                              self._get_property, None)
        self._name_id = Gio.bus_own_name_on_connection(self._connection, self.agent_id,
                                                       Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                                                       self._name_acquired, self._name_lost)

    def _name_acquired(self, _connection, _name) -> None:
        if self._started:
            return
        self._started = True
        if not self.managed:
            LOG.info("%s: running unmanaged; luma-background will not wake it", self.agent_id)
        try:
            self.on_start()
        except Exception:
            LOG.exception("%s: on_start failed", self.agent_id)
            self.quit(1)
            return
        self._schedule_flush()
        if self._queue:
            GLib.idle_add(self._drain)

    def _name_lost(self, _connection, _name) -> None:
        if not self._started:
            LOG.info("%s: the agent's name is already owned", self.agent_id)
            self._exit_status = 1
        if self._loop is not None:
            self._loop.quit()

    def stop(self) -> None:
        if self._started:
            self._started = False
            try:
                self.on_stop()
            except Exception:
                LOG.exception("%s: on_stop failed", self.agent_id)
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
            LOG.error("%s: cannot join the session bus: %s", self.agent_id, error.message)
            return 1

        def terminate() -> bool:
            self._loop.quit()  # type: ignore[union-attr]
            return GLib.SOURCE_CONTINUE

        sources = [GLib.unix_signal_add(GLib.PRIORITY_HIGH, number, terminate)
                   for number in (signal.SIGTERM, signal.SIGINT)]
        try:
            self._loop.run()
        finally:
            for source in sources:
                GLib.source_remove(source)
            self.stop()
        return self._exit_status


def request_background(app_id: str, *, reason: str, autostart: bool = True,
                       callback: Callable[[BackgroundResult], None] | None = None,
                       connection: Gio.DBusConnection | None = None, **_options) -> None:
    """Ask luma-background for background activity (native apps only here).

    The kit's version also covers the Flatpak Background portal and is used
    instead whenever the kit module is available.
    """
    if not _APP_ID.fullmatch(app_id or ""):
        raise ValueError("invalid application ID")
    if len(reason) > 256:
        raise ValueError("reason is at most 256 characters")
    callback = callback or (lambda _result: None)
    if is_sandboxed():
        callback(BackgroundResult(False, False, 2))
        return
    connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def answered(bus, result) -> None:
        try:
            response, results = bus.call_finish(result).unpack()
        except GLib.Error:
            callback(BackgroundResult(False, False, 2))
            return
        callback(BackgroundResult(bool(results.get("background")), bool(results.get("autostart")), response))

    connection.call(SERVICE_NAME, SERVICE_PATH, SERVICE_INTERFACE, "RequestBackground",
                    GLib.Variant("(sa{sv})", ("", {"reason": GLib.Variant("s", reason),
                                                   "autostart": GLib.Variant("b", autostart)})),
                    GLib.VariantType("(ua{sv})"), Gio.DBusCallFlags.NONE, GLib.MAXINT, None, answered)


# ---------------------------------------------------------------------------
# Wakes an unmanaged agent watches for itself
# ---------------------------------------------------------------------------


class EnvironmentWakes:
    """Network, resume and unlock, for an agent luma-background does not manage.

    Under the service these wakes come from it instead; this is the fallback
    for a session without luma-background.
    """

    def __init__(self, connection: Gio.DBusConnection, wake: Callable[[str], None]) -> None:
        self.connection = connection
        self._wake = wake
        self._last: dict[str, float] = {}
        self._subscriptions: list[tuple[Gio.DBusConnection, int]] = []
        self._sources: list[int] = []
        self._monitor = Gio.NetworkMonitor.get_default()
        self._available = self._monitor.get_network_available()
        self._handler = self._monitor.connect("network-changed", self._network_changed)
        self._subscriptions.append((connection, connection.signal_subscribe(
            None, "org.gnome.ScreenSaver", "ActiveChanged", None, None, Gio.DBusSignalFlags.NONE,
            lambda *args: None if args[5].unpack()[0] else self.wake("unlock"))))
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return  # no system bus: no resume events
        self._subscriptions.append((system, system.signal_subscribe(
            "org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
            "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, self._sleep)))

    def wake(self, reason: str) -> None:
        now = time.monotonic()
        # Resume and the network returning usually arrive together.
        if now - self._last.get(reason, -60.0) < 5:
            return
        self._last[reason] = now
        LOG.info("Woke: %s", reason)
        self._wake(reason)

    def _network_changed(self, _monitor, available) -> None:
        if available and not self._available:
            self.wake("network")
        self._available = available

    def _sleep(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        if not parameters.unpack()[0]:
            # Give the network a moment to come back before reconnecting.
            self._sources.append(GLib.timeout_add_seconds(2, lambda: (self.wake("resume"), False)[1]))

    def close(self) -> None:
        if self._handler:
            self._monitor.disconnect(self._handler)
            self._handler = 0
        for connection, subscription in self._subscriptions:
            connection.signal_unsubscribe(subscription)
        self._subscriptions = []
        for source in self._sources:
            GLib.source_remove(source)
        self._sources = []


def configure_logging() -> None:
    level = logging.DEBUG if os.environ.get("LUMA_AGENT_DEBUG") else logging.INFO
    logging.basicConfig(level=level, format="%(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Prefer the kit's module
# ---------------------------------------------------------------------------

_TOOLKIT_IMPORT = re.compile(
    r"^\s*(?:from\s+\.|from\s+luma_appkit\b|import\s+luma_appkit\b"
    r"|.*require_version\(\s*[\"'](?:Gtk|Gdk|Adw|WebKit)"
    r"|from\s+gi\.repository\s+import\s+.*\b(?:Gtk|Gdk|Adw|WebKit))", re.MULTILINE)


def load_kit_background(search_path: list[str] | None = None):
    """``luma_appkit/background.py`` loaded by file path, or ``None``.

    ``luma_appkit/__init__`` imports GTK, which the agent must never load, so
    the package is located without being imported and the one file is
    executed on its own. It is used only when it is self-contained, imports no
    toolkit and speaks this contract (``AGENT_PATH`` and ``Agent``).
    """
    if search_path is None:
        try:
            spec = importlib.util.find_spec("luma_appkit")
        except (ImportError, ValueError):
            return None
        locations = list(spec.submodule_search_locations or ()) if spec is not None else []
    else:
        locations = [str(Path(entry) / "luma_appkit") for entry in search_path]
    candidate = next((Path(location) / "background.py" for location in locations
                      if (Path(location) / "background.py").is_file()), None)
    if candidate is None:
        return None
    try:
        source = candidate.read_text()
    except OSError:
        return None
    if _TOOLKIT_IMPORT.search(source) or 'AGENT_PATH = "/org/projectluma/BackgroundAgent1"' not in source:
        return None
    name = "charlie_luma._kit_background"
    module_spec = importlib.util.spec_from_file_location(name, candidate)
    if module_spec is None or module_spec.loader is None:
        return None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    try:
        module_spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        return None
    agent = getattr(module, "Agent", None)
    if (getattr(module, "AGENT_PATH", None) != AGENT_PATH or getattr(module, "AGENT_INTERFACE", None) != AGENT_INTERFACE
            or not isinstance(agent, type)
            or not all(callable(getattr(agent, member, None))
                       for member in ("on_start", "on_wake", "on_stop", "publish", "quit", "run"))):
        sys.modules.pop(name, None)
        return None
    return module


_kit = load_kit_background()
KIT_BACKGROUND = _kit is not None
AgentBase = _kit.Agent if _kit is not None else Agent
if _kit is not None and callable(getattr(_kit, "request_background", None)):
    request_background = _kit.request_background  # noqa: F811

__all__ = [
    "AGENT_INTERFACE", "AGENT_PATH", "Agent", "AgentBase", "BackgroundResult", "EnvironmentWakes",
    "KIT_BACKGROUND", "Notifier", "SERVICE_NAME", "WAKE_REASONS", "Wake", "configure_logging", "is_managed",
    "is_sandboxed", "load_kit_background", "name_has_owner", "request_background", "service_installed",
    "service_installed_async",
]
