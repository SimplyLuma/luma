# SPDX-License-Identifier: MPL-2.0
"""Where sound goes when output devices come and go. It never asks.

A pure state machine. The service feeds it what PipeWire reports, with the
time, and carries out the actions it returns; it keeps no timers of its own.
``next_deadline()`` tells the service when to call ``tick``.

The rules, as people expect them from macOS and GNOME:

* Outputs present when the service starts, or that appear during the first
  ``STARTUP_GRACE`` seconds (Bluetooth reconnecting at login, USB enumerating
  late), were there at login: nothing changes.
* Network outputs (AirPlay and the like) are never switched to; they are
  opt-in through the AirPlay picker.
* A display with audio (HDMI/DisplayPort), a dock, a USB sound card or any
  other output that appears keeps the sound where it is. WirePlumber restores
  such a device only if the person chose it before (its own history).
* Headphones, earbuds, headsets and Bluetooth audio devices the person
  connects take the sound at once, unless ``switch_to_personal`` is off or the
  person moved the sound away from that device the last time it was
  connected. When the device goes away WirePlumber returns the sound to the
  output that had it before.
* A manual choice is respected and remembered per device: moving the sound
  away from an auto-selected device while it is still connected stops it
  taking the sound next time; choosing it again brings the behaviour back.
* Appearances settle together (``SETTLE`` seconds after the last, at most
  ``MAX_SETTLE``), so a dock or a Bluetooth profile change is decided once.

Every decision is returned as a ``Decision`` for the service to log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Callable

from .classify import Output

__all__ = ("RoutingPolicy", "PolicyState", "SwitchTo", "Decision",
           "memory_from_dict", "memory_to_dict",
           "STARTUP_GRACE", "SETTLE", "MAX_SETTLE", "MANUAL_WINDOW", "RESTORE_WINDOW")

STARTUP_GRACE = 12.0
SETTLE = 2.0
MAX_SETTLE = 8.0            # a steady stream of other arrivals cannot postpone forever
# A default that moves away from a device is a person's choice only if the
# device is still there this long afterwards (unplugging moves it too).
MANUAL_WINDOW = 1.5
# A default change within this long of the new default's arrival is WirePlumber
# restoring its history, not a person choosing. (The change can even be
# reported before the device itself.)
RESTORE_WINDOW = 1.0


@dataclass(frozen=True)
class SwitchTo:
    key: str
    node_name: str
    reason: str


@dataclass(frozen=True)
class Decision:
    """A routing decision, for the log: what happened and why."""

    event: str          # "kept", "switched", "remembered-choice", "ignored"
    key: str
    name: str
    reason: str
    current: str | None = None


@dataclass
class _DeviceMemory:
    """What the policy remembers about a device across connections."""

    auto_switch: bool = True        # False after the person moved sound away from it
    name: str = ""
    icon: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0


@dataclass
class PolicyState:
    devices: dict[str, _DeviceMemory] = field(default_factory=dict)


@dataclass
class _Pending:
    appeared_at: float
    settle_at: float


class RoutingPolicy:
    def __init__(self, state: PolicyState | None = None, *, switch_to_personal: bool = True,
                 on_state_changed: Callable[[PolicyState], None] | None = None,
                 startup_grace: float = STARTUP_GRACE) -> None:
        self.state = state or PolicyState()
        self.switch_to_personal = switch_to_personal
        self._on_state_changed = on_state_changed
        self._startup_grace = startup_grace
        self._started_at: float | None = None
        self._outputs: dict[str, Output] = {}          # node name -> output
        self._arrived: dict[str, float] = {}           # device key -> when this connection began
        self._pending: dict[str, _Pending] = {}        # device key -> settling appearance
        self._default: str | None = None               # effective default node name
        self._expected: str | None = None              # node we asked WirePlumber to select
        self._away_check: tuple[str, float, float] | None = None  # (device key, deadline, changed at)
        self._was_default: set[str] = set()            # keys that had the sound during this connection

    # Inputs -----------------------------------------------------------------

    def start(self, outputs: list[Output], default_node: str | None, now: float) -> list:
        """The service connected: everything present now was there at login."""
        self._started_at = now
        self._default = default_node
        for output in outputs:
            self._outputs[output.node_name] = output
            self._arrived.setdefault(output.key, now - self._startup_grace)
            self._remember(output, now)
        self._changed()
        return []

    def output_added(self, output: Output, now: float) -> list:
        known = self._outputs.get(output.node_name)
        self._outputs[output.node_name] = output
        if output.node_name == self._default:
            self._was_default.add(output.key)
        if known is not None and known.key == output.key:
            return []
        if output.network or not output.local:
            return []
        new_connection = output.key not in self._arrived
        if new_connection:
            self._arrived[output.key] = now
        self._remember(output, now)
        self._changed()
        if not new_connection:
            return []
        if self._in_startup_grace(now):
            return [Decision("kept", output.key, output.name, "present at login", self._current_name())]
        self._pending.setdefault(output.key, _Pending(now, now + SETTLE))
        # One burst (a dock, a Bluetooth profile switch) settles as a whole.
        for pending in self._pending.values():
            pending.settle_at = max(pending.settle_at, min(now + SETTLE, pending.appeared_at + MAX_SETTLE))
        return []

    def output_removed(self, node_name: str, now: float, configured: str | None = None) -> list:
        """``configured`` is WirePlumber's configured default output at the
        moment the device went away (``default.configured.audio.sink``). It
        records the person's last choice even when the default change itself
        was reported late or not at all."""
        output = self._outputs.get(node_name)
        if output is None:
            return []
        device_nodes = {name for name, o in self._outputs.items() if o.key == output.key}
        self._outputs.pop(node_name)
        if len(device_nodes) > 1:
            return []
        # The last node of the device went away: this connection is over.
        actions: list = []
        memory = self.state.devices.get(output.key)
        had_sound = output.key in self._was_default
        if memory is not None and had_sound and configured is not None and self._switches_automatically(output):
            chose_other = configured not in device_nodes
            if chose_other and memory.auto_switch:
                memory.auto_switch = False
                actions.append(Decision("remembered-choice", output.key, output.name,
                                        "the person moved the sound away; it no longer takes it on connect"))
            elif not chose_other and not memory.auto_switch:
                memory.auto_switch = True
                actions.append(Decision("remembered-choice", output.key, output.name,
                                        "chosen by the person; it takes the sound again when it connects"))
        self._arrived.pop(output.key, None)
        self._pending.pop(output.key, None)
        self._was_default.discard(output.key)
        if self._away_check is not None and self._away_check[0] == output.key:
            self._away_check = None
        if memory is not None:
            memory.last_seen = now
            self._changed()
        return actions

    def default_changed(self, node_name: str | None, now: float) -> list:
        previous = self._outputs.get(self._default or "")
        self._default = node_name
        current = self._outputs.get(node_name or "")
        if current is not None:
            self._was_default.add(current.key)
        if node_name is not None and node_name == self._expected:
            self._expected = None
            return []
        self._expected = None
        actions: list = []
        if current is not None:
            self._pending.pop(current.key, None)
            memory = self.state.devices.get(current.key)
            arrived = self._arrived.get(current.key, -math.inf)
            if memory is not None and not memory.auto_switch and now - arrived > RESTORE_WINDOW:
                memory.auto_switch = True
                self._changed()
                actions.append(Decision("remembered-choice", current.key, current.name,
                                        "chosen by the person; it takes the sound again when it connects"))
        if previous is not None and (current is None or current.key != previous.key) \
                and self._switches_automatically(previous):
            # Decided once we know it was not an unplug.
            self._away_check = (previous.key, now + MANUAL_WINDOW, now)
        return actions

    def tick(self, now: float) -> list:
        actions: list = []
        if self._away_check is not None and self._away_check[1] <= now:
            key, _deadline, changed_at = self._away_check
            self._away_check = None
            still_here = key in self._arrived
            current = self._outputs.get(self._default or "")
            restored = current is not None and \
                changed_at - self._arrived.get(current.key, -math.inf) <= RESTORE_WINDOW
            if still_here and not restored and (current is None or current.key != key):
                memory = self.state.devices.get(key)
                if memory is not None and memory.auto_switch:
                    memory.auto_switch = False
                    self._changed()
                    actions.append(Decision("remembered-choice", key, memory.name or key,
                                            "the person moved the sound away; it no longer takes it on connect",
                                            current.name if current else None))

        due = [(key, p) for key, p in self._pending.items() if p.settle_at <= now]
        if not due:
            return actions
        arrivals: list[Output] = []
        for key, _pending in due:
            self._pending.pop(key)
            node = self._best_node_for(key)
            if node is not None:
                arrivals.append(node)
        current = self._outputs.get(self._default or "")
        takers = []
        for output in arrivals:
            memory = self.state.devices.get(output.key, _DeviceMemory())
            if current is not None and current.key == output.key:
                actions.append(Decision("kept", output.key, output.name, "already the output", output.name))
            elif not self._switches_automatically(output):
                actions.append(Decision("kept", output.key, output.name,
                                        "displays, docks and other outputs never take the sound on their own",
                                        self._current_name()))
            elif not self.switch_to_personal:
                actions.append(Decision("kept", output.key, output.name,
                                        "switching to headphones and Bluetooth audio is off", self._current_name()))
            elif not memory.auto_switch:
                actions.append(Decision("kept", output.key, output.name,
                                        "the person moved the sound away from it last time", self._current_name()))
            else:
                takers.append(output)
        if takers:
            chosen = max(takers, key=lambda o: o.priority)
            reason = "Bluetooth audio connected" if chosen.key.startswith("bluez:") and not chosen.personal \
                else "headphones or headset connected"
            self._expected = chosen.node_name
            actions.append(Decision("switched", chosen.key, chosen.name, reason, self._current_name()))
            actions.append(SwitchTo(chosen.key, chosen.node_name, reason))
            for other in takers:
                if other is not chosen:
                    actions.append(Decision("kept", other.key, other.name,
                                            f"{chosen.name} connected at the same time", chosen.name))
        return actions

    def forget_device(self, key: str, now: float) -> list:
        if self.state.devices.pop(key, None) is None:
            return []
        self._changed()
        return []

    # Queries ----------------------------------------------------------------

    def next_deadline(self) -> float | None:
        deadlines = [p.settle_at for p in self._pending.values()]
        if self._away_check is not None:
            deadlines.append(self._away_check[1])
        return min(deadlines) if deadlines else None

    def present_keys(self) -> set[str]:
        return {o.key for o in self._outputs.values()}

    def output_for_node(self, node_name: str) -> Output | None:
        return self._outputs.get(node_name)

    # Internals --------------------------------------------------------------

    @staticmethod
    def _switches_automatically(output: Output) -> bool:
        """Worn by the person or connected on purpose over Bluetooth."""
        return output.personal or output.key.startswith("bluez:")

    def _current_name(self) -> str | None:
        current = self._outputs.get(self._default or "")
        return current.name if current is not None else None

    def _in_startup_grace(self, now: float) -> bool:
        return self._started_at is None or now - self._started_at < self._startup_grace

    def _best_node_for(self, key: str) -> Output | None:
        nodes = [o for o in self._outputs.values() if o.key == key]
        return max(nodes, key=lambda o: o.priority) if nodes else None

    def _remember(self, output: Output, now: float) -> None:
        memory = self.state.devices.get(output.key)
        if memory is None:
            memory = self.state.devices[output.key] = _DeviceMemory(first_seen=now)
        memory.name = output.name
        memory.icon = output.icon
        memory.last_seen = now

    def _changed(self) -> None:
        if self._on_state_changed is not None:
            self._on_state_changed(self.state)


def memory_from_dict(data: dict) -> _DeviceMemory:
    """Reads 1.luma.1/1.luma.2 records too. Their prompt answers ("Don't ask
    again", "Not now" snoozes) no longer mean anything and are dropped."""
    memory = _DeviceMemory()
    memory.auto_switch = data.get("auto_switch") is not False
    for name in ("first_seen", "last_seen"):
        try:
            setattr(memory, name, float(data.get(name) or 0.0))
        except (TypeError, ValueError):
            pass
    memory.name = str(data.get("name") or "")
    memory.icon = str(data.get("icon") or "")
    return memory


def memory_to_dict(memory: _DeviceMemory) -> dict:
    return {"auto_switch": memory.auto_switch, "name": memory.name, "icon": memory.icon,
            "first_seen": memory.first_seen, "last_seen": memory.last_seen}
