# SPDX-License-Identifier: MPL-2.0
"""org.projectluma.Background1: the service on the session bus."""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import (  # noqa: E402
    AGENT_INTERFACE, AGENT_OBJECT_PATH, BUS_NAME, ERROR_PREFIX, INTERFACE, INTERFACE_VERSION,
    OBJECT_PATH, __version__, ids, journal,
)
from .identity import Caller, IdentityError, IdentityResolver  # noqa: E402
from .login import LoginItemError, LoginItems  # noqa: E402
from .manager import Manager, NotFound, Refused  # noqa: E402
from .policy import SYSTEM_DEFAULTS, PolicyStore, load_defaults  # noqa: E402
from .portal import BACKEND_NAME, BackgroundPortal, PermissionStoreMirror  # noqa: E402
from .prompt import NotificationPrompt  # noqa: E402
from .registry import Layout, Registry, NATIVE_SERVICE_APPS, native_service_role  # noqa: E402
from .state import StateStore  # noqa: E402
from .systemd import SystemdClient  # noqa: E402
from .wakes import NetworkSource, PowerSaverSource, ResumeSource  # noqa: E402

INTERFACE_XML = """
<node>
  <interface name="org.projectluma.Background1">
    <method name="ListAgents">
      <arg name="agents" type="aa{sv}" direction="out"/>
    </method>
    <method name="GetAgent">
      <arg name="app_id" type="s" direction="in"/>
      <arg name="agent" type="a{sv}" direction="out"/>
    </method>
    <method name="SetAllowed">
      <arg name="app_id" type="s" direction="in"/>
      <arg name="allowed" type="b" direction="in"/>
    </method>
    <method name="StopNow">
      <arg name="app_id" type="s" direction="in"/>
    </method>
    <method name="RequestBackground">
      <arg name="parent_window" type="s" direction="in"/>
      <arg name="options" type="a{sv}" direction="in"/>
      <arg name="response" type="u" direction="out"/>
      <arg name="results" type="a{sv}" direction="out"/>
    </method>
    <method name="RequestForeground">
      <arg name="ready" type="b" direction="out"/>
    </method>
    <method name="ReleaseForeground"/>
    <method name="Schedule">
      <arg name="name" type="s" direction="in"/>
      <arg name="options" type="a{sv}" direction="in"/>
    </method>
    <method name="Unschedule">
      <arg name="name" type="s" direction="in"/>
    </method>
    <method name="Wake">
      <arg name="app_id" type="s" direction="in"/>
      <arg name="reason" type="s" direction="in"/>
    </method>
    <method name="ListLoginItems">
      <arg name="items" type="aa{sv}" direction="out"/>
    </method>
    <method name="GetLoginItem">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="item" type="a{sv}" direction="out"/>
    </method>
    <method name="SetLoginItem">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="enabled" type="b" direction="in"/>
      <arg name="item" type="a{sv}" direction="out"/>
    </method>
    <signal name="LoginItemsChanged"/>
    <signal name="AgentsChanged"/>
    <signal name="AgentChanged">
      <arg name="app_id" type="s"/>
      <arg name="agent" type="a{sv}"/>
    </signal>
    <property name="Version" type="u" access="read"/>
    <property name="PowerSaver" type="b" access="read"/>
  </interface>
</node>
"""

_VARIANT_TYPES = {bool: "b", int: None, float: "d", str: "s"}
_INT_KEYS = {"last-run": "x", "restarts": "u", "memory": "t", "memory-peak": "t",
             "cpu-usec": "t", "memory-high": "t", "memory-max": "t"}


def agent_variant(agent: dict) -> dict[str, GLib.Variant]:
    out: dict[str, GLib.Variant] = {}
    for key, value in agent.items():
        if key in _INT_KEYS:
            out[key] = GLib.Variant(_INT_KEYS[key], max(0, int(value)) if _INT_KEYS[key] != "x" else int(value))
        elif isinstance(value, bool):
            out[key] = GLib.Variant("b", value)
        elif isinstance(value, float):
            out[key] = GLib.Variant("d", value)
        elif isinstance(value, list):
            out[key] = GLib.Variant("as", [str(item) for item in value])
        else:
            out[key] = GLib.Variant("s", str(value))
    return out


class AgentBus:
    """Wakes for agents, sent only to the name the declaration gave them."""

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection

    def deliver_wake(self, agent: str, reason: str, details: dict, done) -> None:
        payload = {}
        for key, value in details.items():
            if isinstance(value, bool):
                payload[key] = GLib.Variant("b", value)
            elif isinstance(value, str):
                payload[key] = GLib.Variant("s", value)

        def finished(connection, result) -> None:
            try:
                connection.call_finish(result)
            except GLib.Error:
                done(False)
                return
            done(True)

        self.connection.call(agent, AGENT_OBJECT_PATH, AGENT_INTERFACE, "Wake",
                             GLib.Variant("(sa{sv})", (reason, payload)), None,
                             Gio.DBusCallFlags.NO_AUTO_START, 30_000, None, finished)


class BackgroundService:
    def __init__(self, connection: Gio.DBusConnection, *, layout: Layout | None = None,
                 system_bus: Gio.DBusConnection | None = None) -> None:
        self.connection = connection
        environment_trusted = tuple(
            item for item in os.environ.get("LUMA_BACKGROUND_EXTRA_TRUSTED_EXECUTABLES", "").split(":")
            if item)
        self.identity = IdentityResolver(connection, extra_trusted=environment_trusted)
        state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.layout = layout or Layout.from_environment()
        self.policy = PolicyStore(config_home / "luma-background/policy.json", load_defaults(SYSTEM_DEFAULTS))
        self.state = StateStore(state_home / "luma-background/state.json")
        self.systemd = SystemdClient(connection, on_unit_changed=self._unit_changed)
        self.prompt = NotificationPrompt(connection)
        self.permissions = PermissionStoreMirror(connection, self._permission_changed)
        self.manager = Manager(
            Registry(self.layout), self.policy, self.state, self.systemd, AgentBus(connection),
            self.permissions, self.prompt, on_changed=self._changed,
        )
        self._changed_apps: set[str] = set()
        self._changed_all = False
        self._emit_source = 0
        self._rescan_source = 0
        self._monitors: list[Gio.FileMonitor] = []
        node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        connection.register_object(OBJECT_PATH, node.interfaces[0], self._call, self._get_property, None)
        self.login_items = LoginItems(self.layout, lambda: self.manager.agents, self._agent_allowed)
        self._login_source = 0
        self.portal = BackgroundPortal(connection, self.manager.portal_notify_background,
                                       self.manager.cancel_prompt)
        self.system_bus = system_bus
        self._sources = []
        self._foreground_subscription = connection.signal_subscribe(
            'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, self._owner_changed)

    # -- Startup -------------------------------------------------------------

    def prepare(self, then) -> None:
        """Units and autostart masks written and loaded, before the bus name is taken."""
        self.manager.prepare(then)

    def start(self) -> None:
        for app, value in self.permissions.load().items():
            self.manager.portal_apps.add(app)
            decision = self.policy.decision(app).value
            wanted = {"yes": "allow", "no": "deny"}.get(value, "")
            if wanted and decision != wanted:
                # A change made through the portal's own tools while this
                # service was not running is still the person's decision.
                self.policy.set_decision(app, wanted == "allow", "portal")
        if self.system_bus is not None:
            power = PowerSaverSource(self.system_bus, self.manager.set_power_saver)
            self._sources += [
                NetworkSource(self.system_bus, lambda: self.manager.wake_all("network")),
                ResumeSource(self.system_bus, self._resumed),
                power,
            ]
            if power.active:
                self.manager.set_power_saver(True)
        self.manager.refresh(login=True)
        self._watch()
        journal.send(f"luma-background {__version__} is running with {len(self.manager.agents)} "
                     "background agent(s)", event="started")

    def _resumed(self) -> None:
        self.manager.wake_all("resume")

    def _watch(self) -> None:
        for directory in self.layout.watch_directories():
            target = directory
            # Watch the nearest existing parent, so a directory created later
            # (the first Flatpak, the first autostart entry) is still noticed.
            while not target.exists() and target.parent != target:
                target = target.parent
            try:
                monitor = Gio.File.new_for_path(str(target)).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error:
                continue
            monitor.connect("changed", self._files_changed)
            self._monitors.append(monitor)

    def _agent_allowed(self, app: str) -> bool:
        try:
            return bool(self.manager.describe(app)["allowed"])
        except NotFound:
            return False

    def _login_items_changed(self) -> None:
        if not self._login_source:
            self._login_source = GLib.timeout_add(150, self._emit_login_items)

    def _emit_login_items(self) -> bool:
        self._login_source = 0
        self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "LoginItemsChanged", None)
        return GLib.SOURCE_REMOVE

    def _files_changed(self, *_args) -> None:
        self._login_items_changed()
        if self._rescan_source:
            GLib.source_remove(self._rescan_source)
        self._rescan_source = GLib.timeout_add(1500, self._rescan)

    def _rescan(self) -> bool:
        self._rescan_source = 0
        self.manager.refresh()
        return GLib.SOURCE_REMOVE

    # -- Events --------------------------------------------------------------

    def _unit_changed(self, unit: str) -> None:
        if ids.parse_agent_unit(unit):
            self.manager.unit_changed(unit)

    def _permission_changed(self, app: str, value: str, previous: str) -> None:
        self.manager.portal_permission_changed(app, value, previous)

    def _changed(self, app: str | None) -> None:
        if app is None:
            self._changed_all = True
        else:
            self._changed_apps.add(app)
        if not self._emit_source:
            self._emit_source = GLib.timeout_add(150, self._emit)

    def _emit(self) -> bool:
        self._emit_source = 0
        # An agent's allow decision is the login switch for a portal autostart entry.
        self._login_items_changed()
        apps, self._changed_apps = self._changed_apps, set()
        everything, self._changed_all = self._changed_all, False
        if everything:
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "AgentsChanged", None)
            apps |= set(self.manager.agents)
        for app in sorted(apps):
            if app in self.manager.agents:
                description = agent_variant(self.manager.describe(app, reader="signal"))
                self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "AgentChanged",
                                            GLib.Variant("(sa{sv})", (app, description)))
        return GLib.SOURCE_REMOVE

    # -- Interface -----------------------------------------------------------

    def _get_property(self, _connection, _sender, _path, _interface, name):
        if name == "Version":
            return GLib.Variant("u", INTERFACE_VERSION)
        if name == "PowerSaver":
            return GLib.Variant("b", self.manager.power_saver)
        return None

    @staticmethod
    def _error(invocation, name: str, message: str) -> None:
        invocation.return_dbus_error(ERROR_PREFIX + name, message)

    def _call(self, _connection, sender, _path, _interface, method, parameters, invocation) -> None:
        try:
            handler = getattr(self, f"_method_{method}")
        except AttributeError:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        try:
            caller = self.identity.resolve(sender)
        except (IdentityError, GLib.Error) as error:
            self._error(invocation, "NotAuthorized", str(error) or "caller could not be identified")
            return
        try:
            handler(caller, parameters, invocation)
        except NotFound as error:
            self._error(invocation, "NotFound", f"no background agent for {error}")
        except (Refused, IdentityError) as error:
            self._error(invocation, "NotAuthorized", str(error))
        except (ValueError, ids.InvalidIdentifier) as error:
            self._error(invocation, "InvalidArgument", str(error))

    def _method_ListAgents(self, caller: Caller, _parameters, invocation) -> None:
        agents = [agent_variant(item) for item in self.manager.list(reader=caller.sender)]
        invocation.return_value(GLib.Variant("(aa{sv})", (agents,)))

    def _method_GetAgent(self, caller: Caller, parameters, invocation) -> None:
        (app,) = parameters.unpack()
        description = agent_variant(self.manager.describe(ids.app_id(app), reader=caller.sender))
        invocation.return_value(GLib.Variant("(a{sv})", (description,)))

    def _method_SetAllowed(self, caller: Caller, parameters, invocation) -> None:
        app, allowed = parameters.unpack()
        ids.app_id(app)
        if caller.sandboxed and app in NATIVE_SERVICE_APPS:
            if caller.app_id != app:
                raise Refused('an application can change only its own background decision')
            self._native_service_caller(caller)
        if self.identity.is_trusted_host(caller):
            source = "dock" if caller.executable in {"/usr/bin/gnome-shell", "/usr/bin/phosh", "/usr/libexec/phosh"} else "settings"
        elif not allowed and caller.app_id == app:
            source = "app"
        else:
            raise Refused("only Settings, the dock, or the person answering the prompt can allow an app")
        self.manager.set_allowed(app, bool(allowed), source)
        invocation.return_value(None)

    def _method_ListLoginItems(self, _caller: Caller, _parameters, invocation) -> None:
        items = [agent_variant(item) for item in self.login_items.list()]
        invocation.return_value(GLib.Variant("(aa{sv})", (items,)))

    def _method_GetLoginItem(self, _caller: Caller, parameters, invocation) -> None:
        (desktop_id,) = parameters.unpack()
        try:
            item = self.login_items.describe(desktop_id)
        except LoginItemError as error:
            raise ValueError(str(error)) from error
        invocation.return_value(GLib.Variant("(a{sv})", (agent_variant(item),)))

    def _method_SetLoginItem(self, caller: Caller, parameters, invocation) -> None:
        desktop_id, enabled = parameters.unpack()
        if not self.identity.is_trusted_host(caller):
            raise Refused("only Settings or the dock can change what opens at login")
        source = "dock" if caller.executable in {"/usr/bin/gnome-shell", "/usr/bin/phosh", "/usr/libexec/phosh"} else "settings"
        try:
            item = self.login_items.set_enabled(
                desktop_id, bool(enabled),
                set_agent_allowed=lambda app, allowed: self.manager.set_allowed(app, allowed, source))
        except LoginItemError as error:
            raise ValueError(str(error)) from error
        except OSError as error:
            self._error(invocation, "Failed", f"could not change {desktop_id}: {error}")
            return
        journal.send(f"{item['name']} {'opens' if item['enabled'] else 'does not open'} at login "
                     f"(decided in {source})", app_id=desktop_id, event="login-item",
                     LUMA_BACKGROUND_SOURCE=source)
        self._login_items_changed()
        invocation.return_value(GLib.Variant("(a{sv})", (agent_variant(item),)))

    def _method_StopNow(self, caller: Caller, parameters, invocation) -> None:
        (app,) = parameters.unpack()
        ids.app_id(app)
        if not (self.identity.is_trusted_host(caller) or caller.app_id == app):
            raise Refused("only Settings, the dock, or the app itself can stop an agent")
        if caller.sandboxed and app in NATIVE_SERVICE_APPS:
            self._native_service_caller(caller)
        self.manager.stop_now(app)
        invocation.return_value(None)

    def _native_service_caller(self, caller: Caller) -> str:
        if not caller.sandboxed:
            raise Refused('this interface requires an installed application sandbox')
        try:
            from luma_installer.app_data_broker import authenticate
            authenticated = authenticate(self.connection, caller.sender)
            if authenticated != caller.app_id or not native_service_role(authenticated):
                raise ValueError('no native service role')
            self.manager.refresh()
            record = self.manager.record(authenticated)
            if record.origin != 'native' or not record.trusted_install or not record.has_unit:
                raise ValueError('native service is unavailable')
        except Exception:
            raise Refused('this application cannot request a native service') from None
        return authenticated

    def _owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        name, _old, new = parameters.unpack()
        if name.startswith(':') and not new:
            self.manager.release_foreground(name)

    def _method_RequestForeground(self, caller: Caller, _parameters, invocation) -> None:
        app = self._native_service_caller(caller)
        def ready(ok: bool) -> None:
            # Authentication/systemd startup can outlast a disconnect. Check
            # the daemon's live unique name before granting the lease.
            try:
                alive = self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                    'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (caller.sender,)),
                    GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
            except GLib.Error:
                alive = False
            if not alive:
                self.manager.release_foreground(caller.sender, app)
            invocation.return_value(GLib.Variant('(b)', (bool(ok and alive),)))
        self.manager.acquire_foreground(app, caller.sender, ready)

    def _method_ReleaseForeground(self, caller: Caller, _parameters, invocation) -> None:
        # A caller can release only its own unique-name lease. No admission is
        # needed to stop work; disconnect is the equivalent fallback.
        self.manager.release_foreground(caller.sender)
        invocation.return_value(None)

    def _method_RequestBackground(self, caller: Caller, parameters, invocation) -> None:
        _parent_window, options = parameters.unpack()
        if caller.sandboxed:
            # The four native core services remain managed on the host while
            # their UIs update independently. Third-party agents still use the
            # Background portal. Never accept an app ID or command from args.
            self._native_service_caller(caller)
        if not caller.app_id:
            raise Refused("the caller is not running as an identifiable application")
        reason = options.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 256:
            raise ValueError("reason must be a string of at most 256 characters")
        autostart = bool(options.get("autostart", True))
        if caller.app_id not in self.manager.agents:
            self.manager.refresh()

        def reply(response: int, results: dict) -> None:
            invocation.return_value(GLib.Variant("(ua{sv})", (response, {
                "background": GLib.Variant("b", bool(results.get("background"))),
                "autostart": GLib.Variant("b", bool(results.get("autostart"))),
            })))

        self.manager.request_background(caller.app_id, reason=reason, autostart=autostart, reply=reply)

    def _schedule_owner(self, caller: Caller) -> str:
        if not caller.app_id:
            raise Refused("schedules belong to an app; the caller is not one")
        return caller.app_id

    def _method_Schedule(self, caller: Caller, parameters, invocation) -> None:
        name, options = parameters.unpack()
        app = self._schedule_owner(caller)
        at = options.get("at", 0)
        every = options.get("every", 0)
        accuracy = options.get("accuracy")
        if not isinstance(at, int) or not isinstance(every, int):
            raise ValueError("at and every must be integers")
        self.manager.schedule(app, name, at=int(at), every=int(every),
                              accuracy=int(accuracy) if isinstance(accuracy, int) else None)
        invocation.return_value(None)

    def _method_Unschedule(self, caller: Caller, parameters, invocation) -> None:
        (name,) = parameters.unpack()
        self.manager.unschedule(self._schedule_owner(caller), name)
        invocation.return_value(None)

    def _method_Wake(self, caller: Caller, parameters, invocation) -> None:
        app, reason = parameters.unpack()
        ids.app_id(app)
        if reason.startswith("schedule:"):
            name = reason.split(":", 1)[1]
            owner = ids.parse_schedule_unit(caller.unit)
            if owner != (app, name) and not self.identity.is_trusted_host(caller):
                raise Refused("scheduled wakes come from the app's own timer")
            self.manager.scheduled_wake(app, name)
        else:
            if not self.identity.is_trusted_host(caller):
                raise Refused("only Settings and the dock can wake an agent")
            if reason not in {"request", "network", "resume", "login"}:
                raise ValueError(f"unknown wake reason: {reason}")
            if not self.manager.wake(app, reason, {}):
                raise Refused("the agent is not allowed to run, or does not wake for that")
        invocation.return_value(None)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    loop = GLib.MainLoop()
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as error:
        print(f"luma-background: no session bus: {error.message}", file=sys.stderr)
        return 1
    try:
        system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        system_bus = None
    service = BackgroundService(connection, system_bus=system_bus)
    owned = {"main": False, "requested": False}

    def acquired(_connection, name) -> None:
        if name == BUS_NAME and not owned["main"]:
            owned["main"] = True
            service.start()

    def lost(_connection, name) -> None:
        journal.send(f"Lost the bus name {name}; exiting", priority="error", event="stopped")
        loop.quit()

    def ready() -> bool:
        # Taking the name is what tells systemd the service is up, and the
        # session's autostart is ordered after that: by now every mask over an
        # autostart entry an agent replaces is loaded (Manager.prepare).
        if owned["requested"]:
            return GLib.SOURCE_REMOVE
        owned["requested"] = True
        Gio.bus_own_name_on_connection(connection, BACKEND_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        Gio.bus_own_name_on_connection(connection, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                                       acquired, lost)
        return GLib.SOURCE_REMOVE

    # A user manager that never answers the reload must not keep the session
    # without the service: after 20 s it starts anyway, and says so.
    def prepare_timeout() -> bool:
        if not owned["requested"]:
            journal.send("The user manager did not reload in time; starting without waiting",
                         priority="warning", event="prepare-timeout")
            ready()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add_seconds(20, prepare_timeout)
    service.prepare(ready)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    loop.run()
    service.state.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
