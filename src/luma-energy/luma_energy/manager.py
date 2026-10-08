# SPDX-License-Identifier: MPL-2.0
"""Turning what the compositor sees into what the machine spends.

The manager owns every decision and every undo. It is deliberately the only
place that writes anything: the Shell extension reports, the D-Bus service
translates, and this decides. That means there is exactly one place to read to
find out why an application is being treated the way it is, and exactly one
place that has to get "put it back" right.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import cgroups, policy
from .claims import Claims, cpu_usec, quiet

#: How long an application must be hidden and quiet before freezing is even
#: considered. Five minutes is long enough that the person has moved on, and
#: short enough to be worth doing.
FREEZE_AFTER_SECONDS = 5 * 60

#: A frozen application is woken briefly every so often so that nothing builds
#: an unbounded backlog of delayed timers, then frozen again if still quiet.
FREEZE_REFRESH_SECONDS = 30 * 60


@dataclass
class Application:
    cgroup: str
    unit: str
    state: str = policy.HIDDEN
    since: float = field(default_factory=time.monotonic)
    applied: dict[str, int] = field(default_factory=dict)
    uclamp: int | None = None
    frozen: bool = False
    frozen_at: float = 0.0
    cpu_mark: int = 0
    cpu_mark_at: float = 0.0
    reasons: list[str] = field(default_factory=list)


class Manager:
    def __init__(self, *, freezing: bool = False, on_battery: bool = False,
                 claims: Claims | None = None) -> None:
        #: Freezing is the only measure here that can be felt, so it ships off
        #: and is turned on once it has earned it.
        self.freezing = freezing
        self.on_battery = on_battery
        self.claims = claims or Claims()
        self.applications: dict[str, Application] = {}

    # -- what the compositor tells us -------------------------------------

    def set_states(self, states: dict[str, str]) -> None:
        """The whole picture, every time. Anything absent has no windows left."""
        now = time.monotonic()
        for cgroup, state in states.items():
            unit = cgroups.unit_of(cgroup)
            if policy.protected(cgroup, unit) or not unit:
                continue
            application = self.applications.get(cgroup)
            if application is None:
                application = Application(cgroup=cgroup, unit=unit)
                self.applications[cgroup] = application
            if application.state != state:
                application.state = state
                application.since = now
                # A change of state is a change of circumstances; re-judge
                # quietness from here rather than from a stale mark.
                application.cpu_mark = 0
                application.cpu_mark_at = 0.0
            # Anything the person can see is awake, now, before anything else
            # is decided about it.
            if state in (policy.FOCUSED, policy.VISIBLE) and application.frozen:
                self._thaw(application)

        for cgroup in [c for c in self.applications if c not in states]:
            # No windows reported. The scope may simply have exited; if it has
            # not, it is an application with nothing on screen, which the
            # hidden rung already covers on its next tick.
            if not cgroups.exists(cgroup):
                self._release(self.applications.pop(cgroup))

    def set_on_battery(self, on_battery: bool) -> None:
        if on_battery == self.on_battery:
            return
        self.on_battery = on_battery
        if not on_battery:
            # Plugged in: give everything back at once. Nobody should have to
            # wait out a timer for their machine to feel normal again.
            for application in list(self.applications.values()):
                self._thaw(application)
                self._release(application, keep=True)

    # -- thawing, which happens before the person notices ------------------

    def thaw(self, cgroup: str) -> bool:
        application = self.applications.get(cgroup)
        if application is None:
            return False
        return self._thaw(application)

    def thaw_all(self) -> int:
        return sum(1 for a in list(self.applications.values()) if self._thaw(a))

    # -- the periodic decision --------------------------------------------

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for application in list(self.applications.values()):
            if not cgroups.exists(application.cgroup):
                self.applications.pop(application.cgroup, None)
                continue
            self._judge(application, now)

    def _judge(self, application: Application, now: float) -> None:
        held = application.state
        path = cgroups.path_of(application.cgroup)

        busy = self._busy(application, path, now)
        application.reasons = self.claims.reasons(
            application.cgroup, path, busy=busy, inhibited=False)

        if application.reasons:
            # Something here matters more than the battery.
            self._thaw(application)
            self._release(application, keep=True)
            return

        rung = policy.rung_for(held, self.on_battery)
        if rung is None:
            self._thaw(application)
            self._release(application, keep=True)
            return

        if now - application.since < rung.after_seconds:
            return

        wanted: dict[str, int] = {}
        if rung.cpu_weight is not None:
            wanted["CPUWeight"] = rung.cpu_weight
        if rung.io_weight is not None:
            wanted["IOWeight"] = rung.io_weight

        if wanted and wanted != application.applied:
            if cgroups.set_properties(application.unit, wanted):
                application.applied = dict(wanted)
        if not wanted and application.applied:
            self._release(application, keep=True)

        if rung.uclamp_max != application.uclamp:
            if cgroups.set_uclamp(application.cgroup, rung.uclamp_max):
                application.uclamp = rung.uclamp_max

        self._consider_freezing(application, now)

    def _busy(self, application: Application, path, now: float) -> bool:
        """Has this application done real work recently?

        An application running a download, a build, an upload or a sync is
        above the floor, because moving bytes costs processor time. This one
        cheap test stands in for most of "is it doing something?".
        """
        used = cpu_usec(path)
        if not application.cpu_mark_at:
            application.cpu_mark, application.cpu_mark_at = used, now
            # Nothing is judged on its first sample.
            return True
        window = now - application.cpu_mark_at
        if window < 30:
            return not quiet(application.cpu_mark, used, window)
        busy = not quiet(application.cpu_mark, used, window)
        application.cpu_mark, application.cpu_mark_at = used, now
        return busy

    def _consider_freezing(self, application: Application, now: float) -> None:
        if not self.freezing or not self.on_battery:
            return
        if application.frozen:
            if now - application.frozen_at >= FREEZE_REFRESH_SECONDS:
                # Let it catch up, then judge it again from scratch.
                self._thaw(application)
            return
        if application.state != policy.HIDDEN:
            return
        if now - application.since < FREEZE_AFTER_SECONDS:
            return
        if cgroups.freeze(application.unit):
            # Bounded: if it has not actually frozen, do not pretend it has.
            if cgroups.frozen(application.cgroup):
                application.frozen = True
                application.frozen_at = now
            else:
                cgroups.thaw(application.unit)

    # -- undoing ----------------------------------------------------------

    def _thaw(self, application: Application) -> bool:
        if not application.frozen:
            return False
        cgroups.thaw(application.unit)
        application.frozen = False
        application.frozen_at = 0.0
        return True

    def _release(self, application: Application, keep: bool = False) -> None:
        """Put an application back exactly as it was found."""
        if application.applied:
            cgroups.clear_properties(application.unit, list(application.applied))
            application.applied = {}
        if application.uclamp is not None:
            cgroups.set_uclamp(application.cgroup, None)
            application.uclamp = None
        if not keep:
            self._thaw(application)

    def release_all(self) -> None:
        """On the way out, leave nothing behind.

        Called when the service stops, when the person turns the feature off,
        and when the Shell stops reporting. Whatever the reason, a machine
        without Luma Energy running must behave exactly as it did before it
        was installed.
        """
        for application in list(self.applications.values()):
            self._thaw(application)
            self._release(application)
        self.applications.clear()

    # -- what the person is shown ------------------------------------------

    def describe(self) -> list[dict[str, object]]:
        return [{
            "cgroup": a.cgroup,
            "unit": a.unit,
            "state": a.state,
            "paused": a.frozen,
            "limits": dict(a.applied),
            "uclamp": a.uclamp if a.uclamp is not None else 0,
            "reasons": list(a.reasons),
        } for a in sorted(self.applications.values(), key=lambda a: a.unit)]
