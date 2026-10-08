#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Clock as an ADR-033 alarms agent in a real user session.

    clock_agent_session.py --evidence DIR

Clock's agent is the Clock process in service mode (see clock_alarms.py). With
a timer and a one-time alarm on record: `prairie-clock --agent` starts without
a window, owns org.projectluma.Clock.Agent and publishes the next event, and
writes its autostart entry (the portal here has no Background backend, so it
takes the host fallback); after a logout the next login starts it from that
entry; the timer notifies with no window; an alarm that falls due during a
simulated suspend rings on resume; Stop ends it, Clock exits with nothing left
and gives up its agent name and autostart entry; a second start adds no
process; memory is recorded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session import Failure, Session, mib  # noqa: E402

from gi.repository import GLib  # noqa: E402

APP = "org.projectluma.Clock"
AGENT = "org.projectluma.Clock.Agent"
# luma-background's system helper; the container grants the test user its polkit action.
WAKE_HELPER = Path("/usr/libexec/luma-background-wake")


def clock_sleep_holds(session: Session) -> list | None:
    """Clock's block inhibitors for sleep and idle, as systemd-logind lists them."""
    listing = session.run("busctl", "--system", "--json=short", "call", "org.freedesktop.login1",
                          "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "ListInhibitors", check=False)
    if listing.returncode != 0:
        return None
    rows = json.loads(listing.stdout)["data"][0]
    return [row for row in rows if row[1] == "Clock" and row[0] == "sleep:idle" and row[3] == "block"] or None


def wake_timers(session: Session) -> list[str]:
    listing = session.run("systemctl", "list-units", "--all", "--plain", "--no-legend", "--type=timer",
                          f"luma-wake-u{os.getuid()}-*", check=False).stdout
    return [line.split()[0] for line in listing.splitlines() if line.strip()]


def alarm_actions(session: Session, notification_id: int) -> list[str]:
    record = next(r for r in session.notification_records() if r["event"] == "notify" and r["id"] == notification_id)
    return record["actions"]


def clock_pids(session: Session) -> list[int]:
    return [pid for pid in session.processes("prairie-clock") if "python" in session.cmdline(pid)
            and "--event" not in session.cmdline(pid)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args()
    session = Session(options.evidence)
    extra: dict = {"managed": session.managed}
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "prairie/clock"
    autostart = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart/org.projectluma.Clock.desktop"
    try:
        session.run("pkill", "-f", "[p]rairie-clock", check=False)
        autostart.unlink(missing_ok=True)
        now = datetime.now().astimezone()
        ring = (now + timedelta(seconds=150)).replace(second=0, microsecond=0) + timedelta(minutes=1)
        timer_at = int(time.time() + 45)
        alarm_uid, timer_uid = uuid.uuid4().hex, uuid.uuid4().hex
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        (data / "state.json").write_text(json.dumps({
            "world": [],
            "alarms": [{"uid": alarm_uid, "label": "Wake up", "hour": ring.hour, "minute": ring.minute, "days": [],
                        "enabled": True, "sound": "Chime", "snooze_minutes": 9, "ring_seconds": 60}],
            "timers": [{"uid": timer_uid, "label": "Tea", "fires_at": timer_at, "total_seconds": 45}]}))
        (data / "scheduler.json").unlink(missing_ok=True)
        session.note(f"timer at {datetime.fromtimestamp(timer_at):%H:%M:%S}, one-time alarm at {ring:%H:%M}")

        unit = "app-org.projectluma.Clock-agent.service" if session.managed else "app-org.projectluma.Clock@autostart.service"
        if not session.managed:
            # Without luma-background: Clock itself (service mode, no window) holds
            # the alarms and writes its autostart entry, as after using Clock once.
            session.login()
            session.run("systemd-run", "--user", "--collect", "--unit=luma-test-clock-first", "prairie-clock",
                        "--gapplication-service")
            session.wait(lambda: session.has_owner(APP), "Clock in service mode", 40)
            entry = session.wait(lambda: autostart.is_file() and autostart.read_text(), "autostart entry", 30)
            exec_line = next(line.split("=", 1)[1] for line in entry.splitlines() if line.startswith("Exec="))
            session.step("without luma-background Clock writes an autostart entry for its service mode",
                         exec_line.endswith("--gapplication-service"), exec=exec_line)
            session.systemctl("stop", "luma-test-clock-first", check=False)
            session.wait(lambda: not session.has_owner(APP), "Clock stopped", 20)
            session.login()
            session.wait(lambda: session.has_owner(APP), "Clock after login", 40)
            time.sleep(3)
            pids = clock_pids(session)
            session.step("login starts Clock from its autostart entry, one process",
                         len(pids) == 1 and pids[0] in session.unit_pids(unit), pids=pids, unit=unit)
            owner_name = APP
        else:
            # With luma-background: the service starts the windowless agent from Clock's declaration.
            session.login()
            session.wait(lambda: session.has_owner(AGENT), "Clock agent after login", 40)
            time.sleep(3)
            pids = clock_pids(session)
            details = session.values(AGENT)
            session.step("login starts Clock's windowless agent, publishing the next event",
                         len(pids) == 1 and pids[0] in session.unit_pids(unit) and not session.loads_gtk(pids[0])
                         and details.get("scheduled") == 2 and details.get("next-kind") == "timer"
                         and details.get("next-at") == timer_at and not session.has_owner(APP),
                         pids=pids, unit=unit, values=details)
            session.limits_step(unit, "alarms")
            owner_name = AGENT
        pid = pids[0]

        # 3. Single instance.
        # Managed, a second agent exits at once. Without the service the agent is
        # not what runs (a developer can still start one by hand), so the second
        # start is Clock's own service mode reaching the running Clock.
        second_starts = (("--agent",), ("--agent", "--autostart")) if session.managed else (("--gapplication-service",),)
        for arguments in second_starts:
            session.run("prairie-clock", *arguments, timeout=30, check=False)
        if session.managed:
            # A Clock started now (a window's service mode) leaves the alarms to the
            # agent: it holds nothing and exits on its own.
            session.run("systemd-run", "--user", "--quiet", "--collect", "--unit=luma-test-clock-service",
                        "prairie-clock", "--gapplication-service")
            session.wait(lambda: len(clock_pids(session)) == 2, "a second Clock started", 20)
        session.wait(lambda: clock_pids(session) == [pid], "only the agent left", 40)
        session.step("single instance: a second agent exits, a Clock in service mode defers to the agent",
                     clock_pids(session) == [pid], pids=clock_pids(session))

        # 4. The timer notifies with no window.
        def notification(identifier: str):
            for record in session.notification_records():
                if record["event"] == "gtk-add" and record["id"] == identifier:
                    return {"title": record["notification"].get("title"), "body": record["notification"].get("body"),
                            "priority": record["notification"].get("priority"),
                            "buttons": [b.get("label") for b in record["notification"].get("buttons", [])]}
                if record["event"] == "notify" and record["hints"].get("desktop-entry") == APP \
                        and record["summary"] and identifier.split("-")[0] in {"timer", "alarm"}:
                    kind = "timer" if record["summary"] == "Timer done" else "alarm"
                    if kind == identifier.split("-")[0] and not record["locked"]:
                        return {"title": record["summary"], "body": record["body"], "id": record["id"],
                                "priority": "urgent" if record["hints"].get("urgency") == 2 else "high",
                                "buttons": record["actions"][1::2]}
            return None

        timer = session.wait(lambda: notification(f"timer-{timer_uid}"), "timer notification", 90)
        session.step("timer notification with no window", timer["title"] == "Timer done",
                     late=round(time.time() - timer_at, 1), **timer)

        # 4b. With the timer done the alarm is next: the computer is to wake a minute before it.
        if WAKE_HELPER.exists():
            wake_at = int(ring.timestamp()) - 60
            expected = f"luma-wake-u{os.getuid()}-{APP}-{wake_at}.timer"
            armed = session.wait(lambda: expected in wake_timers(session), "wake-up armed for the alarm", 30)
            waking = session.run("systemctl", "show", "-P", "WakeSystem", expected, check=False).stdout.strip()
            session.step("the computer is set to wake a minute before the alarm (WakeSystem timer)",
                         bool(armed) and waking == "yes", timers=wake_timers(session), wake_system=waking,
                         calendar=session.run("systemctl", "show", "-P", "TimersCalendar", expected,
                                              check=False).stdout.strip())
        else:
            session.note("luma-background-wake is not installed: waking the computer is not exercised")

        # 5. The alarm falls due during a simulated suspend and rings on resume.
        wait = ring.timestamp() - time.time()
        session.wait(lambda: time.time() > ring.timestamp() - 20, "shortly before the alarm", max(30, wait))
        session.suspend()
        session.systemctl("freeze", unit)
        session.wait(lambda: time.time() > ring.timestamp() + 30, "the alarm time passes while frozen", 80)
        resumed = time.time()
        session.systemctl("thaw", unit)
        session.resume()
        alarm = session.wait(lambda: notification(f"alarm-{alarm_uid}"), "alarm notification after resume", 60)
        session.step("alarm due during suspend rings on resume", alarm["priority"] == "urgent"
                     and {"Stop", "Snooze 9 min"} <= set(alarm["buttons"]), seconds_after_resume=round(time.time() - resumed, 1), **alarm)
        session.restore_logind()
        if session.managed:
            ringing = session.wait(lambda: session.values(AGENT).get("ringing") is True or None, "ringing published", 10)
            session.step("publishes that an alarm is ringing", bool(ringing))
        # It started ringing while logind was held off for the simulated suspend,
        # so the hold is taken when logind returns.
        holds = session.wait(lambda: clock_sleep_holds(session), "Clock's sleep hold once logind is back", 30)
        session.step("while it rings Clock holds sleep and idle off with logind, taken when logind returns",
                     bool(holds), inhibitors=holds)
        memory = session.memory(unit)
        extra["memory"] = {"clock_rss_ringing_mib": mib(session.rss(pid)), "unit_current_mib": mib(memory["current"]),
                           "unit_peak_mib": mib(memory["peak"])}
        extra["smaps"] = session.smaps(pid)

        # 6. Stop: withdrawn; nothing left, so Clock exits and gives up its name.
        if session.managed:
            stop_key = next(key for key, label in zip(alarm_actions(session, alarm["id"])[0::2],
                                                      alarm_actions(session, alarm["id"])[1::2]) if label == "Stop")
            session.invoke(alarm["id"], stop_key)
            session.wait(lambda: any(r["event"] == "close" and r["id"] == alarm["id"]
                                     for r in session.notification_records()), "alarm withdrawn", 20)
        else:
            session.call(APP, "/org/projectluma/Clock", "org.freedesktop.Application", "ActivateAction",
                         GLib.Variant("(sava{sv})", ("clock-stop", [GLib.Variant("s", alarm_uid)], {})))
            session.wait(lambda: any(r["event"] == "gtk-remove" and r["id"] == f"alarm-{alarm_uid}"
                                     for r in session.notification_records()), "alarm withdrawn", 20)
        session.step("memory while ringing within the alarms limit", (memory["peak"] or 0) < 128 * 1048576
                     or not session.managed, **extra["memory"])
        released = session.wait(lambda: not clock_sleep_holds(session), "Clock's sleep hold released", 20)
        session.step("Stop releases the sleep hold", bool(released))
        session.wait(lambda: not session.has_owner(owner_name), "Clock exits with nothing left to ring", 60)
        session.step("Stop withdraws the alarm; Clock exits cleanly and its agent name goes", not session.has_owner(AGENT)
                     and (session.managed or not autostart.exists()),
                     result=session.unit_property(unit, "Result"), autostart_entry=autostart.exists())
        if WAKE_HELPER.exists():
            left = session.wait(lambda: not wake_timers(session) or None, "wake-up cleared", 20)
            session.step("with nothing scheduled the wake-up is cleared", bool(left), timers=wake_timers(session))
    except Exception as failure:
        if not isinstance(failure, Failure) or not session.results or session.results[-1]["ok"]:
            session.failed(failure)
    finally:
        session.restore_logind()
        session.systemctl("thaw", "app-org.projectluma.Clock@autostart.service", "app-org.projectluma.Clock-agent.service",
                          check=False)
        session.run("pkill", "-f", "[p]rairie-clock", check=False)
    summary = session.summary("clock", extra)
    print(json.dumps({"passed": summary["passed"], **extra}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
