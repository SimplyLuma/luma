# SPDX-License-Identifier: Apache-2.0
"""Mutter's DisplayConfig interface: read what is plugged in, apply a layout."""
from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib, GObject

from . import model

VERIFY, TEMPORARY, PERSISTENT = 0, 1, 2


class DisplayConfig(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self) -> None:
        super().__init__()
        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
            "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig", None)
        self.proxy.connect("g-signal", self._signal)

    def _signal(self, _proxy, _sender, name, _parameters) -> None:
        if name == "MonitorsChanged":
            self.emit("changed")

    def state(self) -> model.State:
        value = self.proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, 5000, None)
        return model.parse_state(value.unpack())

    def apply(self, state: model.State, layout: model.Layout, method: int) -> None:
        """Raises GLib.Error, worded by Mutter, when the layout is refused."""
        properties = {}
        if state.can_change_layout_mode:
            properties["layout-mode"] = GLib.Variant("u", state.layout_mode)
        logical = [(x, y, scale, transform, primary, [(c, m, {}) for c, m, _p in members])
                   for x, y, scale, transform, primary, members in model.apply_arguments(state, layout)]
        parameters = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (state.serial, method, logical, properties))
        self.proxy.call_sync("ApplyMonitorsConfig", parameters, Gio.DBusCallFlags.NONE, 10000, None)
