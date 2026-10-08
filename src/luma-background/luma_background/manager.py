# SPDX-License-Identifier: MPL-2.0
"""The decisions: what is registered, what may run, when it is woken.

Everything that touches the outside world goes through a port -- systemd,
the agents' bus names, the portal's permission table, the prompt, the clock
-- so this module can be tested without a session and read without one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol

from . import ids, journal, units
from .categories import CATEGORIES, WAKE_REASONS
from .cgroup import CpuSampler, Usage
from .policy import PolicyStore
from .registry import AgentRecord, Registry
from .state import Schedule, StateStore

MISSED_THRESHOLD_SECONDS = 120


class NotFound(LookupError):
    pass


class Refused(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class UnitStatus:
    active_state: str = "inactive"      # active | activating | deactivating | inactive | failed | reloading
    sub_state: str = ""
    result: str = ""                    # success | exit-code | signal | core-dump | oom-kill | ...
    control_group: str = ""
    restarts: int = 0
    active_enter_usec: int = 0
    freezer_state: str = "running"      # running | frozen | freezing | thawing | frozen-by-parent
    main_pid: int = 0


class SystemdPort(Protocol):
    def sync_units(self, files: dict[str, str], links: dict[str, str], owned: Iterable[str]) -> bool: ...
    def reload(self, done: Callable[[bool], None]) -> None: ...
    def start(self, unit: str, done: Callable[[bool, str], None]) -> None: ...
    def stop(self, unit: str, done: Callable[[bool, str], None]) -> None: ...
    def reset_failed(self, unit: str) -> None: ...
    def freeze(self, unit: str, done: Callable[[bool, str], None]) -> None: ...
    def thaw(self, unit: str, done: Callable[[bool, str], None]) -> None: ...
    def status(self, unit: str) -> UnitStatus: ...
    def usage(self, status: UnitStatus) -> Usage: ...
    def process_unit(self, pid: int) -> str: ...
    def set_limits(self, unit: str, memory_high: int, memory_max: int) -> None: ...


class AgentBusPort(Protocol):
    def deliver_wake(self, agent: str, reason: str, details: dict, done: Callable[[bool], None]) -> None: ...


class PermissionPort(Protocol):
    def set(self, app: str, allowed: bool) -> None: ...


class PromptPort(Protocol):
    def ask(self, record: AgentRecord, reason: str, answer: Callable[[str | None], None]) -> None: ...


@dataclass(slots=True)
class _Pending:
    """Wakes held back while Power Saver pauses an agent, coalesced by reason."""

    reasons: dict[str, dict] = field(default_factory=dict)


class Manager:
    def __init__(self, registry: Registry, policy: PolicyStore, state: StateStore,
                 systemd: SystemdPort, bus: AgentBusPort, permissions: PermissionPort,
                 prompt: PromptPort, *, clock: Callable[[], float] = time.time,
                 on_changed: Callable[[str | None], None] = lambda app: None) -> None:
        self.registry = registry
        self.policy = policy
        self.state = state
        self.systemd = systemd
        self.bus = bus
        self.permissions = permissions
        self.prompt = prompt
        self.clock = clock
        self.on_changed = on_changed
        self.agents: dict[str, AgentRecord] = {}
        self.power_saver = False
        self.portal_apps: set[str] = set()
        self._pending: dict[str, _Pending] = {}
        #: The scope a Flatpak agent's sandbox moved itself into. `flatpak run`
        #: always leaves the unit that started it, so this is where its limits,
        #: accounting and pausing have to be applied.
        self._scopes: dict[str, str] = {}
        self._frozen: set[str] = set()
        self._restarts_seen: dict[str, int] = {}
        self._last_active: dict[str, str] = {}
        self._prompts_open: dict[str, list[Callable[[str | None], None]]] = {}
        #: Apps woken at least once this session. A refresh starts only agents
        #: that have not been, so an agent a person stopped stays stopped until
        #: its next wake rather than returning at the next rescan.
        self._launched: set[str] = set()
        # Authenticated first-party windows may use their existing host owner
        # while open without changing the person's background decision.
        self._foreground: dict[str, set[str]] = {}
        self._cpu = CpuSampler()
        self._logged_problems: set[tuple[str, str]] = set()

    # -- Registry ------------------------------------------------------------

    def refresh(self, *, login: bool = False, then: Callable[[], None] | None = None) -> None:
        """Rescan, rewrite units, and start what newly may run.

        At login every allowed agent that wakes at login starts. Later, an
        agent that appears (an app was installed, or turned on elsewhere) is
        started the same way once, as if the session had just begun for it.
        `then` runs once systemd has loaded the rewritten units.
        """

        changed_files = self._scan_and_sync()

        def after_reload(_ok: bool = True) -> None:
            for app, record in sorted(self.agents.items()):
                if not self._may_run(record):
                    continue
                if app in self._launched:
                    continue
                if "login" in record.wake:
                    self.wake(app, "login", {})
            if login:
                self._deliver_missed_schedules()
            self._start_timers()
            if then is not None:
                then()
            self.on_changed(None)

        if changed_files:
            self.systemd.reload(after_reload)
        else:
            after_reload()

    def prepare(self, then: Callable[[], None]) -> None:
        """Before the service reports ready: register, write units and masks, reload.

        systemd orders the session's own autostart after this service is
        ready, and a mask only counts once the user manager has reloaded. So
        the first scan, the generated units and the masks over autostart
        entries that agents replace are all loaded before the bus name is
        taken; nothing is started here.
        """

        if self._scan_and_sync():
            self.systemd.reload(lambda _ok=True: then())
        else:
            then()

    def _scan_and_sync(self) -> bool:
        result = self.registry.scan(self.portal_apps)
        for app, problem in result.problems:
            if (app, problem) not in self._logged_problems:
                self._logged_problems.add((app, problem))
                journal.send(f"Ignored background registration: {problem}", priority="warning",
                             app_id=app if ids.is_app_id(app) else "", event="registration-refused")
        previous = self.agents
        self.agents = result.agents
        for app in tuple(self._foreground):
            record = self.agents.get(app)
            if record is None or record.origin != 'native' or not record.trusted_install or not record.has_unit:
                self._foreground.pop(app, None)
                old = previous.get(app)
                if old is not None and old.has_unit:
                    self.systemd.stop(old.unit, lambda _ok, _message: None)
        for app in sorted(set(self.agents) - set(previous)):
            record = self.agents[app]
            journal.send(f"Registered {record.origin} background agent for {record.name} "
                         f"({record.category.id})", app_id=app, event="registered")
        for app in sorted(set(previous) - set(self.agents)):
            journal.send("Background agent unregistered", app_id=app, event="unregistered")
            self._pending.pop(app, None)
            self._foreground.pop(app, None)
        return self._sync_units()

    def _unit_files(self) -> tuple[dict[str, str], dict[str, str]]:
        files: dict[str, str] = {}
        links: dict[str, str] = {}
        for app, record in self.agents.items():
            # While this service runs an app's agent, the session's autostart
            # of that app (the portal's entry, or the app's own fallback entry
            # naming the agent) stays masked, whether the switch is on or off:
            # the switch decides, and the app never runs a second time.
            if record.has_unit:
                for name in record.autostart_units:
                    links[name] = units.MASKED
            if not record.has_unit or not (self._allowed(record) or self._foreground.get(app)):
                continue
            files[record.unit] = units.agent_service(units.AgentUnitSpec(
                app_id=app, name=record.name, agent=record.agent,
                command=record.command, category=record.category,  # type: ignore[arg-type]
            ))
            if not self._allowed(record):
                continue  # A foreground lease never enables background timers.
            if record.interval_seconds and record.agent:
                files.update(units.schedule_units(
                    app, record.name, "interval", every=record.interval_seconds,
                    accuracy=record.category.schedule_accuracy_seconds))
            if record.agent:
                for schedule in self.state.app(app).schedules.values():
                    if schedule.name == "interval":
                        continue
                    if schedule.at and schedule.at <= self.clock() - MISSED_THRESHOLD_SECONDS:
                        continue
                    files.update(units.schedule_units(
                        app, record.name, schedule.name, at=schedule.at, every=schedule.every,
                        accuracy=schedule.accuracy))
        return files, links

    def _sync_units(self) -> bool:
        files, links = self._unit_files()
        return self.systemd.sync_units(files, links, owned=())

    def _start_timers(self) -> None:
        files, _links = self._unit_files()
        for name in sorted(files):
            if name.endswith(".timer"):
                self.systemd.start(name, lambda ok, message, name=name: ok or journal.send(
                    f"Could not start {name}: {message}", priority="warning", event="timer-failed"))

    # -- Policy --------------------------------------------------------------

    def _allowed(self, record: AgentRecord) -> bool:
        return self.policy.effective(record.app_id, trusted_install=record.trusted_install).allowed

    def _may_run(self, record: AgentRecord) -> bool:
        return record.has_unit and self._allowed(record)

    def record(self, app: str) -> AgentRecord:
        if not ids.is_app_id(app) or app not in self.agents:
            raise NotFound(app)
        return self.agents[app]

    def set_allowed(self, app: str, allowed: bool, source: str) -> None:
        record = self.record(app)
        changed = self.policy.set_decision(app, allowed, source)
        if record.origin == "flatpak":
            self.permissions.set(app, allowed)
        if not changed:
            return
        journal.send(
            f"{record.name} {'may' if allowed else 'may not'} work in the background "
            f"(decided in {source})",
            app_id=app, event="allowed" if allowed else "denied", LUMA_BACKGROUND_SOURCE=source,
        )
        if allowed:
            def woken() -> None:
                current = self.agents.get(app)
                if current is not None and current.has_unit:
                    self.wake(app, "request", {})

            # The decision is the wake: an agent allowed now starts with
            # "request", once its unit is loaded, rather than as a login.
            if record.has_unit:
                self._launched.add(app)
            self.refresh(then=woken)
            self._resolve_prompt(app, "allow")
        else:
            self._resolve_prompt(app, "deny")
            self._pending.pop(app, None)
            if record.has_unit and not self._foreground.get(app):
                self._stop(app, only_if_background_denied=True)
            self.refresh()
        self.on_changed(app)

    def portal_permission_changed(self, app: str, value: str, previous: str) -> None:
        """The portal's permission table changed underneath us.

        `yes` over an absent entry is the portal answering an app's first
        request by itself; anything else is a person using the portal's
        permission tools, which is their decision to make.
        """

        if not ids.is_app_id(app):
            return
        self.portal_apps.add(app)
        if value not in {"yes", "no"}:
            return
        current = self.policy.decision(app).value
        wanted = "allow" if value == "yes" else "deny"
        if current == wanted:
            return
        if value == "yes" and not previous and not current:
            self.refresh()
            record = self.agents.get(app)
            if record is None:
                return
            effective = self.policy.effective(app, trusted_install=record.trusted_install)
            if effective.default == "on":
                self.policy.set_decision(app, True, "portal")
                return
            if effective.default == "off":
                # A Luma app that is off by default asked; the person decides.
                pass
            if not effective.prompted:
                self.ask(app, "", lambda answer: None)
            return
        if app in self.agents:
            self.set_allowed(app, value == "yes", "portal")
        else:
            self.policy.set_decision(app, value == "yes", "portal")

    def portal_notify_background(self, app: str, reply: Callable[[int], None]) -> None:
        """A sandboxed app is running with no windows and has no decision.

        Answers use the portal's vocabulary: 0 forbid (the portal ends the
        app), 1 allow, 2 allow this instance without deciding anything.
        """

        if app not in self.agents:
            self.portal_apps.add(app)
            self.refresh()
        record = self.agents.get(app)
        if record is None:
            reply(2)
            return
        effective = self.policy.effective(app, trusted_install=record.trusted_install)
        if effective.decision == "allow":
            reply(1)
        elif effective.decision == "deny":
            reply(0)
        elif effective.default == "on":
            self.policy.set_decision(app, True, "portal")
            reply(1)
        elif effective.prompted:
            reply(2)
        else:
            self.ask(app, "", lambda answer: reply({"allow": 1, "deny": 0}.get(answer or "", 2)))

    def cancel_prompt(self, app: str) -> None:
        cancel = getattr(self.prompt, "dismiss", None)
        if cancel is not None:
            cancel(app)

    # -- Prompt --------------------------------------------------------------

    def ask(self, app: str, reason: str, reply: Callable[[str | None], None]) -> None:
        """Show the one-time prompt; every caller waiting on it gets the answer."""

        record = self.record(app)
        waiting = self._prompts_open.get(app)
        if waiting is not None:
            waiting.append(reply)
            return
        self._prompts_open[app] = [reply]
        self.policy.mark_prompted(app)
        journal.send(f"Asking whether {record.name} may work in the background",
                     app_id=app, event="prompt")

        def answered(answer: str | None) -> None:
            if answer in {"allow", "deny"} and self.policy.decision(app).value == "":
                self.set_allowed(app, answer == "allow", "prompt")
            self._resolve_prompt(app, answer)

        self.prompt.ask(record, reason, answered)

    def _resolve_prompt(self, app: str, answer: str | None) -> None:
        for reply in self._prompts_open.pop(app, []):
            reply(answer)

    def request_background(self, app: str, *, reason: str, autostart: bool,
                           reply: Callable[[int, dict], None]) -> None:
        record = self.record(app)
        effective = self.policy.effective(app, trusted_install=record.trusted_install)

        def finish(allowed: bool, response: int, *, wake: bool = True) -> None:
            started = bool(allowed and autostart and record.has_unit)
            if started and wake:
                self.wake(app, "request", {})
            reply(response, {"background": allowed, "autostart": started})

        if effective.decision == "allow" or (effective.decision == "unset" and effective.default == "on"):
            finish(True, 0)
            return
        if effective.decision == "deny":
            finish(False, 1)
            return
        if effective.prompted and app not in self._prompts_open:
            # Asked before and dismissed without an answer: never nag.
            finish(False, 2)
            return
        # Allowing through the prompt already wakes the agent (set_allowed).
        self.ask(app, reason, lambda answer: finish(
            answer == "allow", {"allow": 0, "deny": 1}.get(answer or "", 2), wake=False))

    # -- Running -------------------------------------------------------------

    def acquire_foreground(self, app: str, owner: str, reply: Callable[[bool], None]) -> None:
        """Use the same bounded native owner until this verified UI disconnects.

        Admission belongs to the D-Bus service; neither an app ID nor command
        is supplied by the caller. Leases are session-only and bounded.
        """
        record = self.record(app)
        if record.origin != "native" or not record.trusted_install or not record.has_unit:
            raise Refused("a trusted native service is required")
        if not owner.startswith(":") or len(owner) > 255:
            raise ValueError("a unique bus owner is required")
        owners = self._foreground.setdefault(app, set())
        if owner not in owners and len(owners) >= 32:
            raise Refused("too many foreground clients")
        owners.add(owner)

        def start(_ok: bool = True) -> None:
            if owner not in self._foreground.get(app, ()) or self.agents.get(app) != record:
                self.release_foreground(owner, app)
                reply(False)
                return
            if not _ok:
                self.release_foreground(owner, app)
                reply(False)
                return
            def started(ok: bool, _message: str) -> None:
                if owner not in self._foreground.get(app, ()) or self.agents.get(app) != record:
                    self.release_foreground(owner, app)
                    if app in self.agents and not self._allowed(self.agents[app]) and not self._foreground.get(app):
                        self._stop(app, only_if_background_denied=True)
                    reply(False)
                    return
                if not ok:
                    self.release_foreground(owner, app)
                self.on_changed(app)
                reply(ok)
            # Power Saver must not freeze work the person is using now.
            self._thaw_app(app, lambda: self.systemd.start(record.unit, started))

        if self._sync_units():
            self.systemd.reload(start)
        else:
            start()

    def release_foreground(self, owner: str, app: str | None = None) -> None:
        for app_id, owners in tuple(self._foreground.items()):
            if app is not None and app_id != app:
                continue
            if owner not in owners:
                continue
            owners.discard(owner)
            if owners:
                continue
            self._foreground.pop(app_id, None)
            record = self.agents.get(app_id)
            if record is not None:
                if not self._allowed(record):
                    self._pending.pop(app_id, None)
                    self._stop(app_id, only_if_background_denied=True)
                elif self.power_saver and record.category.pauses_in_power_saver:
                    self._freeze_app(app_id)
            if self._sync_units():
                self.systemd.reload(lambda _ok: None)
            self.on_changed(app_id)

    def wake(self, app: str, reason: str, details: dict) -> bool:
        """Deliver a wake, starting the agent first if it is not running."""

        if reason not in WAKE_REASONS:
            raise ValueError(f"unknown wake reason: {reason}")
        record = self.record(app)
        if not self._may_run(record):
            return False
        if reason in ("login", "network", "resume", "schedule") and reason not in record.wake:
            return False
        self._launched.add(app)
        if self.power_saver and record.category.pauses_in_power_saver and not self._foreground.get(app):
            self._pending.setdefault(app, _Pending()).reasons[reason] = dict(details)
            return True
        status = self.systemd.status(record.unit)
        self.state.record_run(app, int(self.clock() * 1e6))
        self.state.flush()
        if status.active_state == "active":
            self._deliver(record, reason, details)
            self.on_changed(app)
            return True
        if status.active_state == "failed":
            self.systemd.reset_failed(record.unit)

        def started(ok: bool, message: str) -> None:
            if not ok:
                journal.send(f"Could not start the background agent: {message}",
                             priority="warning", app_id=app, event="start-failed")
                self.on_changed(app)
                return
            self._deliver(record, reason, details)
            self.on_changed(app)

        journal.send(f"Starting background agent ({reason})", app_id=app, event="start",
                     LUMA_BACKGROUND_WAKE=reason)
        self.systemd.start(record.unit, started)
        return True

    def _deliver(self, record: AgentRecord, reason: str, details: dict) -> None:
        if not record.agent or not record.command or not record.command.owns_bus_name:
            return
        if reason == "login" and "login" in record.wake:
            pass
        self.bus.deliver_wake(record.agent, reason, details, lambda ok: ok or journal.send(
            f"The agent did not accept the {reason} wake", priority="notice",
            app_id=record.app_id, event="wake-undelivered"))

    def wake_all(self, reason: str) -> None:
        for app, record in sorted(self.agents.items()):
            if reason in record.wake and self._may_run(record):
                self.wake(app, reason, {})

    def stop_now(self, app: str) -> None:
        record = self.record(app)
        if not record.has_unit:
            raise NotFound(app)
        self._pending.pop(app, None)
        self._foreground.pop(app, None)
        self._stop(app)
        if self._sync_units():
            self.systemd.reload(lambda _ok: None)

    def _stop(self, app: str, *, only_if_background_denied: bool = False) -> None:
        """Thaw before stopping: systemd refuses to stop a frozen unit."""
        record = self.agents[app]

        def stop(*_ignored) -> None:
            if only_if_background_denied:
                current = self.agents.get(app)
                if current != record or self._foreground.get(app) or self._allowed(record):
                    return
            self.systemd.stop(record.unit, lambda ok, message: self._after_stop(app, ok, message))

        if self._frozen & {record.unit, self._scopes.get(app, "")}:
            self._thaw_app(app, stop)
        else:
            stop()

    def _after_stop(self, app: str, ok: bool, message: str) -> None:
        if ok:
            self.state.record_exit(app, "stopped")
            self.state.flush()
        self.on_changed(app)

    # -- Power Saver ---------------------------------------------------------

    def _pausable_units(self, app: str) -> list[str]:
        record = self.agents[app]
        found = [record.unit]
        scope = self._scopes.get(app)
        if scope:
            found.append(scope)
        return found

    def set_power_saver(self, active: bool) -> None:
        """Pause, not stop: a paused agent resumes exactly where it was.

        Each agent is frozen by itself rather than through its slice. systemd
        silently skips freezing a slice when any child cannot freeze, refuses
        to stop anything frozen by a parent, and a Flatpak's sandbox is not in
        the slice at all.
        """

        if active == self.power_saver:
            return
        self.power_saver = active
        journal.send("Power Saver is on: pausing background agents that can wait"
                     if active else "Power Saver is off: resuming paused background agents",
                     event="power-saver")
        if active:
            for app, record in sorted(self.agents.items()):
                if record.has_unit and record.category.pauses_in_power_saver and not self._foreground.get(app):
                    if self.systemd.status(record.unit).active_state == "active":
                        self._freeze_app(app)
        else:
            for app in sorted({ids.parse_agent_unit(unit) or "" for unit in self._frozen} - {""}):
                if app in self.agents:
                    self._thaw_app(app, lambda: None)
            self._frozen.clear()
            pending, self._pending = self._pending, {}
            for app, held in sorted(pending.items()):
                if app not in self.agents:
                    continue
                for reason, details in held.reasons.items():
                    self.wake(app, reason, details)
        self.on_changed(None)

    def _freeze_app(self, app: str) -> None:
        for unit in self._pausable_units(app):
            def done(ok: bool, message: str, unit=unit) -> None:
                if ok:
                    self._frozen.add(unit)
                    if not self.power_saver or self._foreground.get(app) or app not in self.agents:
                        # A freeze sent before a window opened may complete
                        # afterwards. Do not leave that foreground work frozen.
                        def thawed(thawed_ok: bool, thaw_message: str) -> None:
                            if thawed_ok:
                                self._frozen.discard(unit)
                            else:
                                journal.send(f'Could not resume {unit}: {thaw_message}',
                                    priority='warning', app_id=app, event='thaw-failed')
                            self.on_changed(app)
                        self.systemd.thaw(unit, thawed)
                else:
                    journal.send(f"Could not pause {unit}: {message}", priority="warning",
                                 app_id=app, event="freeze-failed")
                self.on_changed(app)
            self.systemd.freeze(unit, done)

    def _thaw_app(self, app: str, then: Callable[[], None]) -> None:
        targets = [unit for unit in self._pausable_units(app) if unit in self._frozen]
        if not targets:
            then()
            return
        remaining = set(targets)
        for unit in targets:
            def done(ok: bool, message: str, unit=unit) -> None:
                if not ok:
                    journal.send(f"Could not resume {unit}: {message}", priority="warning",
                                 app_id=app, event="thaw-failed")
                self._frozen.discard(unit)
                remaining.discard(unit)
                if not remaining:
                    then()
                    self.on_changed(app)
            self.systemd.thaw(unit, done)

    # -- Schedules -----------------------------------------------------------

    def schedule(self, app: str, name: str, *, at: int = 0, every: int = 0,
                 accuracy: int | None = None) -> None:
        record = self.record(app)
        ids.schedule_name(name)
        if name == "interval":
            raise ValueError("the schedule name 'interval' is reserved for the declaration")
        if not record.agent:
            raise Refused("only a declared agent can schedule wakes")
        if "schedule" not in record.wake:
            raise Refused("the declaration does not include the schedule wake")
        if bool(at) == bool(every):
            raise ValueError("pass exactly one of at and every")
        if every and every < 300:
            raise ValueError("a repeating schedule is at least 300 seconds")
        if at and at > self.clock() + 400 * 86400:
            raise ValueError("a schedule may be at most 400 days ahead")
        if accuracy is None:
            accuracy = record.category.schedule_accuracy_seconds
        self.state.set_schedule(app, Schedule(name, int(at), int(every), max(1, min(int(accuracy), 3600))))
        self.state.flush()
        if at and at <= self.clock():
            self.scheduled_wake(app, name)
            return
        self.refresh()

    def unschedule(self, app: str, name: str) -> None:
        self.record(app)
        ids.schedule_name(name)
        if self.state.remove_schedule(app, name):
            self.state.flush()
            timer = ids.schedule_timer(app, name)
            self.systemd.stop(timer, lambda ok, message: None)
            self.refresh()

    def scheduled_wake(self, app: str, name: str) -> None:
        record = self.record(app)
        schedule = self.state.app(app).schedules.get(name)
        details: dict = {"schedule": name}
        if name != "interval" and schedule is None:
            return
        if schedule is not None and schedule.at:
            details["missed"] = self.clock() - schedule.at > MISSED_THRESHOLD_SECONDS
            self.state.remove_schedule(app, name)
            self.state.flush()
            self.systemd.stop(ids.schedule_timer(app, name), lambda ok, message: None)
            self.refresh()
        else:
            details["missed"] = False
        if record.app_id in self.agents:
            self.wake(app, "schedule", details)

    def _deliver_missed_schedules(self) -> None:
        now = self.clock()
        for app, record in sorted(self.agents.items()):
            for schedule in list(self.state.app(app).schedules.values()):
                if schedule.at and schedule.at <= now:
                    self.scheduled_wake(app, schedule.name)

    # -- Unit events ---------------------------------------------------------

    def unit_changed(self, unit: str) -> None:
        app = ids.parse_agent_unit(unit)
        if app is None or app not in self.agents:
            return
        status = self.systemd.status(unit)
        record = self.agents[app]
        if status.active_state == "active" and record.origin == "flatpak" and status.main_pid:
            scope = self.systemd.process_unit(status.main_pid)
            if scope and scope != unit and units.is_flatpak_scope_for(scope, app) \
                    and self._scopes.get(app) != scope:
                self._scopes[app] = scope
                self.systemd.set_limits(scope, record.category.memory_high, record.category.memory_max)
                journal.send(f"Applied background limits to the sandbox scope {scope}",
                             app_id=app, event="limits-applied")
                if self.power_saver and record.category.pauses_in_power_saver:
                    self._freeze_app(app)
        elif status.active_state in {"inactive", "failed"}:
            self._scopes.pop(app, None)
            self._frozen.discard(unit)
        before = self._last_active.get(app, "")
        self._last_active[app] = status.active_state
        restarts = status.restarts
        seen = self._restarts_seen.get(app, restarts)
        if restarts > seen:
            journal.send("Background agent crashed and is being restarted", priority="warning",
                         app_id=app, event="crashed", LUMA_BACKGROUND_RESULT=status.result or "unknown")
            self.state.record_exit(app, "out-of-memory" if status.result == "oom-kill" else "crashed")
            self.state.flush()
        self._restarts_seen[app] = restarts
        if status.active_state == before:
            self.on_changed(app)
            return
        if status.active_state == "failed":
            how = "out-of-memory" if status.result == "oom-kill" else "crashed"
            journal.send("Background agent failed repeatedly; it will be tried again at its next wake",
                         priority="error", app_id=app, event="failed",
                         LUMA_BACKGROUND_RESULT=status.result or "unknown")
            self.state.record_exit(app, how)
            self.state.flush()
        elif status.active_state == "inactive" and before in {"active", "deactivating"}:
            if status.result in {"", "success"}:
                if self.state.app(app).last_exit != "stopped":
                    self.state.record_exit(app, "clean")
            else:
                self.state.record_exit(app, "out-of-memory" if status.result == "oom-kill" else "crashed")
            self.state.flush()
        self.on_changed(app)

    # -- Reporting -----------------------------------------------------------

    def describe(self, app: str, reader: str = "") -> dict:
        record = self.record(app)
        effective = self.policy.effective(app, trusted_install=record.trusted_install)
        app_state = self.state.app(app)
        status = self.systemd.status(record.unit) if record.has_unit else UnitStatus()
        usage = Usage()
        if status.active_state in {"active", "deactivating"}:
            usage = self.systemd.usage(status)
            scope = self._scopes.get(app)
            if scope:
                inner = self.systemd.usage(self.systemd.status(scope))
                usage = Usage(usage.memory + inner.memory, max(usage.memory_peak, inner.memory_peak),
                              usage.cpu_usec + inner.cpu_usec, usage.frozen or inner.frozen,
                              usage.oom_kills + inner.oom_kills)
        if not effective.allowed and not self._foreground.get(app):
            state = "off"
        elif status.active_state in {"active", "activating", "deactivating", "reloading"}:
            paused = usage.frozen or status.freezer_state.startswith("frozen") or record.unit in self._frozen
            state = "paused" if paused else "running"
        elif status.active_state == "failed":
            state = "failed"
        elif self.power_saver and record.category.pauses_in_power_saver:
            state = "paused"
        else:
            state = "idle"
        schedules = sorted(app_state.schedules)
        if record.interval_seconds:
            schedules = ["interval", *schedules]
        return {
            "app-id": app,
            "agent": record.agent,
            "name": record.name,
            "icon": record.icon,
            "category": record.category.id,
            "explanation": record.category.explanation(record.name),
            "consequence": record.category.consequence(record.name),
            "allowed": effective.allowed,
            "foreground": bool(self._foreground.get(app)),
            "decision": effective.decision,
            "default": effective.default,
            "essential": effective.essential,
            "origin": record.origin,
            "state": state,
            "last-run": app_state.last_run_usec,
            "last-exit": app_state.last_exit,
            "restarts": status.restarts,
            "memory": usage.memory,
            "memory-peak": usage.memory_peak,
            "cpu-usec": usage.cpu_usec,
            "cpu-percent": self._cpu.percent(reader, app, usage.cpu_usec) if usage.cpu_usec else 0.0,
            "wake": list(record.wake),
            "publishes": list(record.publishes),
            "schedules": schedules,
            "memory-high": record.category.memory_high,
            "memory-max": record.category.memory_max,
            "pauses-in-power-saver": record.category.pauses_in_power_saver,
            "unit": record.unit,
        }

    def list(self, reader: str = "") -> list[dict]:
        described = [self.describe(app, reader) for app in self.agents]
        return sorted(described, key=lambda item: (item["name"].casefold(), item["app-id"]))
