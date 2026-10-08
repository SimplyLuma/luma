# SPDX-License-Identifier: Apache-2.0
"""luma-wifi-guard: stay off access points that never give this computer an address.

wpa_supplicant picks access points; NetworkManager restarts DHCP each time it
joins one. This watches both. When the computer has joined an access point and
still has no IPv4 address LEASE_SECONDS later, the access point goes on the
network's ignore list in wpa_supplicant for a while (see policy.py) and the
computer roams to the strongest other access point of the same network.
Nothing is shown; the journal records each decision.
"""
from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .policy import Avoidance, lease_since, network_ssid  # noqa: E402

log = logging.getLogger("luma-wifi-guard")

SUPPLICANT, SUPPLICANT_PATH = "fi.w1.wpa_supplicant1", "/fi/w1/wpa_supplicant1"
INTERFACE, BSS, NETWORK = SUPPLICANT + ".Interface", SUPPLICANT + ".BSS", SUPPLICANT + ".Network"
NM, NM_PATH = "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
# NetworkManager gives up on a DHCP attempt after about 15 seconds and removes the address.
LEASE_SECONDS = 20
STATE = Path("/var/lib/luma-wifi-guard/avoidance.json")


@dataclass
class Attempt:
    bssid: str
    ssid: str
    started: float


def mac(value) -> str:
    return bytes(value).hex(":")


class Guard:
    def __init__(self, bus: Gio.DBusConnection, avoidance: Avoidance) -> None:
        self.bus, self.avoidance = bus, avoidance
        self.attempts: dict[str, Attempt] = {}
        self._expiry_source = 0

    # ── D-Bus helpers ──────────────────────────────────────────────────────

    def _call(self, name, path, interface, method, args=None):
        return self.bus.call_sync(name, path, interface, method, args, None, Gio.DBusCallFlags.NONE, 3000,
                                  None).unpack()

    def _get(self, name, path, interface, prop):
        return self._call(name, path, PROPERTIES, "Get", GLib.Variant("(ss)", (interface, prop)))[0]

    def _all(self, name, path, interface) -> dict:
        return self._call(name, path, PROPERTIES, "GetAll", GLib.Variant("(s)", (interface,)))[0]

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        self.bus.signal_subscribe(SUPPLICANT, PROPERTIES, "PropertiesChanged", None, INTERFACE,
                                  Gio.DBusSignalFlags.NONE, self._on_properties)
        self.bus.signal_subscribe(SUPPLICANT, INTERFACE, "NetworkAdded", None, None,
                                  Gio.DBusSignalFlags.NONE, lambda *args: self._apply(args[2]))
        Gio.bus_watch_name_on_connection(self.bus, SUPPLICANT, Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self._survey(), None)
        self.avoidance.forget_stale()

    def _interfaces(self) -> list[str]:
        try:
            return list(self._get(SUPPLICANT, SUPPLICANT_PATH, SUPPLICANT, "Interfaces"))
        except GLib.Error:
            return []

    def _survey(self) -> None:
        """wpa_supplicant (re)started, or so did this service: learn what works and reapply."""
        for path in self._interfaces():
            self._apply(path)
            try:
                props = self._all(SUPPLICANT, path, INTERFACE)
            except GLib.Error:
                continue
            if props.get("State") == "completed" and props.get("CurrentBSS", "/") != "/":
                identity = self._bss(props["CurrentBSS"])
                if identity and self._lease_age(props["Ifname"]) is not None:
                    self.avoidance.succeeded(identity[1], identity[0])
        self._schedule_expiry()

    # ── Association and DHCP ───────────────────────────────────────────────

    def _bss(self, path: str) -> tuple[str, str] | None:
        try:
            props = self._all(SUPPLICANT, path, BSS)
        except GLib.Error:
            return None
        ssid = bytes(props.get("SSID") or b"").decode("utf-8", "replace")
        return (mac(props["BSSID"]), ssid) if ssid else None

    def _on_properties(self, _bus, _sender, path, _interface, _signal, parameters) -> None:
        interface, changed, _ = parameters.unpack()
        if interface != INTERFACE:
            return
        if "CurrentNetwork" in changed or "Networks" in changed:
            self._apply(path)
        if "CurrentBSS" not in changed and "State" not in changed:
            return
        try:
            props = self._all(SUPPLICANT, path, INTERFACE)
        except GLib.Error:
            return
        if props.get("State") != "completed" or props.get("CurrentBSS", "/") == "/":
            return
        identity = self._bss(props["CurrentBSS"])
        if identity is None:
            return
        attempt = self.attempts.get(path)
        if attempt is not None and attempt.bssid == identity[0]:
            return
        attempt = self.attempts[path] = Attempt(identity[0], identity[1], time.time())
        GLib.timeout_add_seconds(LEASE_SECONDS, lambda: (self._check(path, attempt), False)[1])

    def _device(self, ifname: str) -> str | None:
        try:
            return self._call(NM, NM_PATH, NM, "GetDeviceByIpIface", GLib.Variant("(s)", (ifname,)))[0]
        except GLib.Error:
            return None

    def _uses_dhcp(self, device: str) -> bool:
        try:
            settings, _ = self._call(NM, device, NM + ".Device", "GetAppliedConnection", GLib.Variant("(u)", (0,)))
        except GLib.Error:
            return False
        return settings.get("ipv4", {}).get("method") == "auto"

    def _lease_age(self, ifname: str, since: float = 0.0) -> float | None:
        """Seconds since this interface's current DHCP lease began, if it began at or after `since`."""
        device = self._device(ifname)
        if device is None:
            return None
        try:
            config = self._get(NM, device, NM + ".Device", "Dhcp4Config")
            options = self._get(NM, config, NM + ".DHCP4Config", "Options") if config != "/" else {}
        except GLib.Error:
            return None
        if not lease_since(options, since):
            return None
        return time.time() - (float(options["expiry"]) - float(options["dhcp_lease_time"]))

    def _has_ipv4(self, device: str) -> bool:
        try:
            config = self._get(NM, device, NM + ".Device", "Ip4Config")
            return config != "/" and bool(self._get(NM, config, NM + ".IP4Config", "AddressData"))
        except GLib.Error:
            return True  # unknown is not a failure

    def _check(self, path: str, attempt: Attempt) -> None:
        if self.attempts.get(path) is not attempt:
            return
        try:
            props = self._all(SUPPLICANT, path, INTERFACE)
        except GLib.Error:
            return
        if props.get("State") != "completed" or (self._bss(props.get("CurrentBSS", "/")) or ("",))[0] != attempt.bssid:
            return  # it has already moved on; that join is checked on its own
        ifname = props["Ifname"]
        if self._lease_age(ifname, attempt.started) is not None:
            if self.avoidance.succeeded(attempt.ssid, attempt.bssid):
                log.info("access point %s on %r gave an address again; no longer avoided", attempt.bssid, attempt.ssid)
                self._apply(path)
            return
        device = self._device(ifname)
        if device is None or not self._uses_dhcp(device) or self._has_ipv4(device):
            return
        visible = {identity[0] for identity in map(self._bss, props.get("BSSs", [])) if identity and identity[1] == attempt.ssid}
        seconds = self.avoidance.failed(attempt.ssid, attempt.bssid, visible)
        if not seconds:
            log.info("no address through %s on %r, but no other access point is proven; leaving it", attempt.bssid,
                     attempt.ssid)
            return
        log.info("no address through access point %s on %r after %ss; avoiding it for %d minutes", attempt.bssid,
                 attempt.ssid, LEASE_SECONDS, seconds // 60)
        self._apply(path)
        self._roam(path, attempt.ssid, props.get("BSSs", []))
        self._schedule_expiry()

    def _roam(self, path: str, ssid: str, bss_paths: list[str]) -> None:
        avoided = self.avoidance.avoided(ssid)
        best, strongest = None, -1000
        for bss_path in bss_paths:
            try:
                props = self._all(SUPPLICANT, bss_path, BSS)
            except GLib.Error:
                continue
            bssid = mac(props["BSSID"])
            if bytes(props.get("SSID") or b"").decode("utf-8", "replace") == ssid and bssid not in avoided \
                    and props.get("Signal", -1000) > strongest:
                best, strongest = bssid, props["Signal"]
        try:
            if best is not None:
                self._call(SUPPLICANT, path, INTERFACE, "Roam", GLib.Variant("(s)", (best,)))
                log.info("roaming to %s (%d dBm)", best, strongest)
            else:
                self._call(SUPPLICANT, path, INTERFACE, "Reassociate")
        except GLib.Error as error:
            log.info("roaming failed (%s); reassociating", error.message)
            try:
                self._call(SUPPLICANT, path, INTERFACE, "Reassociate")
            except GLib.Error:
                pass

    # ── The ignore lists ───────────────────────────────────────────────────

    def _apply(self, interface_path: str) -> None:
        try:
            networks = self._get(SUPPLICANT, interface_path, INTERFACE, "Networks")
        except GLib.Error:
            return
        for network in networks:
            try:
                props = self._get(SUPPLICANT, network, NETWORK, "Properties")
            except GLib.Error:
                continue
            ignore = " ".join(sorted(self.avoidance.avoided(network_ssid(props.get("ssid", "")))))
            if props.get("bssid_ignore", "") == ignore:
                continue
            try:
                self._call(SUPPLICANT, network, PROPERTIES, "Set", GLib.Variant(
                    "(ssv)", (NETWORK, "Properties", GLib.Variant("a{sv}", {"bssid_ignore": GLib.Variant("s", ignore)}))))
            except GLib.Error as error:
                log.warning("could not update the ignore list: %s", error.message)

    def _schedule_expiry(self) -> None:
        if self._expiry_source:
            GLib.source_remove(self._expiry_source)
            self._expiry_source = 0
        expiry = self.avoidance.next_expiry()
        if expiry is None:
            return

        def expired() -> bool:
            self._expiry_source = 0
            for path in self._interfaces():
                self._apply(path)
            self._schedule_expiry()
            return False
        self._expiry_source = GLib.timeout_add_seconds(max(1, int(expiry - time.time()) + 1), expired)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    guard = Guard(bus, Avoidance(STATE))
    guard.start()
    loop = GLib.MainLoop()
    try:
        from gi.repository import GLibUnix
        add_signal = GLibUnix.signal_add
    except ImportError:
        add_signal = GLib.unix_signal_add
    for number in (signal.SIGTERM, signal.SIGINT):
        add_signal(GLib.PRIORITY_DEFAULT, number, lambda: (loop.quit(), False)[1])
    loop.run()
    return 0
