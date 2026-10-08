# SPDX-License-Identifier: MPL-2.0
"""What luma-audio-devices remembers between sessions.

One JSON document at $XDG_STATE_HOME/luma-audio-devices/state.json, written
atomically. It holds what the routing policy remembers per device (whether it still
takes the sound when it connects) and the AirPlay receivers the person chose. AirPlay passwords are never
written here; they live in the Secret Service (see secrets.py).

A document that cannot be read is moved aside rather than silently
overwritten, so a bug can never erase someone's choices without a trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import time

from .routing import PolicyState, memory_from_dict, memory_to_dict

__all__ = ("RememberedReceiver", "Store", "default_state_path")

log = logging.getLogger("luma-audio-devices")

VERSION = 1
PRUNE_AFTER = 180 * 24 * 3600
MAX_OUTPUTS = 200


def default_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "luma-audio-devices" / "state.json"


@dataclass
class RememberedReceiver:
    """An AirPlay receiver the person chose once. Identified by its device id,
    never by its address, which changes with every DHCP lease."""

    id: str
    name: str
    service_name: str
    model: str = ""
    remembered_at: float = 0.0
    last_connected: float = 0.0
    has_password: bool = False

    @classmethod
    def from_dict(cls, receiver_id: str, data: dict) -> "RememberedReceiver":
        def number(key: str) -> float:
            try:
                return float(data.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0
        return cls(id=receiver_id, name=str(data.get("name") or receiver_id),
                   service_name=str(data.get("service_name") or ""),
                   model=str(data.get("model") or ""),
                   remembered_at=number("remembered_at"), last_connected=number("last_connected"),
                   has_password=bool(data.get("has_password")))

    def to_dict(self) -> dict:
        return {"name": self.name, "service_name": self.service_name, "model": self.model,
                "remembered_at": self.remembered_at, "last_connected": self.last_connected,
                "has_password": self.has_password}


@dataclass
class Store:
    path: Path
    policy: PolicyState = field(default_factory=PolicyState)
    receivers: dict[str, RememberedReceiver] = field(default_factory=dict)
    # Set when the file was written by a newer version (after a rollback):
    # use defaults for this session and leave the newer file intact.
    read_only: bool = False
    # Written by 1.luma.1-2, with prompt answers that no longer mean anything.
    legacy_answers: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Store":
        path = path or default_state_path()
        store = cls(path=path)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return store
        except OSError as error:
            log.warning("cannot read %s: %s", path, error)
            return store
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            if int(data.get("version", 0)) > VERSION:
                log.warning("%s was written by a newer luma-audio-devices; not changing it", path)
                return cls(path=path, read_only=True)
            for key, entry in (data.get("outputs") or {}).items():
                if isinstance(key, str) and isinstance(entry, dict):
                    store.policy.devices[key] = memory_from_dict(entry)
                    store.legacy_answers |= "policy" in entry or "snoozed_until" in entry
            for receiver_id, entry in (data.get("airplay") or {}).items():
                if isinstance(receiver_id, str) and isinstance(entry, dict):
                    store.receivers[receiver_id] = RememberedReceiver.from_dict(receiver_id, entry)
        except (ValueError, TypeError, AttributeError) as error:
            aside = path.with_name(f"{path.name}.unreadable-{int(time.time())}")
            log.warning("state file %s is unreadable (%s); moved to %s", path, error, aside)
            try:
                os.replace(path, aside)
            except OSError:
                pass
            return cls(path=path)
        return store

    def prune(self, now: float) -> None:
        """Forget ordinary devices not seen for PRUNE_AFTER seconds. A device
        the person moved the sound away from is kept, since that choice changes
        what happens when it connects; the list never grows past MAX_OUTPUTS."""
        devices = self.policy.devices
        for key, memory in list(devices.items()):
            if memory.auto_switch and memory.last_seen and now - memory.last_seen > PRUNE_AFTER:
                del devices[key]
        if len(devices) > MAX_OUTPUTS:
            ordinary = sorted((m.last_seen, k) for k, m in devices.items() if m.auto_switch)
            for _, key in ordinary[:len(devices) - MAX_OUTPUTS]:
                del devices[key]

    def save(self) -> None:
        if self.read_only:
            return
        document = {
            "version": VERSION,
            "outputs": {key: memory_to_dict(memory)
                        for key, memory in sorted(self.policy.devices.items())},
            "airplay": {receiver_id: receiver.to_dict()
                        for receiver_id, receiver in sorted(self.receivers.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=1, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
