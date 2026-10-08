# SPDX-License-Identifier: MPL-2.0
"""The system events agents are woken for, and the one they are paused for.

Each source listens to the service that owns the fact -- NetworkManager for
connectivity, logind for sleep, power-profiles-daemon for Power Saver -- and
reports only transitions, debounced, so agents are woken once per event and
not once per signal.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import journal  # noqa: E402

NM_STATE_CONNECTED_GLOBAL = 70
NETWORK_DEBOUNCE_SECONDS = 3
RESUME_DELAY_SECONDS = 2


class NetworkSource:
    """Online means NetworkManager's global connectivity, not a link coming up."""

    def __init__(self, bus: Gio.DBusConnection, online: Callable[[], None]) -> None:
        self._online = online
        self._state = 0
        self._debounce = 0
        self._monitor = None
        self._bus = bus
        bus.signal_subscribe("org.freedesktop.NetworkManager", "org.freedesktop.NetworkManager",
                             "StateChanged", "/org/freedesktop/NetworkManager", None,
                             Gio.DBusSignalFlags.NONE, self._state_changed)
        try:
            value = bus.call_sync(
                "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager",
                "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.freedesktop.NetworkManager", "State")),
                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NO_AUTO_START, 3_000, None)
            self._state = int(value.unpack()[0])
        except GLib.Error:
            # No NetworkManager (a container, another network stack): fall
            # back to GLib's monitor, which reads the kernel's routes.
            self._monitor = Gio.NetworkMonitor.get_default()
            self._monitor.connect("network-changed", self._monitor_changed)
            self._state = NM_STATE_CONNECTED_GLOBAL if self._full() else 0

    @property
    def online(self) -> bool:
        return self._state >= NM_STATE_CONNECTED_GLOBAL

    def _full(self) -> bool:
        return bool(self._monitor and self._monitor.get_network_available()
                    and self._monitor.get_connectivity() == Gio.NetworkConnectivity.FULL)

    def _transition(self, state: int) -> None:
        was_online = self.online
        self._state = state
        if self.online and not was_online:
            if self._debounce:
                GLib.source_remove(self._debounce)
            self._debounce = GLib.timeout_add_seconds(NETWORK_DEBOUNCE_SECONDS, self._fire)
        elif not self.online and self._debounce:
            GLib.source_remove(self._debounce)
            self._debounce = 0

    def _fire(self) -> bool:
        self._debounce = 0
        if self.online:
            journal.send("The network is online", event="wake-network")
            self._online()
        return GLib.SOURCE_REMOVE

    def _state_changed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        self._transition(int(parameters.unpack()[0]))

    def _monitor_changed(self, _monitor, _available) -> None:
        self._transition(NM_STATE_CONNECTED_GLOBAL if self._full() else 0)


class ResumeSource:
    def __init__(self, bus: Gio.DBusConnection, resumed: Callable[[], None]) -> None:
        self._resumed = resumed
        bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                             "PrepareForSleep", "/org/freedesktop/login1", None,
                             Gio.DBusSignalFlags.NONE, self._prepare_for_sleep)

    def _prepare_for_sleep(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        (starting,) = parameters.unpack()
        if starting:
            return
        # Give the network and the clock a moment to come back before agents
        # rush to use them.
        GLib.timeout_add_seconds(RESUME_DELAY_SECONDS, self._fire)

    def _fire(self) -> bool:
        journal.send("The computer resumed from sleep", event="wake-resume")
        self._resumed()
        return GLib.SOURCE_REMOVE


class PowerSaverSource:
    CANDIDATES = (
        ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
        ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"),
    )

    def __init__(self, bus: Gio.DBusConnection, changed: Callable[[bool], None]) -> None:
        self._changed = changed
        self.active = False
        self._subscriptions = []
        for name, path in self.CANDIDATES:
            self._subscriptions.append(bus.signal_subscribe(
                name, "org.freedesktop.DBus.Properties", "PropertiesChanged", path, name,
                Gio.DBusSignalFlags.NONE, self._properties_changed))
        for name, path in self.CANDIDATES:
            try:
                value = bus.call_sync(name, path, "org.freedesktop.DBus.Properties", "Get",
                                      GLib.Variant("(ss)", (name, "ActiveProfile")),
                                      GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE,
                                      3_000, None)
            except GLib.Error:
                continue
            self.active = value.unpack()[0] == "power-saver"
            break

    def _properties_changed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        _name, changed, _invalidated = parameters.unpack()
        if "ActiveProfile" not in changed:
            return
        active = changed["ActiveProfile"] == "power-saver"
        if active != self.active:
            self.active = active
            self._changed(active)
