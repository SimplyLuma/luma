# SPDX-License-Identifier: Apache-2.0

"""Calendar's background agent (ADR-033): reminders with every window closed.

``prairie-calendar --agent`` watches every enabled calendar and task list in
Evolution Data Server — local ones, CalDAV and Google accounts, and the shared
calendars Luma Connect keeps in EDS — and shows each reminder as a Luma
notification with Open, Snooze and Dismiss.

It is built on ``ECal.ReminderWatcher``, the engine evolution-alarm-notify
uses, not on evolution-alarm-notify itself: the watcher expands recurrences,
applies default reminders, remembers past and snoozed reminders across
restarts (GSettings org.gnome.evolution-data-server.calendar) and records the
last notified time per calendar, so reminders that fell due while the machine
was asleep or off are shown when it comes back. evolution-alarm-notify would
also act on the same reminders (and draw its own GTK 3 notifications), so
prairie-core-apps holds it off with a unit drop-in; see data/agents.

On resume and when the network returns the agent re-checks the timers at once
and asks each open calendar to refresh, since EDS's own refresh timers do not
count time spent asleep.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("EDataServer", "1.2")
gi.require_version("ECal", "2.0")
gi.require_version("ICalGLib", "3.0")
from gi.repository import ECal, EDataServer, GLib  # noqa: E402

from .background_agent import Agent, AgentInfo, Notifier, configure_logging, launch_app  # noqa: E402

LOG = logging.getLogger("prairie.calendar.agent")

APP_ID = "org.projectluma.Calendar"
INFO = AgentInfo(
    APP_ID, "Calendar", "calendar", ("prairie-calendar", "--agent"),
    wake=("login", "network", "resume", "schedule"),
    publishes=("active-reminders", "snoozed-reminders", "live-extension:org.projectluma.Calendar.Reminders"),
    purpose="Get reminders when Calendar is closed",
)
# Snooze length; the session test shortens it to see a snoozed reminder come back.
SNOOZE_MINUTES = max(1, int(os.environ.get("LUMA_CALENDAR_SNOOZE_MINUTES", "10") or 10))
# Reminders already shown before a restart are not shown again; past this age
# a reminder left from before the agent started is not raised at all.
PAST_LIMIT_SECONDS = 12 * 3600


def state_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return base / "prairie/calendar/reminders-shown.json"


def reminder_key(rd) -> str:
    component = rd.get_component()
    instance = rd.get_instance()
    identity = component.get_id()
    return "|".join((rd.get_source_uid() or "", identity.get_uid() or "", identity.get_rid() or "",
                     instance.get_uid() or "", str(int(instance.get_occur_start()))))


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    getter = getattr(value, "get_value", None)
    return (getter() or "") if callable(getter) else ""


def describe_time(start: int, end: int, now: float, *, all_day: bool) -> str:
    """"Now", "In 10 minutes · 14:00", "Started 25 minutes ago", "Tomorrow" …"""
    moment = datetime.fromtimestamp(start).astimezone()
    today = datetime.fromtimestamp(now).astimezone().date()
    clock = moment.strftime("%H:%M")
    delta = start - now
    if all_day:
        if moment.date() == today:
            return "Today"
        return "Tomorrow" if (moment.date() - today).days == 1 else moment.strftime("%A %-d %B")
    if end and end < now:
        return f"Ended at {clock}"
    if abs(delta) < 60:
        return f"Now · {clock}"
    if delta > 0:
        minutes = round(delta / 60)
        if minutes < 60:
            return f"In {minutes} minute{'s' if minutes != 1 else ''} · {clock}"
        if moment.date() == today:
            return f"Today at {clock}"
        if (moment.date() - today).days == 1:
            return f"Tomorrow at {clock}"
        return moment.strftime("%a %-d %b at %H:%M")
    minutes = round(-delta / 60)
    if minutes < 60:
        return f"Started {minutes} minute{'s' if minutes != 1 else ''} ago · {clock}"
    return f"Started at {clock}"


class CalendarReminders:
    def __init__(self, *, clock=time.time) -> None:
        self.clock = clock
        self.agent: Agent | None = None
        self.notifier: Notifier | None = None
        self.registry = None
        self.watcher = None
        self._handlers: list[int] = []
        self._shown: dict[str, float] = {}
        self._reminders: dict[str, object] = {}

    # -- lifecycle -------------------------------------------------------------
    def start(self, agent: Agent) -> None:
        self.agent = agent
        self.notifier = Notifier(agent.connection, APP_ID, "Calendar")
        self._load_shown()
        self.registry = EDataServer.SourceRegistry.new_sync(None)
        self.watcher = ECal.ReminderWatcher.new(self.registry)
        self._handlers = [self.watcher.connect("triggered", self._triggered),
                          self.watcher.connect("changed", self._changed)]
        # Reminders that became due while nobody was watching (logged out, or the
        # agent restarted) and were never shown.
        GLib.idle_add(self._show_unshown_past)
        agent.set_state("running", "Watching calendars")

    def wake(self, event: str) -> None:
        if self.watcher is None or event not in {"resume", "network", "unlock"}:
            return
        if event in {"resume", "network"}:
            self._refresh_calendars()
        # The watcher's own timers are monotonic; look at the wall clock now.
        self.watcher.timer_elapsed()

    def stop(self) -> None:
        if self.watcher is not None:
            for handler in self._handlers:
                self.watcher.disconnect(handler)
            self.watcher = None
        if self.notifier is not None:
            self.notifier.close()

    # -- reminders -------------------------------------------------------------
    def _triggered(self, _watcher, reminders, snoozed) -> None:
        for rd in list(reminders or []):
            self._show(rd, snoozed=bool(snoozed))
        self._publish()

    def _changed(self, _watcher) -> None:
        # A dismissed or deleted event takes its notification with it.
        if self.watcher is None or self.notifier is None:
            return
        alive = {reminder_key(rd) for rd in self.watcher.dup_past() or []}
        alive |= {reminder_key(rd) for rd in self.watcher.dup_snoozed() or []}
        for key in list(self._reminders):
            if key not in alive:
                self._reminders.pop(key, None)
                self.notifier.withdraw(key)
        self._publish()

    def _show_unshown_past(self) -> bool:
        if self.watcher is None:
            return GLib.SOURCE_REMOVE
        now = self.clock()
        for rd in self.watcher.dup_past() or []:
            key = reminder_key(rd)
            if key in self._shown:
                continue
            if now - rd.get_instance().get_time() > PAST_LIMIT_SECONDS:
                continue
            self._show(rd, snoozed=False)
        self._publish()
        return GLib.SOURCE_REMOVE

    def _show(self, rd, *, snoozed: bool) -> None:
        if self.notifier is None:
            return
        key = reminder_key(rd)
        component = rd.get_component()
        instance = rd.get_instance()
        summary = _text(component.get_summary()).strip() or "Untitled event"
        location = (component.get_location() or "").strip()
        start = component.get_dtstart()
        all_day = bool(start is not None and start.get_value() is not None and start.get_value().is_date())
        when = describe_time(int(instance.get_occur_start()), int(instance.get_occur_end()), self.clock(),
                             all_day=all_day)
        body = " · ".join(part for part in (when, location) if part)
        is_task = component.get_vtype() == ECal.ComponentVType.TODO
        self._reminders[key] = rd
        self.notifier.notify(
            key, summary, body,
            actions=[("default", "Open"), ("snooze", f"Snooze {SNOOZE_MINUTES} min"), ("dismiss", "Dismiss")],
            on_action=lambda action, key=key: self._action(key, action),
            category="x-luma.calendar.reminder", urgency=1, resident=True, timeout=0,
            public_summary="Calendar", public_body="Task reminder" if is_task else "Event reminder",
            hints={"x-luma-calendar-source": GLib.Variant("s", rd.get_source_uid() or ""),
                   "x-luma-snoozed": GLib.Variant("b", snoozed)})
        self._shown[key] = self.clock()
        self._save_shown()
        LOG.info("Reminder shown%s", " (snoozed)" if snoozed else "")

    def _action(self, key: str, action: str) -> None:
        rd = self._reminders.get(key)
        if rd is None or self.watcher is None:
            return
        if action == "snooze":
            self.watcher.snooze(rd, int(self.clock()) + SNOOZE_MINUTES * 60)
            self._reminders.pop(key, None)
            self._shown.pop(key, None)
            self._save_shown()
            self.notifier.withdraw(key)
        elif action == "dismiss":
            self._dismiss(key, rd)
        elif action == "default":
            # Opening the event is not dismissing its reminder: it stays in the
            # past reminders until dismissed, just no longer on screen.
            open_event(rd)
            self._reminders.pop(key, None)
            self.notifier.withdraw(key)
        self._publish()

    def _dismiss(self, key: str, rd) -> None:
        self._reminders.pop(key, None)
        self.notifier.withdraw(key)

        def done(watcher, result, *_user):
            try:
                watcher.dismiss_finish(result)
            except GLib.Error as error:
                LOG.info("Reminder could not be dismissed on its calendar: %s", error.message)
            self._publish()
        self.watcher.dismiss(rd, None, done)

    def _refresh_calendars(self) -> None:
        for extension in (EDataServer.SOURCE_EXTENSION_CALENDAR, EDataServer.SOURCE_EXTENSION_TASK_LIST):
            for source in self.registry.list_enabled(extension):
                client = self.watcher.ref_opened_client(source.get_uid())
                if client is None or not client.check_refresh_supported():
                    continue
                client.refresh(None, self._refreshed)

    @staticmethod
    def _refreshed(client, result, *_user) -> None:
        try:
            client.refresh_finish(result)
        except GLib.Error as error:
            LOG.info("A calendar did not refresh: %s", error.message)

    def _publish(self) -> None:
        if self.agent is None or self.watcher is None:
            return
        self.agent.publish("active-reminders", len(self._reminders))
        self.agent.publish("snoozed-reminders", len(self.watcher.dup_snoozed() or []))

    # -- what was shown ----------------------------------------------------------
    def _load_shown(self) -> None:
        try:
            data = json.loads(state_path().read_text())
            self._shown = {str(key): float(value) for key, value in data.items()}
        except (OSError, ValueError, AttributeError):
            self._shown = {}

    def _save_shown(self) -> None:
        cutoff = self.clock() - 7 * 86400
        self._shown = {key: at for key, at in self._shown.items() if at >= cutoff}
        path = state_path()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".shown-", dir=path.parent)
            with os.fdopen(fd, "w") as stream:
                json.dump(self._shown, stream)
            os.replace(temporary, path)
        except OSError as error:
            LOG.info("Shown reminders could not be saved: %s", error)


def open_event(rd) -> None:
    """Open Calendar on the day of the reminder's occurrence, with the event shown."""
    component = rd.get_component()
    day = datetime.fromtimestamp(int(rd.get_instance().get_occur_start())).astimezone().date().isoformat()
    launch_app(APP_ID, ["prairie-calendar", f"--event-uid={component.get_id().get_uid()}", f"--event-day={day}"])


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    reminders = CalendarReminders()
    return Agent(INFO, start=reminders.start, wake=reminders.wake, stop=reminders.stop).run()


__all__ = ["CalendarReminders", "INFO", "describe_time", "main", "reminder_key"]
