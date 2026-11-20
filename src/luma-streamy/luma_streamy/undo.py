# SPDX-License-Identifier: Apache-2.0
"""Project-wide undo: every edit is a step, a snapshot of the project's JSON before it.

Pure Python. A drag or a slider scrub is one step: checkpoints with the same ``key`` that follow
each other within ``COALESCE_S`` join the step already taken. Undoing gives back the project as it
was (same ids, places, Looks, sources and restore tokens), so a deleted widget returns exactly.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass

COALESCE_S = 1.5
DEPTH = 200


@dataclass
class Step:
    label: str
    project: dict


class History:
    def __init__(self) -> None:
        self.undos: list[Step] = []
        self.redos: list[Step] = []
        self._key = None
        self._at = 0.0

    def checkpoint(self, label: str, project: dict, *, key=None, now: float | None = None) -> bool:
        """Before an edit: remember ``project`` as it is (the dict is copied). False when joined."""
        now = time.monotonic() if now is None else now
        if key is not None and key == self._key and now - self._at < COALESCE_S and self.undos:
            self._at = now
            return False
        self.undos.append(Step(label, copy.deepcopy(project)))
        del self.undos[:-DEPTH]
        self.redos.clear()
        self._key, self._at = key, now
        return True

    def end_coalescing(self) -> None:
        self._key = None

    def undo(self, current: dict) -> Step | None:
        """The step to go back to (its project); ``current`` becomes redoable."""
        if not self.undos:
            return None
        step = self.undos.pop()
        self.redos.append(Step(step.label, copy.deepcopy(current)))
        self._key = None
        return step

    def redo(self, current: dict) -> Step | None:
        if not self.redos:
            return None
        step = self.redos.pop()
        self.undos.append(Step(step.label, copy.deepcopy(current)))
        self._key = None
        return step

    @property
    def undo_label(self) -> str | None:
        return self.undos[-1].label if self.undos else None

    @property
    def redo_label(self) -> str | None:
        return self.redos[-1].label if self.redos else None
