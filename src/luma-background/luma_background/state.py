# SPDX-License-Identifier: MPL-2.0
"""What agents did, and what they asked to be woken for, across logins.

Kept apart from the person's decisions: this file can be deleted at any time
and the only loss is "last run" history and pending schedules the agents will
set again at their next wake.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import ids

MAX_SCHEDULES_PER_APP = 64


@dataclass(slots=True)
class Schedule:
    name: str
    at: int = 0          # Unix seconds, one-shot
    every: int = 0       # seconds, repeating
    accuracy: int = 60

    def to_dict(self) -> dict:
        return {"at": self.at, "every": self.every, "accuracy": self.accuracy}


@dataclass(slots=True)
class AppState:
    last_run_usec: int = 0
    last_exit: str = ""
    schedules: dict[str, Schedule] = field(default_factory=dict)


class StateStore:
    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        self.apps: dict[str, AppState] = {}
        self._dirty = False
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        for app, entry in (data.get("apps") or {}).items():
            if not ids.is_app_id(app) or not isinstance(entry, dict):
                continue
            state = AppState(
                last_run_usec=int(entry.get("last_run_usec", 0) or 0),
                last_exit=str(entry.get("last_exit", "") or ""),
            )
            for name, schedule in (entry.get("schedules") or {}).items():
                if not ids.SCHEDULE_NAME.fullmatch(name) or not isinstance(schedule, dict):
                    continue
                at, every = int(schedule.get("at", 0) or 0), int(schedule.get("every", 0) or 0)
                if bool(at) == bool(every):
                    continue
                state.schedules[name] = Schedule(name, at, every, int(schedule.get("accuracy", 60) or 60))
            self.apps[app] = state

    def app(self, app: str) -> AppState:
        return self.apps.setdefault(app, AppState())

    def record_run(self, app: str, when_usec: int | None = None) -> None:
        self.app(app).last_run_usec = when_usec if when_usec is not None else time.time_ns() // 1000
        self._dirty = True

    def record_exit(self, app: str, how: str) -> None:
        self.app(app).last_exit = how
        self._dirty = True

    def set_schedule(self, app: str, schedule: Schedule) -> None:
        schedules = self.app(app).schedules
        if schedule.name not in schedules and len(schedules) >= MAX_SCHEDULES_PER_APP:
            raise ValueError("too many schedules")
        schedules[schedule.name] = schedule
        self._dirty = True

    def remove_schedule(self, app: str, name: str) -> bool:
        removed = self.app(app).schedules.pop(name, None) is not None
        self._dirty = self._dirty or removed
        return removed

    def flush(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "version": self.VERSION,
            "apps": {
                app: {
                    "last_run_usec": state.last_run_usec,
                    "last_exit": state.last_exit,
                    "schedules": {name: item.to_dict() for name, item in sorted(state.schedules.items())},
                }
                for app, state in sorted(self.apps.items())
            },
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self._dirty = False
