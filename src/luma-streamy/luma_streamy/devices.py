# SPDX-License-Identifier: Apache-2.0
"""Microphones, speakers and their monitors, as GStreamer's device monitor sees them, kept current.

The monitor runs on a thread of its own (starting it asks PipeWire and can take a moment);
the window hears ``on_changed()`` on its own thread whenever a device comes, goes or becomes
the default. A device is named by its node name (what pulsesrc's ``device`` and pipewiresrc's
``target-object`` both take); None means the system default, which follows changes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from gi.repository import GLib


@dataclass(frozen=True)
class Device:
    name: str  # node name
    label: str  # "Built-in Digital Microphone"
    kind: str  # "source" | "sink"
    default: bool = False
    internal: bool = False  # built into the computer (a laptop's speakers or microphone)


def _props(device) -> dict:
    props = device.get_properties()
    out = {}
    if props is None:
        return out
    for i in range(props.n_fields()):
        key = props.nth_field_name(i)
        try:
            out[key] = props.get_value(key)
        except TypeError:
            continue
    return out


def _device(gst_device) -> Device | None:
    klass = gst_device.get_device_class()
    kind = "source" if klass.startswith("Audio/Source") else "sink" if klass.startswith("Audio/Sink") else None
    if kind is None:
        return None
    props = _props(gst_device)
    name = props.get("node.name") or props.get("device.name")
    if not name:
        element = gst_device.create_element(None)
        if element is not None:
            for key in ("device", "target-object"):
                if element.find_property(key) is not None and element.get_property(key):
                    name = element.get_property(key)
                    break
    if not name or str(name).endswith(".monitor"):
        return None
    text = " ".join(str(v) for v in props.values()).lower()
    internal = props.get("device.form-factor") == "internal" or props.get("device.form_factor") == "internal" or \
        any(word in text for word in ("built-in", "internal", "sof_sdw", "sof-hda", "hda intel"))
    return Device(str(name), gst_device.get_display_name(), kind, bool(props.get("is-default")), internal)


class AudioDevices:
    """The machine's microphones and outputs; ``on_changed`` follows them."""

    def __init__(self, on_changed: Callable[[], None] | None = None) -> None:
        self.on_changed = on_changed
        self.devices: list[Device] = []
        self._monitor = None
        self._lock = threading.Lock()
        threading.Thread(target=self._start, name="streamy-devices", daemon=True).start()

    def _start(self) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Audio/Source", None)
        monitor.add_filter("Audio/Sink", None)
        bus = monitor.get_bus()
        if not monitor.start():
            return
        self._monitor = monitor
        self._refresh()
        while True:  # the monitor's own bus, on this thread: devices come and go
            message = bus.timed_pop(Gst.CLOCK_TIME_NONE)
            if message is None:
                break
            if message.type in (Gst.MessageType.DEVICE_ADDED, Gst.MessageType.DEVICE_REMOVED,
                                Gst.MessageType.DEVICE_CHANGED):
                self._refresh()

    def _refresh(self) -> None:
        found, seen = [], set()
        for gst_device in self._monitor.get_devices() or []:
            device = _device(gst_device)
            if device is not None and (device.kind, device.name) not in seen:
                seen.add((device.kind, device.name))
                found.append(device)
        with self._lock:
            self.devices = found
        if self.on_changed is not None:
            GLib.idle_add(lambda: self.on_changed() and False)

    def of(self, kind: str) -> list[Device]:
        with self._lock:
            return [d for d in self.devices if d.kind == kind]

    def default(self, kind: str) -> Device | None:
        listed = self.of(kind)
        return next((d for d in listed if d.default), listed[0] if listed else None)

    def find(self, kind: str, name: str | None) -> Device | None:
        if not name:
            return self.default(kind)
        return next((d for d in self.of(kind) if d.name == name), None)

    def words(self, kind: str, name: str | None) -> str:
        """What a strip says it hears: "System default (Built-in Digital Microphone)", or the device."""
        if not name:
            default = self.default(kind)
            return f"System default ({default.label})" if default else "System default"
        device = self.find(kind, name)
        return device.label if device else "Disconnected"
