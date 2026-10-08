# SPDX-License-Identifier: MPL-2.0
"""The PipeWire graph as pw-dump describes it, reduced to sound outputs.

``pw-dump --monitor`` prints JSON arrays of objects: the whole graph first,
then every object that changed, and ``{"id": N, "info": null}`` for objects
that went away. Metadata changes arrive as deltas, and pw-dump 1.6 prints a
removed metadata key as an empty list, so the service reads the default
output from WirePlumber's metadata object instead (pipewire.py); the value
kept here is only a fallback. ``Graph`` folds those chunks into a model and answers the
questions the service asks: which outputs exist and are usable, which is the
default, and which network outputs nobody chose.

An output is usable the way WirePlumber decides it (scripts/lib/linking-utils.lua
haveAvailableRoutes): a node that belongs to a card port which reports
"available: no" (an HDMI port with no display, a headphone jack with nothing
plugged in) is not an output yet.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable

from .classify import is_hidden_output

__all__ = ("Graph", "OutputNode", "ChunkDecoder")

NODE = "PipeWire:Interface:Node"
DEVICE = "PipeWire:Interface:Device"
METADATA = "PipeWire:Interface:Metadata"

OUTPUT_CLASSES = ("Audio/Sink", "Audio/Duplex")

# Device properties the classifier reads (see classify.device_key).
DEVICE_PROPERTIES = (
    "device.bus", "device.bus-path", "device.name", "device.vendor.id", "device.product.id",
    "device.serial", "device.description", "device.product.name", "device.form-factor",
    "device.icon-name", "api.bluez5.address", "device.api",
)


class ChunkDecoder:
    """Split the pw-dump byte stream into complete JSON documents."""

    def __init__(self) -> None:
        self._buffer = ""
        self._decoder = json.JSONDecoder()

    def feed(self, text: str) -> list:
        self._buffer += text
        documents = []
        position = 0
        length = len(self._buffer)
        while True:
            while position < length and self._buffer[position] in " \t\r\n":
                position += 1
            if position >= length:
                break
            try:
                document, end = self._decoder.raw_decode(self._buffer, position)
            except json.JSONDecodeError:
                break
            documents.append(document)
            position = end
        self._buffer = self._buffer[position:]
        return documents


@dataclass(frozen=True)
class OutputNode:
    node_id: int
    name: str
    props: dict           # node properties laid over its device's properties
    route: dict           # active route info plus port.type, or {}
    available: bool


def _info_dict(info) -> dict:
    """Route "info" is {count, key, value, ...} as a flat list in pw-dump."""
    if isinstance(info, dict):
        return {str(k): v for k, v in info.items()}
    result: dict = {}
    if isinstance(info, list):
        items = info[1:] if info and isinstance(info[0], int) else info
        for index in range(0, len(items) - 1, 2):
            result[str(items[index])] = items[index + 1]
    return result


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Graph:
    def __init__(self) -> None:
        self.objects: dict[int, dict] = {}

    # Folding ----------------------------------------------------------------

    def apply(self, chunk: Iterable[dict]) -> None:
        for update in chunk:
            if not isinstance(update, dict) or "id" not in update:
                continue
            object_id = update["id"]
            if update.get("info") is None and update.get("metadata") is None:
                self.objects.pop(object_id, None)
                continue
            current = self.objects.get(object_id)
            if current is None or (update.get("type") and current.get("type") != update.get("type")):
                self.objects[object_id] = json.loads(json.dumps(update))
                continue
            for key, value in update.items():
                if key == "metadata" and isinstance(value, list):
                    # Metadata arrives as deltas: only the entries that changed.
                    entries = {(e.get("subject"), e.get("key")): e
                               for e in current.get("metadata") or () if isinstance(e, dict)}
                    for entry in value:
                        if not isinstance(entry, dict):
                            continue
                        slot = (entry.get("subject"), entry.get("key"))
                        if entry.get("value") is None:
                            entries.pop(slot, None)
                        else:
                            entries[slot] = entry
                    current["metadata"] = list(entries.values())
                elif key == "info" and isinstance(value, dict) and isinstance(current.get("info"), dict):
                    info = current["info"]
                    for info_key, info_value in value.items():
                        if info_key == "params" and isinstance(info_value, dict):
                            params = info.setdefault("params", {})
                            params.update(info_value)
                        else:
                            info[info_key] = info_value
                else:
                    current[key] = value

    # Queries ----------------------------------------------------------------

    def _devices(self) -> dict[int, dict]:
        return {oid: o for oid, o in self.objects.items() if o.get("type") == DEVICE}

    def default_sink(self, configured: bool = False) -> str | None:
        key = "default.configured.audio.sink" if configured else "default.audio.sink"
        for obj in self.objects.values():
            if obj.get("type") != METADATA:
                continue
            if (obj.get("props") or {}).get("metadata.name") != "default":
                continue
            for entry in obj.get("metadata") or ():
                if entry.get("key") == key and entry.get("subject", 0) == 0:
                    value = entry.get("value")
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except ValueError:
                            return None
                    if isinstance(value, dict):
                        name = value.get("name")
                        return name if isinstance(name, str) else None
        return None

    def output_nodes(self) -> dict[str, OutputNode]:
        devices = self._devices()
        result: dict[str, OutputNode] = {}
        for oid, obj in self.objects.items():
            if obj.get("type") != NODE:
                continue
            info = obj.get("info") or {}
            node_props = info.get("props") or {}
            if node_props.get("media.class") not in OUTPUT_CLASSES:
                continue
            name = node_props.get("node.name")
            if not isinstance(name, str):
                continue
            device = devices.get(_as_int(node_props.get("device.id")))
            merged: dict = {}
            route_info: dict = {}
            available = True
            if device is not None:
                device_info = device.get("info") or {}
                device_props = device_info.get("props") or {}
                for key in DEVICE_PROPERTIES:
                    if key in device_props:
                        merged[key] = device_props[key]
                route_info, available = self._route_for(device_info.get("params") or {},
                                                        node_props.get("card.profile.device"))
            merged.update(node_props)
            result[name] = OutputNode(oid, name, merged, route_info, available)
        return result

    @staticmethod
    def _route_for(params: dict, card_profile_device) -> tuple[dict, bool]:
        profile_device = _as_int(card_profile_device)
        if profile_device is None:
            return {}, True
        for route in params.get("Route") or ():
            if isinstance(route, dict) and _as_int(route.get("device")) == profile_device:
                info = _info_dict(route.get("info"))
                return info, route.get("available") != "no"
        found = 0
        for route in params.get("EnumRoute") or ():
            if not isinstance(route, dict):
                continue
            devices = route.get("devices") or ()
            if profile_device in [_as_int(d) for d in devices]:
                found += 1
                if route.get("available") != "no":
                    return _info_dict(route.get("info")), True
        # A profile without routes (Pro Audio) counts as available.
        return {}, found == 0

    def hidden_output_names(self) -> list[str]:
        return sorted(node.name for node in self.output_nodes().values()
                      if is_hidden_output(node.props))
