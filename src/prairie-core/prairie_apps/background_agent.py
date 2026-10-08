# SPDX-License-Identifier: Apache-2.0

"""Background agents for Luma's own apps (ADR-033).

An agent is a small process with no windows that keeps one app working while
its window is closed: Messages receiving, Phone listening for calls, Calendar
reminders, Clock alarms. It is not the app with its window hidden.

The contract is the published one (docs/developer/kit/background-agents.md,
interface version 1):

* **Declared, and started by the system.** Each app installs
  ``/usr/share/luma/background/<app id>.toml`` (``[application]`` and
  ``[background]``). luma-background generates ``app-<app id>-agent.service``
  under a luma-background slice with the category's limits, starts it at login
  when the app is allowed, and wakes it: ``login``, ``network``, ``resume``,
  ``schedule`` and ``request``. Apps ship no units of their own.
* **One per session.** The agent owns its D-Bus name (``<app id>.Agent``) with
  ``DO_NOT_QUEUE``; a second start exits at once.
* **Published values.** ``/org/projectluma/BackgroundAgent1`` carries
  ``org.projectluma.BackgroundAgent1``: ``AppId``, ``Values`` (announced with
  PropertiesChanged) and ``Wake(reason, details)``, accepted only from
  luma-background. Live extensions read ``Values`` with ``from_agent``.
* **Before the service is installed** an autostart entry starts each agent at
  login with ``--agent --autostart``; that start steps aside when the service
  is present. Unmanaged, the agent watches the network and resume itself. A
  Flatpak asks the Background portal.

``luma_appkit.background`` implements the bus side. It is used whenever it is
installed, loaded on its own so the kit's GTK widgets never enter an agent;
without it the same contract is implemented here.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

LOG = logging.getLogger("luma.background")

SERVICE_BUS = "org.projectluma.Background1"
SERVICE_PATH = "/org/projectluma/Background1"
SERVICE_INTERFACE = "org.projectluma.Background1"
AGENT_INTERFACE = "org.projectluma.BackgroundAgent1"
AGENT_PATH = "/org/projectluma/BackgroundAgent1"
DECLARATION_DIRECTORY = Path("/usr/share/luma/background")

CATEGORIES = ("communication", "calendar", "alarms", "mail", "sync", "widget-data", "other")
WAKE_EVENTS = ("login", "network", "resume", "schedule")
# What an agent can be woken for: the declared events, a request (an app asked
# for it, or D-Bus activation), and unlock, which agents here watch themselves.
WAKE_REASONS = ("login", "network", "resume", "schedule", "request", "unlock")

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

_APP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+")
_VALUE_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}")

AGENT_XML = f"""<node><interface name="{AGENT_INTERFACE}">
  <method name="Wake"><arg name="reason" type="s" direction="in"/><arg name="details" type="a{{sv}}" direction="in"/></method>
  <property name="AppId" type="s" access="read"/>
  <property name="Values" type="a{{sv}}" access="read"/>
</interface></node>"""


@dataclass(frozen=True)
class AgentInfo:
    """What an app's [background] declaration says about its agent."""

    app_id: str
    name: str
    category: str
    exec_args: tuple[str, ...]
    wake: tuple[str, ...] = ("login",)
    publishes: tuple[str, ...] = ("notifications",)
    purpose: str = ""

    def __post_init__(self) -> None:
        if not _APP_ID.fullmatch(self.app_id):
            raise ValueError(f"invalid app id {self.app_id!r}")
        if self.category not in CATEGORIES:
            raise ValueError(f"invalid category {self.category!r}")
        if not self.wake or any(event not in WAKE_EVENTS for event in self.wake):
            raise ValueError("invalid wake event")

    @property
    def agent_id(self) -> str:
        return f"{self.app_id}.Agent"

    @property
    def unit(self) -> str:
        """The unit luma-background generates for this agent."""
        return f"app-{self.app_id}-agent.service"


def is_sandboxed() -> bool:
    return Path("/.flatpak-info").exists()


def is_managed() -> bool:
    """Started by luma-background in the unit it generated."""
    return os.environ.get("LUMA_BACKGROUND_MANAGED") == "1"


def resident_bytes() -> int:
    """This process's resident set, from /proc (0 where there is none)."""
    try:
        with open("/proc/self/statm", "rb") as stream:
            return int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def variant(value: Any) -> GLib.Variant:
    """A publishable value: plain data any toolkit can read (the kit's rules)."""
    if isinstance(value, GLib.Variant):
        return value
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        return GLib.Variant("x", value)
    if isinstance(value, float):
        return GLib.Variant("d", value)
    if isinstance(value, str):
        return GLib.Variant("s", value)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return GLib.Variant("as", list(value))
    if isinstance(value, Mapping):
        return GLib.Variant("a{sv}", {str(key): variant(item) for key, item in value.items()})
    raise TypeError(f"cannot publish a value of type {type(value).__name__}")


class SystemdUser:
    """The few systemd user manager calls agents make about other units."""

    BUS = "org.freedesktop.systemd1"
    PATH = "/org/freedesktop/systemd1"
    MANAGER = "org.freedesktop.systemd1.Manager"

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection

    def _call(self, method: str, parameters: GLib.Variant | None, timeout: int = 5000):
        return self.connection.call_sync(self.BUS, self.PATH, self.MANAGER, method, parameters, None,
                                         Gio.DBusCallFlags.NONE, timeout, None)

    def unit_installed(self, unit: str) -> bool:
        try:
            state = self._call("GetUnitFileState", GLib.Variant("(s)", (unit,))).unpack()[0]
        except GLib.Error:
            return False
        return state not in {"masked", "masked-runtime", "not-found", "bad"}

    def active(self, unit: str) -> bool:
        try:
            path = self._call("GetUnit", GLib.Variant("(s)", (unit,))).unpack()[0]
            state = self.connection.call_sync(self.BUS, path, "org.freedesktop.DBus.Properties", "Get",
                                              GLib.Variant("(ss)", ("org.freedesktop.systemd1.Unit", "ActiveState")),
                                              None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        except GLib.Error:
            return False
        return state in {"active", "activating", "reloading"}

    def start(self, unit: str) -> bool:
        try:
            self._call("StartUnit", GLib.Variant("(ss)", (unit, "replace")), 10000)
            return True
        except GLib.Error as error:
            LOG.warning("Could not start %s: %s", unit, error.message)
            return False


def name_has_owner(connection: Gio.DBusConnection, name: str) -> bool:
    try:
        result = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                      "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(b)"),
                                      Gio.DBusCallFlags.NONE, 2000, None)
        return bool(result.unpack()[0])
    except GLib.Error:
        return False


def name_activatable(connection: Gio.DBusConnection, name: str) -> bool:
    try:
        names = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                     "ListActivatableNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE,
                                     2000, None).unpack()[0]
    except GLib.Error:
        return False
    return name in names


def service_present(connection: Gio.DBusConnection) -> bool:
    """Whether luma-background is installed in this session."""
    return name_has_owner(connection, SERVICE_BUS) or name_activatable(connection, SERVICE_BUS)


def agent_allowed(connection: Gio.DBusConnection, app_id: str) -> bool | None:
    """The person's decision for an app's agent, from luma-background; None without the service."""
    if not service_present(connection):
        return None
    try:
        agent = connection.call_sync(SERVICE_BUS, SERVICE_PATH, SERVICE_INTERFACE, "GetAgent",
                                     GLib.Variant("(s)", (app_id,)), GLib.VariantType("(a{sv})"),
                                     Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error:
        return None
    return bool(agent.get("allowed", False))


def ensure_agent(app_id: str, agent_name: str, *, reason: str, connection: Gio.DBusConnection | None = None,
                 timeout: float = 5.0, foreground: bool = False) -> bool:
    """True once the app's agent is running, asking luma-background to start it if needed.

    Apps call this when their window opens, to use the agent rather than do
    its work twice. The request is the service's RequestBackground, which
    answers at once for an app that is allowed (and starts its agent) or
    declined; an undecided third-party app gets the one-time prompt, so ask
    only when the person has done something that needs it. Without the
    service, or when the agent does not come up in time, False: the app does
    the work itself while its window is open. Independently installed
    first-party UIs use foreground=True to acquire the same native owner for
    their window's lifetime, without changing background permissions. They
    release it when closing; disconnect also releases it. They never fall
    back to copying credentials or running an unmanaged host owner.
    """
    try:
        connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return False
    if not foreground and name_has_owner(connection, agent_name):
        return True
    if not service_present(connection):
        return False
    try:
        if foreground:
            ready = connection.call_sync(SERVICE_BUS, SERVICE_PATH, SERVICE_INTERFACE, 'RequestForeground',
                None, GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NONE,
                int(timeout * 1000), None).unpack()[0]
            response, results = (0 if ready else 1), {'background': ready}
        else:
            response, results = connection.call_sync(
                SERVICE_BUS, SERVICE_PATH, SERVICE_INTERFACE, "RequestBackground",
                GLib.Variant("(sa{sv})", ("", {"reason": GLib.Variant("s", reason[:256]),
                                               "autostart": GLib.Variant("b", True)})),
                GLib.VariantType("(ua{sv})"), Gio.DBusCallFlags.NONE, int(timeout * 1000), None).unpack()
    except GLib.Error as error:
        LOG.info("Background activity for %s was not granted: %s", app_id, error.message)
        if foreground:
            release_foreground(connection)
        return False
    if response != 0 or not results.get("background"):
        if foreground:
            release_foreground(connection)
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name_has_owner(connection, agent_name):
            return True
        time.sleep(0.1)
    if foreground:
        release_foreground(connection)
    return False


def release_foreground(connection: Gio.DBusConnection) -> None:
    """Release only this connection's managed native service lease."""
    try:
        connection.call_sync(SERVICE_BUS, SERVICE_PATH, SERVICE_INTERFACE, 'ReleaseForeground',
            None, None, Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
    except GLib.Error:
        pass  # The bus owner disappearing is the fallback, including crashes.


def load_kit():
    """luma_appkit.background, loaded by its file so luma_appkit's GTK widgets stay out; None without it."""
    if os.environ.get("LUMA_AGENT_KIT") == "0":
        return None
    cached = sys.modules.get("luma_appkit_background_standalone")
    if cached is not None:
        return cached
    try:
        spec = importlib.util.find_spec("luma_appkit")
    except (ImportError, ValueError):
        return None
    locations = list(spec.submodule_search_locations or []) if spec is not None else []
    path = next((Path(location) / "background.py" for location in locations
                 if (Path(location) / "background.py").is_file()), None)
    if path is None:
        return None
    try:
        module_spec = importlib.util.spec_from_file_location("luma_appkit_background_standalone", path)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        module_spec.loader.exec_module(module)
    except Exception as error:  # a kit this code cannot use is the same as no kit
        sys.modules.pop("luma_appkit_background_standalone", None)
        LOG.info("luma_appkit.background not usable (%s); using the built-in agent", type(error).__name__)
        return None
    if getattr(module, "AGENT_PATH", "") != AGENT_PATH or not hasattr(module, "Agent"):
        return None
    return module


def launch_app(app_id: str, argv: list[str]) -> bool:
    """Open an app's window from an agent, in the app's own scope.

    A process spawned directly would stay in the agent's cgroup: the window
    would count against the agent's memory limit (and be killed by it) and
    would stop with the agent. On systemd it runs in a new
    ``app-luma-<app id>-<number>.scope`` under app.slice, named the way the
    shell names launched apps, so luma-background and the Semantic Broker
    identify the window as the app.
    """
    import shutil
    command = list(argv)
    if not is_sandboxed() and shutil.which("systemd-run") and os.environ.get("XDG_RUNTIME_DIR"):
        number = int.from_bytes(os.urandom(4), "big")
        command = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--slice=app.slice",
                   f"--unit=app-luma-{app_id}-{number}.scope", "--", *argv]
    try:
        Gio.Subprocess.new(command, Gio.SubprocessFlags.NONE)
        return True
    except GLib.Error as error:
        LOG.warning("%s could not be opened: %s", app_id, error.message)
        return False


# ---------------------------------------------------------------------------
# Wall-clock scheduling
# ---------------------------------------------------------------------------


class Schedule:
    """Named wall-clock deadlines, each fired once, late if the machine slept.

    Uses Clock's CLOCK_REALTIME timerfd, which expires as soon as the machine
    resumes past the deadline and also when the clock is set.
    """

    def __init__(self, fire: Callable[[str], None]) -> None:
        from .clock_alarms import WallClockTimer

        self._fire = fire
        self._deadlines: dict[str, float] = {}
        self._timer = WallClockTimer(self._expired)

    def at(self, key: str, epoch: float) -> None:
        self._deadlines[key] = float(epoch)
        self._arm()

    def cancel(self, key: str) -> None:
        if self._deadlines.pop(key, None) is not None:
            self._arm()

    def clear(self) -> None:
        self._deadlines.clear()
        self._timer.cancel()

    def next(self) -> tuple[str, float] | None:
        if not self._deadlines:
            return None
        key = min(self._deadlines, key=self._deadlines.__getitem__)
        return key, self._deadlines[key]

    def _arm(self) -> None:
        upcoming = self.next()
        if upcoming is None:
            self._timer.cancel()
        else:
            self._timer.arm(upcoming[1])

    def _expired(self) -> None:
        now = time.time()
        due = sorted((epoch, key) for key, epoch in self._deadlines.items() if epoch <= now)
        for _epoch, key in due:
            self._deadlines.pop(key, None)
        self._arm()
        for _epoch, key in due:
            try:
                self._fire(key)
            except Exception:
                LOG.exception("Scheduled work %s failed", key)


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
        self._locked = lock_state or self._screen_locked
        self._details = details_on_lock_screen or self._details_allowed
        self._subscriptions = [
            connection.signal_subscribe(self.BUS, self.INTERFACE, "ActionInvoked", self.PATH, None,
                                        Gio.DBusSignalFlags.NONE, self._action_invoked),
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
            LOG.warning("Notification %s was not shown: %s", posted.key, error.message)
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
        self.connection.call(self.BUS, self.PATH, self.INTERFACE, "CloseNotification",
                             GLib.Variant("(u)", (posted.id,)), None, Gio.DBusCallFlags.NONE, 2000, None, None)

    def posted(self, key: str) -> int:
        item = self._posted.get(key)
        return item.id if item else 0

    def key_for(self, notification_id: int) -> str | None:
        return self._by_id.get(int(notification_id))

    def keys(self) -> list[str]:
        return list(self._posted)

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
                LOG.exception("Notification action %s failed", action)

    def _closed(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        notification_id, _reason = parameters.unpack()
        key = self._by_id.pop(int(notification_id), None)
        if key is not None and (posted := self._posted.get(key)) is not None and posted.id == int(notification_id):
            self._posted.pop(key, None)

    def _server_changed(self, *_args) -> None:
        # A restarted shell forgets every notification it had.
        self._posted.clear()
        self._by_id.clear()

    def close(self) -> None:
        for subscription in self._subscriptions + [self._screensaver]:
            self.connection.signal_unsubscribe(subscription)
        self._subscriptions = []


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class AgentPublisher:
    """An agent's presence on the bus, without owning the process or its main loop.

    Owns ``<app id>.Agent`` (DO_NOT_QUEUE), exports
    ``org.projectluma.BackgroundAgent1`` and turns wakes into ``wake(reason)``.
    ``Agent`` runs a whole agent process around one; an app whose alarms live
    in its own process (Clock) uses one directly.

    With luma_appkit.background installed, the kit's Agent does the bus side.
    Managed by luma-background, wakes come from the service. Unmanaged (the
    autostart fallback, a developer's terminal) the network returning and
    resume are watched here instead. Unlock is always watched here: the
    service has no such wake.
    """

    def __init__(self, info: AgentInfo, *, start: Callable[["AgentPublisher"], None] | None = None,
                 wake: Callable[[str], None] | None = None, stop: Callable[[], None] | None = None,
                 lost: Callable[[bool], None] | None = None, kit=None) -> None:
        self.info = info
        self._start = start or (lambda _agent: None)
        self._wake = wake or (lambda _reason: None)
        self._stop = stop or (lambda: None)
        self._lost_callback = lost or (lambda _started: None)
        self.kit = load_kit() if kit is None else (kit or None)
        self.managed = is_managed()
        self.connection: Gio.DBusConnection | None = None
        self.state, self.detail = "starting", ""
        self.last_wake: tuple[str, int] = ("", 0)
        self._values: dict[str, GLib.Variant] = {}
        self._bridge = None
        self._owner = 0
        self._registration = 0
        self._flush_source = 0
        self._subscriptions: list[tuple[Gio.DBusConnection, int]] = []
        self._network = None
        self._network_handler = 0
        self._network_available: bool | None = None
        self._last_wake_at: dict[str, float] = {}
        self._started = False

    @property
    def owned(self) -> bool:
        return self._started

    @property
    def values(self) -> dict[str, Any]:
        return {name: value.unpack() for name, value in self._values.items()}

    # -- lifecycle -----------------------------------------------------------
    def own(self, connection: Gio.DBusConnection) -> None:
        if self._owner or self._bridge is not None:
            return
        self.connection = connection
        if self.kit is not None:
            self._bridge = _bridge_class(self.kit)(self, connection=connection)
            if name_has_owner(connection, self.info.agent_id):
                self._bridge = None
                self._lost(connection, self.info.agent_id)
                return
            self._bridge.start()
            return
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        self._registration = connection.register_object(AGENT_PATH, node.interfaces[0], self._method,
                                                        self._get_property, None)
        self._owner = Gio.bus_own_name_on_connection(
            connection, self.info.agent_id, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, self._acquired, self._lost)

    def _acquired(self, _connection=None, _name=None) -> None:
        if self._started:
            return
        self._started = True
        self._watch_environment()
        LOG.info("%s started (%s, %s)", self.info.agent_id, self.info.category,
                 "managed" if self.managed else "unmanaged")
        try:
            self._start(self)
        except Exception:
            LOG.exception("%s could not start", self.info.agent_id)
            self._failed()
            return
        if self.state == "starting":
            self.state = "running"
        if not self.managed:
            self.wake("login")  # the service would have sent it

    def _failed(self) -> None:
        self._lost_callback(True)

    def _lost(self, _connection=None, _name=None) -> None:
        started = self._started
        if not started:
            LOG.info("%s is already running", self.info.agent_id)
        else:
            LOG.warning("%s lost its bus name", self.info.agent_id)
        self._lost_callback(started)

    def release(self) -> None:
        """Stop publishing: the agent's work ended (Clock with nothing left to ring)."""
        if self._started:
            self._started = False
            try:
                self._stop()
            except Exception:
                LOG.exception("%s did not stop cleanly", self.info.agent_id)
        if self._flush_source:
            GLib.source_remove(self._flush_source)
            self._flush_source = 0
        if self._network is not None and self._network_handler:
            self._network.disconnect(self._network_handler)
            self._network_handler = 0
        for connection, subscription in self._subscriptions:
            connection.signal_unsubscribe(subscription)
        self._subscriptions = []
        if self._bridge is not None:
            bridge, self._bridge = self._bridge, None
            bridge.stop()
        if self.connection is not None and self._registration:
            self.connection.unregister_object(self._registration)
            self._registration = 0
        if self._owner:
            Gio.bus_unown_name(self._owner)
            self._owner = 0

    # -- published values ------------------------------------------------------
    def set_state(self, state: str, detail: str = "") -> None:
        """What the agent is doing, for its log (Settings reads the service's view)."""
        if (state, detail) != (self.state, self.detail):
            self.state, self.detail = state, detail[:200]
            LOG.debug("%s: %s %s", self.info.agent_id, state, self.detail)

    def publish(self, name: str, value: Any) -> None:
        """Set one published value (``unread-count``…); subscribers see Values change."""
        if not _VALUE_NAME.fullmatch(name or ""):
            raise ValueError(f"invalid value name {name!r}")
        converted = variant(value)
        current = self._values.get(name)
        if current is not None and current.equal(converted):
            return
        self._values[name] = converted
        if self._bridge is not None:
            self._bridge.publish(name, converted)
        elif not self._flush_source:
            self._flush_source = GLib.idle_add(self._flush)

    def _flush(self) -> bool:
        self._flush_source = 0
        if self.connection is not None and self._registration:
            self.connection.emit_signal(None, AGENT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                        GLib.Variant("(sa{sv}as)", (AGENT_INTERFACE,
                                                                    {"Values": GLib.Variant("a{sv}", self._values)},
                                                                    [])))
        return GLib.SOURCE_REMOVE

    def _get_property(self, _connection, _sender, _path, _interface, name):
        if name == "AppId":
            return GLib.Variant("s", self.info.app_id)
        if name == "Values":
            return GLib.Variant("a{sv}", self._values)
        return None

    def _method(self, connection, sender, _path, _interface, method, parameters, invocation) -> None:
        if method != "Wake":
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        try:
            owner = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                         "GetNameOwner", GLib.Variant("(s)", (SERVICE_BUS,)), GLib.VariantType("(s)"),
                                         Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        except GLib.Error:
            owner = ""
        if not owner or owner != sender:
            invocation.return_dbus_error(f"{AGENT_INTERFACE}.Error.NotAuthorized", "wakes are delivered by luma-background")
            return
        reason, _details = parameters.unpack()
        if reason not in WAKE_REASONS:
            invocation.return_dbus_error(f"{AGENT_INTERFACE}.Error.InvalidArgument", "unknown wake reason")
            return
        invocation.return_value(None)
        GLib.idle_add(lambda: (self.wake(reason), False)[1])

    # -- wake events ---------------------------------------------------------
    def wake(self, reason: str) -> None:
        now = time.monotonic()
        # Resume and the network returning usually arrive together, and both
        # the service and this process may report them; one within a few
        # seconds carries no news.
        if reason in {"network", "resume", "unlock"} and now - self._last_wake_at.get(reason, -60.0) < 5:
            return
        self._last_wake_at[reason] = now
        self.last_wake = (reason, int(time.time()))
        LOG.info("%s woke: %s", self.info.agent_id, reason)
        try:
            self._wake(reason)
        except Exception:
            LOG.exception("%s wake handler failed for %s", self.info.agent_id, reason)

    def _watch_environment(self) -> None:
        connection = self.connection
        self._subscriptions.append((connection, connection.signal_subscribe(
            None, "org.gnome.ScreenSaver", "ActiveChanged", None, None, Gio.DBusSignalFlags.NONE,
            lambda *args: None if args[5].unpack()[0] else self.wake("unlock"))))
        if self.managed:
            return  # luma-background wakes the agent for the network and resume
        self._network = Gio.NetworkMonitor.get_default()
        self._network_available = self._network.get_network_available()
        self._network_handler = self._network.connect("network-changed", self._network_changed)
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return  # no system bus: no resume events
        self._subscriptions.append((system, system.signal_subscribe(
            "org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
            "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, self._sleep)))

    def _network_changed(self, _monitor, available) -> None:
        if available and not self._network_available:
            self.wake("network")
        self._network_available = available

    def _sleep(self, _connection, _sender, _path, _interface, _signal, parameters, *_user) -> None:
        if not parameters.unpack()[0]:
            # Give the network a moment to come back before reconnecting.
            GLib.timeout_add_seconds(2, lambda: (self.wake("resume"), False)[1])


def _bridge_class(kit):
    """A luma_appkit.background Agent that hands its hooks to an AgentPublisher."""

    class Bridge(kit.Agent):
        def __init__(self, publisher: AgentPublisher, *, connection: Gio.DBusConnection) -> None:
            type(self).app_id = publisher.info.app_id
            type(self).agent_id = publisher.info.agent_id
            self.publisher = publisher
            super().__init__(connection=connection)

        def on_start(self) -> None:
            self.publisher._acquired()

        def on_wake(self, wake) -> None:
            self.publisher.wake(wake.reason)

        def on_stop(self) -> None:
            pass  # AgentPublisher.release ran the agent's stop first

        def _name_lost(self, connection, name) -> None:
            started = self.started
            super()._name_lost(connection, name)
            if not started:
                self.publisher._lost(connection, name)

    return Bridge


class Agent(AgentPublisher):
    """Runs one agent process: single instance, published values, wakes."""

    def __init__(self, info: AgentInfo, *, start: Callable[["Agent"], None],
                 wake: Callable[[str], None] | None = None, stop: Callable[[], None] | None = None,
                 bus_type: Gio.BusType = Gio.BusType.SESSION, kit=None) -> None:
        super().__init__(info, start=start, wake=wake, stop=stop, lost=self._name_lost, kit=kit)
        self.bus_type = bus_type
        self.loop = GLib.MainLoop()
        self.exit_code = 0

    def run(self, argv: list[str] | None = None) -> int:
        arguments = list(sys.argv if argv is None else argv)
        try:
            self.connection = Gio.bus_get_sync(self.bus_type, None)
        except GLib.Error as error:
            LOG.error("No session bus for %s: %s", self.info.agent_id, error.message)
            return 1
        if "--autostart" in arguments[1:] and not self.managed and service_present(self.connection):
            # The session's autostart is only the fallback: luma-background starts
            # allowed agents at login itself, under their limits.
            LOG.info("%s: luma-background is installed and starts this agent", self.info.agent_id)
            return 0
        self.own(self.connection)
        def terminate() -> bool:
            self.loop.quit()
            return GLib.SOURCE_CONTINUE  # removed below, once the loop has ended
        sources = [GLib.unix_signal_add(GLib.PRIORITY_HIGH, number, terminate)
                   for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)]
        try:
            self.loop.run()
        finally:
            for source in sources:
                GLib.source_remove(source)
            self.release()
            try:
                self.connection.flush_sync(None)
            except GLib.Error:
                pass
        return self.exit_code

    def _name_lost(self, started: bool) -> None:
        # Possibly before the loop runs (the name was already taken): quitting
        # from an idle callback reaches a loop that has started.
        GLib.idle_add(self.quit)

    def _failed(self) -> None:
        self.exit_code = 1
        GLib.idle_add(self.quit)

    def quit(self) -> bool:
        self.loop.quit()
        return GLib.SOURCE_REMOVE


def configure_logging() -> None:
    level = logging.DEBUG if os.environ.get("LUMA_AGENT_DEBUG") else logging.INFO
    logging.basicConfig(level=level, format="%(name)s: %(message)s")


class BackgroundPortalRequest:
    """Ask org.freedesktop.portal.Background for autostart (Flatpak agents)."""

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection

    def request(self, commandline: list[str], reason: str, callback: Callable[[bool, bool], None]) -> None:
        token = f"luma_agent_{uuid.uuid4().hex[:16]}"
        sender = (self.connection.get_unique_name() or "").lstrip(":").replace(".", "_")
        handle = f"{PORTAL_PATH}/request/{sender}/{token}"
        holder = [0]

        def response(connection, _sender, _path, _interface, _signal, parameters, *_user):
            connection.signal_unsubscribe(holder[0])
            code, results = parameters.unpack()
            callback(code == 0 and bool(results.get("background")), bool(results.get("autostart")))

        holder[0] = self.connection.signal_subscribe(None, "org.freedesktop.portal.Request", "Response", handle,
                                                     None, Gio.DBusSignalFlags.NONE, response)
        options = {"handle_token": GLib.Variant("s", token), "reason": GLib.Variant("s", reason),
                   "autostart": GLib.Variant("b", True), "commandline": GLib.Variant("as", commandline),
                   "dbus-activatable": GLib.Variant("b", False)}

        def done(connection, result, *_user):
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                connection.signal_unsubscribe(holder[0])
                LOG.warning("Background portal unavailable: %s", error.message)
                callback(False, False)

        self.connection.call(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.portal.Background", "RequestBackground",
                             GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
                             Gio.DBusCallFlags.NONE, -1, None, done)


__all__ = [
    "AGENT_INTERFACE", "AGENT_PATH", "Agent", "AgentInfo", "AgentPublisher", "BackgroundPortalRequest", "CATEGORIES",
    "DECLARATION_DIRECTORY", "Notifier", "Schedule", "SystemdUser", "WAKE_EVENTS", "WAKE_REASONS", "agent_allowed",
    "configure_logging", "ensure_agent", "is_managed", "is_sandboxed", "launch_app", "load_kit", "name_activatable",
    "name_has_owner", "resident_bytes", "service_present", "variant",
]
