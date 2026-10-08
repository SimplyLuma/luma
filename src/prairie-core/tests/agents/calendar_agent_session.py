#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Calendar's reminders agent in a real user session with Evolution Data Server (ADR-033).

    calendar_agent_session.py --evidence DIR

With a local EDS calendar and events carrying reminders: the agent starts at
login inside its unit and evolution-alarm-notify is held off; a reminder shows
with Open, Snooze and Dismiss and the event's time and place; Snooze brings it
back; Dismiss withdraws it; a reminder that falls due during a simulated
suspend shows on resume; one that falls due while logged out shows at the next
login and is not shown twice after a restart; Open starts Calendar on the
event; one agent however often it is started; memory within limits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session import Failure, Session, mib  # noqa: E402

AGENT = "org.projectluma.Calendar.Agent"
APP = "org.projectluma.Calendar"
UNIT = "app-org.projectluma.Calendar-agent.service"  # replaced in main() for the unmanaged fallback


def agent_pid(session: Session) -> int:
    pids = [pid for pid in session.processes("prairie-calendar --agent") if "python" in session.cmdline(pid)]
    return pids[0] if len(pids) == 1 else 0


def event(calendar: str, summary: str, *, starts_in: float, alert_minutes: int, location: str = "") -> str:
    from prairie_apps import calendar_backend as backend
    start = (datetime.now().astimezone() + timedelta(seconds=starts_in)).replace(microsecond=0)
    return backend.save_event(backend.EventDraft(
        source_uid=calendar, summary=summary, start=start, end=start + timedelta(minutes=30),
        tzid=backend.local_tzid(), alerts=(alert_minutes,), location=location))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args()
    session = Session(options.evidence)
    extra: dict = {"managed": session.managed}
    global UNIT
    UNIT = session.agent_unit(APP)
    try:
        from prairie_apps import calendar_backend as backend
        session.systemctl("stop", UNIT, check=False)
        session.systemctl("set-environment", "LUMA_CALENDAR_SNOOZE_MINUTES=1")
        for source in backend.list_sources():
            if source.name.startswith(("Agent test", "Probe")):
                backend.remove_calendar(source.uid)
        for key in ("reminders-past", "reminders-snoozed"):
            session.run("gsettings", "reset", "org.gnome.evolution-data-server.calendar", key, check=False)
        (Path.home() / ".local/state/prairie/calendar/reminders-shown.json").unlink(missing_ok=True)
        calendar = backend.create_calendar(f"Agent test {int(time.time())}")
        standup = event(calendar, "Stand-up", starts_in=165, alert_minutes=2, location="Room 2")
        session.note(f"local calendar {calendar} with an event whose reminder falls due in ~45 s")

        # 1. Login: the agent starts in its unit; evolution-alarm-notify does not.
        session.login()
        session.wait(lambda: session.unit_active(UNIT) and session.has_owner(AGENT), "agent after login", 40)
        pid = session.wait(lambda: agent_pid(session), "one agent process")
        session.step("starts at login", True, unit=UNIT, pid=pid)
        session.limits_step(UNIT, "calendar")
        session.step("loads no GTK", not session.loads_gtk(pid), cmdline=session.cmdline(pid))
        session.systemctl("start", "evolution-alarm-notify.service", check=False)
        condition = session.unit_property("evolution-alarm-notify.service", "ConditionResult")
        session.step("evolution-alarm-notify is held off", condition == "no"
                     and not session.processes("evolution-alarm-notify"),
                     condition=condition, drop_ins=session.unit_property("evolution-alarm-notify.service", "DropInPaths"))

        # 2. The reminder shows.
        record = session.wait_notification(lambda r: r["summary"] == "Stand-up", "Stand-up reminder", 120)
        actions = dict(zip(record["actions"][0::2], record["actions"][1::2]))
        session.step("reminder shows with the event's time and place", "Room 2" in record["body"]
                     and record["body"].startswith("In ") and {"default", "snooze", "dismiss"} <= set(actions)
                     and record["hints"].get("desktop-entry") == "org.projectluma.Calendar"
                     and record["hints"].get("resident") is True,
                     body=record["body"], actions=actions)
        published = session.wait(lambda: session.values(AGENT).get("active-reminders"), "active reminders published", 10)
        session.step("publishes active reminders", published == 1, values=session.values(AGENT))

        # 3. Snooze brings it back a minute later.
        mark = session.mark()
        session.invoke(record["id"], "snooze")
        session.wait(lambda: session.values(AGENT).get("snoozed-reminders") == 1, "snoozed", 10)
        again = session.wait_notification(lambda r: r["summary"] == "Stand-up", "snoozed reminder returns", 110, mark)
        session.step("snooze brings the reminder back", again["hints"].get("x-luma-snoozed") is True, body=again["body"])

        # 4. Dismiss withdraws it.
        session.invoke(again["id"], "dismiss")
        session.wait(lambda: [r for r in session.notification_records() if r["event"] == "close" and r["id"] == again["id"]],
                     "dismissed reminder withdrawn", 10)
        session.wait(lambda: session.values(AGENT).get("active-reminders") == 0, "no active reminders", 10)
        session.step("dismiss withdraws it", True)

        # 5. A reminder that falls due during a suspend shows on resume.
        event(calendar, "Dentist", starts_in=150, alert_minutes=2)
        session.suspend()
        session.systemctl("freeze", UNIT)
        time.sleep(60)
        mark = session.mark()
        resumed_at = time.time()
        session.systemctl("thaw", UNIT)
        session.resume()
        caught = session.wait_notification(lambda r: r["summary"] == "Dentist", "reminder due during suspend", 60, mark)
        session.step("catches up after a simulated suspend", True, seconds_after_resume=round(time.time() - resumed_at, 1),
                     body=caught["body"])
        session.restore_logind()

        # 6. Due while logged out: shown at the next login, and only once.
        session.logout()
        session.wait(lambda: not session.unit_active(UNIT), "agent stopped at logout", 20)
        event(calendar, "Call with Sam", starts_in=110, alert_minutes=2)
        time.sleep(15)
        mark = session.mark()
        session.login()
        session.wait_notification(lambda r: r["summary"] == "Call with Sam", "reminder due while logged out", 60, mark)
        session.step("reminder due while logged out shows at login", True)
        mark = session.mark()
        if session.managed:
            session.systemctl("restart", UNIT)
        else:
            session.run("pkill", "-f", "[p]rairie-calendar --agent", check=False)
            session.wait(lambda: not session.has_owner(AGENT), "agent stopped", 20)
            session.run("systemd-run", "--user", "--quiet", "--collect", "--unit=luma-test-calendar-restart",
                        "prairie-calendar", "--agent")
        session.wait(lambda: session.has_owner(AGENT), "restarted", 30)
        time.sleep(10)
        repeats = [r for r in session.notification_records()[mark:] if r.get("event") == "notify"
                   and r["summary"] in {"Call with Sam", "Dentist", "Stand-up"}]
        session.step("a restart does not show reminders again", not repeats, repeats=len(repeats))

        # 7. Open starts Calendar on the event.
        last = [r for r in session.notification_records() if r.get("event") == "notify" and r["summary"] == "Call with Sam"][-1]
        agent_now = session.owner(AGENT)
        if last["sender"] == agent_now:
            session.invoke(last["id"], "default")
            opened = session.wait(lambda: session.processes("--event-uid="), "Calendar started with the event", 20)
            session.step("Open starts Calendar on that event", True, command=session.cmdline(opened[0]))
        else:
            # The notification belongs to the agent before the restart; show one from this agent.
            event(calendar, "Review", starts_in=125, alert_minutes=2)
            fresh = session.wait_notification(lambda r: r["summary"] == "Review", "reminder for Open", 90)
            session.invoke(fresh["id"], "default")
            opened = session.wait(lambda: session.processes("--event-uid="), "Calendar started with the event", 20)
            session.step("Open starts Calendar on that event", True, command=session.cmdline(opened[0]))
        session.wait(lambda: session.has_owner("org.projectluma.Calendar"), "Calendar window running", 40)
        window = [p for p in session.processes("prairie-calendar") if "--agent" not in session.cmdline(p)]
        cgroups = {p: Path(f"/proc/{p}/cgroup").read_text().strip().rsplit("/", 1)[-1] for p in window}
        session.step("Calendar window opened from the reminder, in its own scope, not the agent's unit",
                     bool(window) and not set(window) & set(session.unit_pids(UNIT))
                     and all(group.startswith("app-luma-org.projectluma.Calendar-") for group in cgroups.values()),
                     cgroups=cgroups)
        session.run("pkill", "-f", "[p]rairie-calendar --event-uid", check=False)

        # 8. Single instance.
        pid = agent_pid(session)
        for arguments in (("--agent",), ("--agent", "--autostart")):
            for _ in range(3):
                session.run("prairie-calendar", *arguments, timeout=30, check=False)
        session.step("single instance", agent_pid(session) == pid)

        # 9. Memory at rest.
        time.sleep(10)
        memory = session.memory(UNIT)
        extra["smaps"] = session.smaps(pid)
        extra["memory"] = {"agent_rss_mib": mib(session.rss(pid)), "unit_current_mib": mib(memory["current"]),
                           "unit_peak_mib": mib(memory["peak"])}
        session.step("memory at rest within limits", (memory["peak"] or 0) < 128 * 1048576
                     and session.rss(pid) < 64 * 1048576, **extra["memory"])
        backend.remove_calendar(calendar)
    except Exception as failure:
        if not isinstance(failure, Failure) or not session.results or session.results[-1]["ok"]:
            session.failed(failure)
    finally:
        session.restore_logind()
        session.systemctl("thaw", UNIT, check=False)
        session.systemctl("stop", "luma-test-calendar-restart", check=False)
        session.systemctl("unset-environment", "LUMA_CALENDAR_SNOOZE_MINUTES", check=False)
    summary = session.summary("calendar", extra)
    print(json.dumps({"passed": summary["passed"], **extra}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
