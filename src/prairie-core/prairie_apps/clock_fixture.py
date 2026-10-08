# SPDX-License-Identifier: Apache-2.0
"""Clock's v70 sample, held entirely in memory for visual captures.

The fixture store has the same small interface as ClockStore. It never creates
the real data directory or starts the alarm service. Interactions during a
capture change only these Python lists.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

from .clock_backend import Alarm, TimerRecord, WorldClock


class FixtureClockStore:
    def __init__(self, payload: dict) -> None:
        self._world = [WorldClock(str(item["uid"]), str(item["label"]), str(item["zone"]))
                       for item in payload["world"]]
        self._alarms = [Alarm(
            str(item["uid"]), str(item["label"]), int(item["hour"]), int(item["minute"]),
            tuple(int(day) for day in item.get("days", ())), bool(item["enabled"]),
        ) for item in payload["alarms"]]
        self.alarm_days = {str(item["uid"]): str(item["days_label"]) for item in payload["alarms"]}
        self._timers: list[TimerRecord] = []
        self.home_label = str(payload.get("home_label", ""))
        self.home_zone = str(payload.get("home_zone", ""))
        self.initial_timer = int(payload.get("initial_timer_seconds", 300))
        self._places = tuple(payload.get("places", ()))
        self.offsets = {str(item["zone"]): float(item["offset_hours"]) for item in payload["world"]}

    @classmethod
    def from_path(cls, path: str | Path) -> "FixtureClockStore":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Clock fixture must be an object")
        return cls(payload)

    def world_clocks(self) -> tuple[WorldClock, ...]:
        return tuple(self._world)

    def add_world_clock(self, label: str, zone: str, *, uid: str | None = None) -> WorldClock:
        record = WorldClock(uid or uuid.uuid4().hex, label, zone)
        self._world.append(record)
        return record

    def insert_world_clock(self, record: WorldClock, index: int) -> None:
        self._world.insert(index, record)

    def remove_world_clock(self, uid: str) -> None:
        self._world = [item for item in self._world if item.uid != uid]

    def move_world_clock(self, uid: str, offset: int) -> bool:
        index = next((i for i, item in enumerate(self._world) if item.uid == uid), -1)
        target = index + offset
        if index < 0 or not 0 <= target < len(self._world):
            return False
        self._world.insert(target, self._world.pop(index))
        return True

    def alarms(self) -> tuple[Alarm, ...]:
        return tuple(sorted(self._alarms, key=lambda item: (item.hour, item.minute)))

    def save_alarm(self, alarm: Alarm, *, index: int | None = None) -> Alarm:
        self._alarms = [item for item in self._alarms if item.uid != alarm.uid]
        position = len(self._alarms) if index is None else index
        self._alarms.insert(position, alarm)
        return alarm

    def update_alarm_enabled(self, uid: str, enabled: bool) -> bool:
        for index, alarm in enumerate(self._alarms):
            if alarm.uid == uid:
                self._alarms[index] = replace(alarm, enabled=enabled)
                return True
        return False

    def delete_alarm(self, uid: str) -> None:
        self._alarms = [item for item in self._alarms if item.uid != uid]

    def timers(self) -> tuple[TimerRecord, ...]:
        return tuple(self._timers)

    def save_timer(self, record: TimerRecord) -> TimerRecord:
        self._timers = [item for item in self._timers if item.uid != record.uid]
        self._timers.append(record)
        return record

    def clear_timer(self, uid: str) -> None:
        self._timers = [item for item in self._timers if item.uid != uid]

    def places(self) -> tuple[dict, ...]:
        return self._places
