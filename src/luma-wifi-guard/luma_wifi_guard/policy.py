# SPDX-License-Identifier: Apache-2.0
"""Which access points of a Wi-Fi network to avoid for now, and for how long.

Networks with several access points sometimes include one that accepts the
computer but never gives it an address: a radio left on another VLAN, a
misconfigured extender, a building's shared equipment. The computer roams to
it, loses its connection and stays there, because Wi-Fi roaming only looks at
signal strength. Android handles this the same way: an access point where
DHCP failed is blocked for a while, the block doubles if it fails again, and
a success clears it.

Two rules keep this from ever making things worse:

- an access point is only avoided when another access point of the same
  network has given this computer an address recently, so a network whose
  router is down, or that offers no IPv4 at all, is never touched;
- the last access point of a network that can be seen is never avoided.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

BASE_SECONDS = 5 * 60
MAX_SECONDS = 24 * 3600
# An address from another access point this recently proves the network works.
KNOWN_GOOD_SECONDS = 30 * 24 * 3600


class Avoidance:
    def __init__(self, path: Path | None = None, clock=time.time) -> None:
        self.path, self.clock = path, clock
        self.networks: dict[str, dict] = {}
        if path is not None:
            try:
                value = json.loads(path.read_text())
                if value.get("schema_version") == 1:
                    self.networks = value.get("networks") or {}
            except (OSError, ValueError, AttributeError):
                pass

    def _network(self, ssid: str) -> dict:
        return self.networks.setdefault(ssid, {"good": {}, "failed": {}})

    def succeeded(self, ssid: str, bssid: str) -> bool:
        """An address arrived through this access point. Returns whether an avoidance was lifted."""
        network = self._network(ssid)
        network["good"][bssid] = self.clock()
        lifted = network["failed"].pop(bssid, None) is not None
        self._save()
        return lifted

    def failed(self, ssid: str, bssid: str, visible: set[str]) -> float:
        """No address arrived through this access point. Returns the seconds it is now avoided, or 0."""
        now = self.clock()
        network = self._network(ssid)
        proven = any(now - when < KNOWN_GOOD_SECONDS for other, when in network["good"].items() if other != bssid)
        alternatives = visible - {bssid} - self.avoided(ssid)
        if not proven or not alternatives:
            return 0.0
        entry = network["failed"].setdefault(bssid, {"failures": 0, "until": 0})
        entry["failures"] += 1
        seconds = min(BASE_SECONDS * 2 ** (entry["failures"] - 1), MAX_SECONDS)
        entry["until"] = now + seconds
        network["good"].pop(bssid, None)
        self._save()
        return seconds

    def avoided(self, ssid: str) -> set[str]:
        now = self.clock()
        return {bssid for bssid, entry in self.networks.get(ssid, {}).get("failed", {}).items()
                if entry.get("until", 0) > now}

    def next_expiry(self) -> float | None:
        now = self.clock()
        ends = [entry["until"] for network in self.networks.values()
                for entry in network.get("failed", {}).values() if entry.get("until", 0) > now]
        return min(ends) if ends else None

    def forget_stale(self) -> None:
        """A failure count lasts a day past its block; a success is remembered for the known-good window."""
        now = self.clock()
        for ssid in list(self.networks):
            network = self.networks[ssid]
            network["failed"] = {b: e for b, e in network.get("failed", {}).items()
                                 if e.get("until", 0) + MAX_SECONDS > now}
            network["good"] = {b: t for b, t in network.get("good", {}).items() if now - t < KNOWN_GOOD_SECONDS}
            if not network["failed"] and not network["good"]:
                del self.networks[ssid]
        self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "networks": self.networks}))
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


def lease_since(options: dict, started: float) -> bool:
    """Whether NetworkManager's DHCP options describe a lease obtained at or after `started`."""
    try:
        expiry = float(options["expiry"])
        lease_time = float(options["dhcp_lease_time"])
    except (KeyError, TypeError, ValueError):
        return False
    return expiry - lease_time >= started - 1


def network_ssid(value: str) -> str:
    """wpa_supplicant's network ssid: a quoted string, or hexadecimal bytes."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    try:
        return bytes.fromhex(value).decode("utf-8", "replace")
    except ValueError:
        return value
