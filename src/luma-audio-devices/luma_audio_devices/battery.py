# SPDX-License-Identifier: MPL-2.0
"""Battery level of Bluetooth sound outputs, from UPower.

UPower publishes a device per Bluetooth headset that reports its battery
(BlueZ Battery1), with the Bluetooth address as its serial. ``Batteries``
follows those by signals once a Bluetooth output exists; nothing is read or
watched before that, and nothing polls.
"""

from __future__ import annotations

import logging

__all__ = ("Batteries",)

log = logging.getLogger("luma-audio-devices")

UPOWER = "org.freedesktop.UPower"
UPOWER_PATH = "/org/freedesktop/UPower"
DEVICE_INTERFACE = "org.freedesktop.UPower.Device"


class Batteries:
    def __init__(self, on_changed) -> None:
        self._on_changed = on_changed
        self._bus = None
        self._subscriptions: list[int] = []
        self._levels: dict[str, tuple[str, int]] = {}   # device path -> (ADDRESS, percent)

    def percent(self, address: str) -> int | None:
        address = (address or "").upper()
        for known, percent in self._levels.values():
            if known == address:
                return percent
        return None

    def start(self) -> None:
        """Idempotent; called when the first Bluetooth output appears."""
        from gi.repository import Gio, GLib
        if self._bus is not None:
            return
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as error:
            log.debug("no system bus for battery levels: %s", error.message)
            return
        bus = self._bus
        self._subscriptions = [
            bus.signal_subscribe(UPOWER, UPOWER, "DeviceAdded", UPOWER_PATH, None, Gio.DBusSignalFlags.NONE,
                                 lambda *a: self._read(a[5].unpack()[0])),
            bus.signal_subscribe(UPOWER, UPOWER, "DeviceRemoved", UPOWER_PATH, None, Gio.DBusSignalFlags.NONE,
                                 lambda *a: self._forget(a[5].unpack()[0])),
            bus.signal_subscribe(UPOWER, "org.freedesktop.DBus.Properties", "PropertiesChanged", None,
                                 DEVICE_INTERFACE, Gio.DBusSignalFlags.NONE,
                                 lambda *a: self._read(a[2]) if a[2] in self._levels else None),
        ]

        def listed(_bus, result) -> None:
            try:
                (paths,) = bus.call_finish(result).unpack()
            except GLib.Error as error:
                log.debug("UPower does not answer: %s", error.message)
                return
            for path in paths:
                self._read(path)

        bus.call(UPOWER, UPOWER_PATH, UPOWER, "EnumerateDevices", None, GLib.VariantType.new("(ao)"),
                 Gio.DBusCallFlags.NONE, 3000, None, listed)

    def stop(self) -> None:
        if self._bus is not None:
            for subscription in self._subscriptions:
                self._bus.signal_unsubscribe(subscription)
        self._subscriptions = []
        self._bus = None
        self._levels = {}

    def _forget(self, path: str) -> None:
        if self._levels.pop(path, None) is not None:
            self._on_changed()

    def _read(self, path: str) -> None:
        from gi.repository import Gio, GLib
        bus = self._bus
        if bus is None:
            return

        def done(_bus, result) -> None:
            try:
                (properties,) = bus.call_finish(result).unpack()
            except GLib.Error:
                return
            serial = str(properties.get("Serial") or "").upper()
            native = str(properties.get("NativePath") or "")
            percent = properties.get("Percentage")
            if not ("bluez" in native or len(serial.split(":")) == 6) or not isinstance(percent, (int, float)):
                return
            entry = (serial, int(round(percent)))
            if self._levels.get(path) != entry:
                self._levels[path] = entry
                self._on_changed()

        bus.call(UPOWER, path, "org.freedesktop.DBus.Properties", "GetAll",
                 GLib.Variant("(s)", (DEVICE_INTERFACE,)), GLib.VariantType.new("(a{sv})"),
                 Gio.DBusCallFlags.NONE, 3000, None, done)
