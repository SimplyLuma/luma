# SPDX-License-Identifier: Apache-2.0

"""Clock's windowless alarms agent (ADR-033, category alarms).

``prairie-clock --agent`` runs Clock's AlarmService without GTK: the same
scheduling, missed and late alarms, snoozes, timers and ringing (GStreamer), in
a process luma-background starts and limits. It owns org.projectluma.Clock.Agent
from the start, publishes the next alarm or timer and whether one is ringing,
notifies through org.freedesktop.Notifications with Snooze and Stop (the lock
screen sees only that an alarm is ringing unless details are allowed there),
follows edits a Clock window makes to the store, and exits cleanly when nothing
is scheduled — its next wake starts it again. While it runs, Clock windows
leave ringing to it.
"""

from __future__ import annotations

import logging
import sys

from gi.repository import Gio, GLib

from .background_agent import Agent, AgentInfo, Notifier, configure_logging, launch_app
from .clock_alarms import APP_ID, IDLE_EXIT_MS, AlarmNotification, AlarmService

LOG = logging.getLogger("prairie.clock.agent")

INFO = AgentInfo(
    APP_ID, "Clock", "alarms", ("prairie-clock", "--agent"),
    wake=("login", "resume", "schedule"),
    publishes=("next-at", "next-kind", "scheduled", "ringing", f"live-extension:{APP_ID}.Next"),
    purpose="Ring alarms and timers when Clock is closed",
)
URGENCY = {"urgent": 2, "high": 1, "normal": 1}


class AgentApplication:
    """What AlarmService asks of its application, for a process without one."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.actions: dict[str, Gio.SimpleAction] = {}
        self.holds = 0
        self._idle = 0

    def add_action(self, action: Gio.SimpleAction) -> None:
        self.actions[action.get_name()] = action

    def activate_action(self, name: str, target: str) -> None:
        action = self.actions.get(name)
        if action is not None:
            action.activate(GLib.Variant("s", target))

    def get_dbus_connection(self) -> Gio.DBusConnection | None:
        return self.agent.connection

    def hold(self) -> None:
        self.holds += 1
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0

    def release(self) -> None:
        self.holds = max(0, self.holds - 1)
        self.idle_check()

    def idle_check(self) -> None:
        if not self.holds and not self._idle:
            self._idle = GLib.timeout_add(IDLE_EXIT_MS, self._idle_exit)

    def _idle_exit(self) -> bool:
        self._idle = 0
        if not self.holds:
            LOG.info("Nothing scheduled; Clock's agent stops until its next wake")
            self.agent.quit()
        return GLib.SOURCE_REMOVE


class AgentNotifications:
    """AlarmNotification through org.freedesktop.Notifications, buttons back to the service's actions."""

    def __init__(self, notifier: Notifier, application: AgentApplication, service_ref) -> None:
        self.notifier, self.application, self.service_ref = notifier, application, service_ref

    def post(self, identifier: str, spec: AlarmNotification) -> None:
        actions = []
        targets: dict[str, tuple[str, str]] = {}
        if spec.default is not None:
            actions.append(("default", "Open"))
            targets["default"] = spec.default
        for index, (label, action, target) in enumerate(spec.buttons):
            key = f"button-{index}"
            actions.append((key, label))
            targets[key] = (action, target)

        def invoked(action_key: str) -> None:
            action, target = targets.get(action_key, ("", ""))
            if action == "clock-open":
                service = self.service_ref()
                if service is not None and service.ringing:
                    # Opening Clock from a ringing alarm answers it, as the window does.
                    service.stop(service.ringing.split("-", 1)[1])
                launch_app(APP_ID, ["prairie-clock"])
            elif action:
                self.application.activate_action(action, target)
        self.notifier.notify(identifier, spec.title, spec.body, actions=actions, on_action=invoked,
                             category=spec.category or ("x-luma.clock.timer" if identifier.startswith("timer-")
                                                        else "x-luma.clock.alarm"),
                             urgency=URGENCY.get(spec.priority, 1), resident=spec.priority == "urgent",
                             timeout=0 if spec.priority == "urgent" else -1,
                             public_summary="Clock", public_body="Timer done" if identifier.startswith("timer-")
                             else "Alarm")

    def withdraw(self, identifier: str) -> None:
        self.notifier.withdraw(identifier)


class ClockAgent:
    def __init__(self) -> None:
        self.service: AlarmService | None = None
        self.application: AgentApplication | None = None
        self.notifier: Notifier | None = None
        self._monitor: Gio.FileMonitor | None = None
        self._refresh = 0
        self.host_interface = None

    def start(self, agent: Agent) -> None:
        self.application = AgentApplication(agent)
        self.notifier = Notifier(agent.connection, APP_ID, "Clock")
        notifications = AgentNotifications(self.notifier, self.application, lambda: self.service)
        self.service = AlarmService(self.application, notifications=notifications, agent=agent, sandboxed=False)
        self.service.start()
        from .clock_host import HostInterface
        self.host_interface = HostInterface(agent.connection, self.service)
        # A Clock window saves alarms and timers to the store; follow it.
        directory = self.service.store.path.parent
        self._monitor = Gio.File.new_for_path(str(directory)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._monitor.connect("changed", self._store_changed)
        self.application.idle_check()

    def _store_changed(self, _monitor, file, other, _event) -> None:
        names = {item.get_basename() for item in (file, other) if item is not None}
        if self.service is None or self.service.store.path.name not in names:
            return
        if not self._refresh:
            self._refresh = GLib.timeout_add(300, self._reschedule)

    def _reschedule(self) -> bool:
        self._refresh = 0
        if self.service is not None:
            self.service.reschedule()
            if self.host_interface is not None: self.host_interface.changed()
            self.application.idle_check()
        return GLib.SOURCE_REMOVE

    def wake(self, reason: str) -> None:
        if self.service is not None:
            self.service._agent_wake(reason)

    def stop(self) -> None:
        if self.host_interface is not None: self.host_interface.close()
        if self._monitor is not None:
            self._monitor.cancel()
        if self._refresh:
            GLib.source_remove(self._refresh)
        if self.service is not None:
            self.service.shutdown()
        if self.notifier is not None:
            self.notifier.close()


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    clock = ClockAgent()
    return Agent(INFO, start=clock.start, wake=clock.wake, stop=clock.stop).run(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
