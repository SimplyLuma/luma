# SPDX-License-Identifier: MPL-2.0
"""What kind of sound output a PipeWire node is, and what to call it.

Pure functions over PipeWire property dictionaries, with no GObject imports,
so they can be tested anywhere. ``device_key`` must stay identical to
``device_key`` in data/wireplumber/scripts/lib/luma-output-policy.lua: both
test suites check tests/luma-audio-devices/fixtures/device-keys.json.

``props`` is always a node's properties laid over the properties of the
device the node belongs to (node values win). ``route`` is the info
dictionary of the node's active route plus its ``port.type`` when known.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

__all__ = (
    "NETWORK_NAME_PREFIXES", "DISCOVERED_NAME_PREFIXES", "AIRPLAY_NODE_PREFIX",
    "truthy", "is_network", "is_discovered_network", "is_hidden_output",
    "is_local_hardware", "device_key", "display_name", "icon_name",
    "is_personal_listening", "is_display", "Output", "describe",
)

AIRPLAY_NODE_PREFIX = "luma_airplay."

NETWORK_NAME_PREFIXES = (
    "raop_sink.", AIRPLAY_NODE_PREFIX, "tunnel.", "rtp_session.", "rtp-sink",
    "roc-sink", "snapcast", "netjack2_",
)

NETWORK_PROPERTIES = (
    "raop.ip", "raop.hostname", "tunnel.mode", "pulse.server.address",
    "rtp.destination.ip", "snapcast.name", "roc.remote.source.endpoint",
    "luma.airplay.id",
)

# Created by a module that discovers receivers on the network by itself
# (module-raop-discover, module-zeroconf-discover, module-rtp-session,
# module-snapcast-discover). Outputs a person configured by hand keep showing.
DISCOVERED_NAME_PREFIXES = ("raop_sink.", "tunnel.", "rtp_session.", "snapcast")

PERSONAL_FORM_FACTORS = frozenset({"headphone", "headset", "hands-free", "handset"})
PERSONAL_ICONS = frozenset({"audio-headphones", "audio-headset"})
PERSONAL_PORT_TYPES = frozenset({"headphones", "headset"})


def truthy(value) -> bool:
    return value is True or value in ("true", "1")


def _nonempty(value) -> str | None:
    if value is None:
        return None
    value = str(value)
    return value or None


def is_network(props: Mapping) -> bool:
    if truthy(props.get("node.network")):
        return True
    if props.get("sess.media") == "raop":
        return True
    if any(_nonempty(props.get(key)) for key in NETWORK_PROPERTIES):
        return True
    name = props.get("node.name")
    return isinstance(name, str) and name.startswith(NETWORK_NAME_PREFIXES)


def is_discovered_network(props: Mapping) -> bool:
    name = props.get("node.name")
    return (is_network(props) and isinstance(name, str)
            and name.startswith(DISCOVERED_NAME_PREFIXES))


def is_hidden_output(props: Mapping) -> bool:
    """Network outputs nobody chose: never listed in the sound menus."""
    return is_discovered_network(props) and not _nonempty(props.get("luma.airplay.id"))


def is_local_hardware(props: Mapping) -> bool:
    return (not is_network(props)
            and props.get("device.api") in ("alsa", "bluez5")
            and str(props.get("media.class", "")).startswith("Audio/Sink"))


def is_display(props: Mapping, route: Mapping | None = None) -> bool:
    route = route or {}
    return route.get("port.type") == "hdmi" or props.get("device.icon_name") == "video-display"


def device_key(props: Mapping, route: Mapping | None = None) -> str:
    route = route or {}
    if is_network(props):
        airplay = _nonempty(props.get("luma.airplay.id"))
        if airplay:
            return "airplay:" + airplay.lower()
        return "network:" + str(props.get("node.name") or "")

    address = _nonempty(props.get("api.bluez5.address"))
    if address:
        return "bluez:" + address.upper()

    if props.get("device.bus") == "usb":
        vendor = _nonempty(props.get("device.vendor.id"))
        product = _nonempty(props.get("device.product.id"))
        if vendor and product:
            key = f"usb:{vendor.lower()}:{product.lower()}"
            serial = _nonempty(props.get("device.serial"))
            if serial:
                key += ":" + serial
            return key

    card = _nonempty(props.get("device.bus-path")) or _nonempty(props.get("device.name"))
    if is_display(props, route):
        product = _nonempty(route.get("device.product.name"))
        return "display:" + (card or "") + ":" + (product or str(props.get("node.name") or ""))

    if card:
        slot = _nonempty(props.get("card.profile.device")) or str(props.get("node.name") or "")
        return f"card:{card}:{slot}"

    return "node:" + str(props.get("node.name") or "")


def is_personal_listening(props: Mapping, route: Mapping | None = None) -> bool:
    """Headphones, earbuds and headsets: worn by the person who connected them."""
    route = route or {}
    if props.get("device.form-factor") in PERSONAL_FORM_FACTORS:
        return True
    if route.get("port.type") in PERSONAL_PORT_TYPES:
        return True
    for key in ("device.icon-name", "device.icon_name"):
        icon = str(props.get(key) or "").removesuffix("-symbolic")
        if icon in PERSONAL_ICONS:
            return True
    return False


_GENERIC_CARD_WORDS = ("HD Audio", "High Definition Audio", "Series Processors")


def display_name(props: Mapping, route: Mapping | None = None) -> str:
    """The name a person recognises: the product, not the sound card path."""
    route = route or {}
    if is_display(props, route):
        product = _nonempty(route.get("device.product.name"))
        if product:
            return product
        nick = _nonempty(props.get("node.nick"))
        if nick and not nick.upper().startswith("HDMI"):
            return nick
        return _nonempty(props.get("device.profile.description")) or "Display"

    if props.get("device.api") == "bluez5" or props.get("device.bus") == "usb":
        for key in ("device.description", "device.product.name", "node.description", "node.nick"):
            value = _nonempty(props.get(key))
            if value:
                return value

    # A port of a built-in card ("Headphones", "Line Out") reads better than
    # the processor's audio controller name.
    profile = _nonempty(props.get("device.profile.description"))
    description = _nonempty(props.get("node.description"))
    if profile and (not description or any(word in description for word in _GENERIC_CARD_WORDS)):
        return profile
    return description or _nonempty(props.get("node.nick")) or str(props.get("node.name") or "Sound output")


def icon_name(props: Mapping, route: Mapping | None = None) -> str:
    route = route or {}
    if is_network(props):
        return "luma-network-speaker-symbolic"
    if is_display(props, route):
        return "video-display-symbolic"
    form = props.get("device.form-factor")
    icon = str(props.get("device.icon-name") or props.get("device.icon_name") or "").removesuffix("-symbolic")
    if form == "headset" or icon == "audio-headset" or route.get("port.type") == "headset":
        return "audio-headset-symbolic"
    if is_personal_listening(props, route):
        return "audio-headphones-symbolic"
    if form in ("speaker", "car", "hifi", "tv") or icon == "audio-speakers":
        return "audio-speakers-symbolic"
    return "audio-card-symbolic"


@dataclass(frozen=True)
class Output:
    """One sound output as the routing policy sees it."""

    node_name: str
    key: str
    name: str
    icon: str
    personal: bool = False
    network: bool = False
    local: bool = True
    priority: int = 0


def describe(props: Mapping, route: Mapping | None = None) -> Output:
    try:
        priority = int(props.get("priority.session") or props.get("priority.driver") or 0)
    except (TypeError, ValueError):
        priority = 0
    return Output(
        node_name=str(props.get("node.name") or ""),
        key=device_key(props, route),
        name=display_name(props, route),
        icon=icon_name(props, route),
        personal=is_personal_listening(props, route),
        network=is_network(props),
        local=is_local_hardware(props),
        priority=priority,
    )
