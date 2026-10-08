# SPDX-License-Identifier: MPL-2.0
"""The session service: the compositor talks to this, and the person's Settings reads it."""
from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import policy  # noqa: E402
from .claims import Claims  # noqa: E402
from .manager import Manager  # noqa: E402

BUS_NAME = "org.projectluma.Energy1"
OBJECT_PATH = "/org/projectluma/Energy1"

#: How often the ladder is re-judged. Slow on purpose: this service exists to
#: save power and a busy poller would spend more than it saves.
TICK_SECONDS = 10

INTERFACE_XML = """
<node>
  <interface name="org.projectluma.Energy1">
    <!-- The whole picture, every time, from the compositor. -->
    <method name="SetAppStates">
      <arg type="a(ss)" name="states" direction="in"/>
    </method>
    <!-- Wake one application, or all of them, before the person notices. -->
    <method name="Thaw">
      <arg type="s" name="cgroup" direction="in"/>
      <arg type="b" name="woken" direction="out"/>
    </method>
    <method name="ThawAll">
      <arg type="u" name="woken" direction="out"/>
    </method>
    <!-- What Luma is doing and why, for Settings and for Vitals. -->
    <method name="ListApplications">
      <arg type="aa{sv}" name="applications" direction="out"/>
    </method>
    <!-- The person's own "keep running in the background". -->
    <method name="SetKeepRunning">
      <arg type="s" name="cgroup" direction="in"/>
      <arg type="b" name="keep" direction="in"/>
    </method>
    <property name="Version" type="u" access="read"/>
    <property name="Enabled" type="b" access="readwrite"/>
    <property name="Freezing" type="b" access="read"/>
    <property name="OnBattery" type="b" access="read"/>
    <signal name="ApplicationsChanged"/>
  </interface>
</node>
"""


class EnergyService:
    def __init__(self, *, freezing: bool = False) -> None:
        self.manager = Manager(freezing=freezing, claims=Claims())
        self.enabled = True
        self._registration = 0
        self._connection = None
        self._node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)

    # -- lifetime ----------------------------------------------------------

    def run(self) -> int:
        self._loop = GLib.MainLoop()
        Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                         Gio.BusNameOwnerFlags.NONE,
                         self._on_bus_acquired, None, self._on_name_lost)
        GLib.timeout_add_seconds(TICK_SECONDS, self._tick)
        self._watch_power()
        try:
            self._loop.run()
        finally:
            # Whatever ends this -- a stop, a crash, a logout -- the machine
            # goes back to behaving as though Luma Energy were never here.
            self.manager.release_all()
        return 0

    def _on_bus_acquired(self, connection, _name, _data=None):
        self._connection = connection
        self._registration = connection.register_object(
            OBJECT_PATH, self._node.interfaces[0],
            self._on_call, self._on_get, self._on_set)

    def _on_name_lost(self, _connection, _name, _data=None):
        self.manager.release_all()
        self._loop.quit()

    def _tick(self) -> bool:
        if self.enabled:
            self.manager.tick()
        return GLib.SOURCE_CONTINUE

    def _watch_power(self) -> None:
        """Follow the charger, through UPower, and give everything back on mains."""
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.UPower", "/org/freedesktop/UPower",
                "org.freedesktop.DBus.Properties", None)
        except GLib.Error:
            # Without UPower we cannot tell mains from battery. Assume mains,
            # which is the setting that does the least.
            self.manager.set_on_battery(False)
            return

        def read():
            try:
                value = proxy.call_sync(
                    "Get", GLib.Variant("(ss)", ("org.freedesktop.UPower", "OnBattery")),
                    Gio.DBusCallFlags.NONE, 2000, None)
                self.manager.set_on_battery(bool(value.unpack()[0]))
            except GLib.Error:
                pass

        read()
        proxy.connect("g-signal", lambda *_: read())
        GLib.timeout_add_seconds(30, lambda: (read(), GLib.SOURCE_CONTINUE)[1])

    # -- the interface -----------------------------------------------------

    def _on_call(self, _connection, _sender, _path, _interface, method,
                 parameters, invocation):
        if method == "SetAppStates":
            states = {cgroup: state for cgroup, state in parameters.unpack()[0]
                      if state in policy.ORDER}
            if self.enabled:
                self.manager.set_states(states)
            invocation.return_value(None)
            self._changed()
        elif method == "Thaw":
            woken = self.manager.thaw(parameters.unpack()[0]) if self.enabled else False
            invocation.return_value(GLib.Variant("(b)", (woken,)))
        elif method == "ThawAll":
            woken = self.manager.thaw_all() if self.enabled else 0
            invocation.return_value(GLib.Variant("(u)", (woken,)))
        elif method == "ListApplications":
            rows = [{key: GLib.Variant(_signature(value), value)
                     for key, value in row.items()}
                    for row in self.manager.describe()]
            invocation.return_value(GLib.Variant("(aa{sv})", (rows,)))
        elif method == "SetKeepRunning":
            cgroup, keep = parameters.unpack()
            if keep:
                self.manager.claims.keep_running.add(cgroup)
                # The person said so: give it back immediately, not at the
                # next tick.
                self.manager.thaw(cgroup)
            else:
                self.manager.claims.keep_running.discard(cgroup)
            invocation.return_value(None)
            self._changed()
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)

    def _on_get(self, _connection, _sender, _path, _interface, name):
        if name == "Version":
            return GLib.Variant("u", 1)
        if name == "Enabled":
            return GLib.Variant("b", self.enabled)
        if name == "Freezing":
            return GLib.Variant("b", self.manager.freezing)
        if name == "OnBattery":
            return GLib.Variant("b", self.manager.on_battery)
        return None

    def _on_set(self, _connection, _sender, _path, _interface, name, value):
        if name != "Enabled":
            return False
        self.enabled = bool(value.unpack())
        if not self.enabled:
            # Off means off, at once and completely.
            self.manager.release_all()
        self._changed()
        return True

    def _changed(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(
                None, OBJECT_PATH, BUS_NAME, "ApplicationsChanged", None)
        except GLib.Error:
            pass


def _signature(value: object) -> str:
    if isinstance(value, bool):
        return "b"
    if isinstance(value, int):
        return "u"
    if isinstance(value, dict):
        return "a{si}"
    if isinstance(value, list):
        return "as"
    return "s"


def main(argv: list[str] | None = None) -> int:
    import os
    freezing = os.environ.get("LUMA_ENERGY_FREEZING", "").strip().lower() in ("1", "on", "true")
    return EnergyService(freezing=freezing).run()
